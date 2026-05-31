"""レース特徴量（AI予測強化用指標）"""

import time
from typing import Optional

import pandas as pd
import requests

from analyze import (
    BET_UNIT,
    SENKO_STYLES,
    load_bet_frame,
    load_entries_frame,
    summarize,
    winning_combinations,
)
from config import RACE_API_URL, REQUEST_INTERVAL, REQUEST_TIMEOUT, USER_AGENT
from db import db_session, get_connection

MAN_TICKET_YEN = 10_000
MAN_TICKET_ODDS = 100.0


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def fetch_line_forecast(race_id: str) -> Optional[list[list[str]]]:
    """netkeirin 並び予想 API"""
    resp = requests.get(
        RACE_API_URL,
        params={
            "class": "AplNarabiYoso",
            "method": "get",
            "race_id": race_id,
            "output": "json",
        },
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    time.sleep(REQUEST_INTERVAL)
    body = resp.json()
    if body.get("status") != "OK":
        return None
    key = f"nkrace_line::{race_id}"
    block = body.get("data", {}).get(key, {})
    raw = block.get("lineForecast")
    if not raw:
        return None
    return raw


def parse_line_info(line_forecast: Optional[list]) -> tuple[str, int]:
    """
    並び予想を文字列化。0 はライン区切り。
    例: [["3","1","5","0","4","2"]] → "3-1 | 5-4-2", ライン数2
    """
    if not line_forecast or not line_forecast[0]:
        return "不明", 0

    groups: list[list[str]] = [[]]
    for token in line_forecast[0]:
        t = str(token).strip()
        if t in ("0", ""):
            if groups[-1]:
                groups.append([])
            continue
        if t.isdigit():
            groups[-1].append(t)
    groups = [g for g in groups if g]
    if not groups:
        return "不明", 0

    parts = ["-".join(g) for g in groups]
    return " | ".join(parts), len(parts)


def save_line_info(race_id: str, line_info: str, line_count: int) -> None:
    with db_session() as conn:
        conn.execute(
            """
            UPDATE races SET line_info = ?, line_count = ?
            WHERE race_id = ?
            """,
            (line_info, line_count, race_id),
        )


def update_line_from_api(race_id: str) -> tuple[str, int]:
    raw = fetch_line_forecast(race_id)
    line_info, line_count = parse_line_info(raw)
    save_line_info(race_id, line_info, line_count)
    return line_info, line_count


def _race_odds_snapshot(bet_type: str = "3連単") -> pd.DataFrame:
    conn = get_connection()
    query = """
        WITH latest AS (
            SELECT race_id, MAX(captured_at) AS ts
            FROM odds GROUP BY race_id
        )
        SELECT o.race_id, o.combination, o.odds
        FROM odds o
        JOIN latest l ON o.race_id = l.race_id AND o.captured_at = l.ts
        WHERE o.bet_type = ?
    """
    df = pd.read_sql(query, conn, params=(bet_type,))
    conn.close()
    return df


def build_race_metrics(bet_type: str = "3連単") -> pd.DataFrame:
    """レース単位のAI指標"""
    entries = load_entries_frame()
    conn = get_connection()
    races = pd.read_sql(
        """
        SELECT r.race_id, r.race_date, r.venue_name, r.venue_code,
               r.race_no, r.line_info, r.line_count,
               res.finish_order, res.trifecta_pay, res.exacta_pay
        FROM races r
        LEFT JOIN results res ON r.race_id = res.race_id
        """,
        conn,
    )
    conn.close()

    odds = _race_odds_snapshot(bet_type)
    if races.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    for race_id, rgroup in races.groupby("race_id"):
        race = rgroup.iloc[0]
        ent = entries[entries["race_id"] == race_id]
        nige_count = int((ent["style"] == "逃").sum()) if not ent.empty else 0
        senko_count = int(ent["style"].isin(SENKO_STYLES).sum()) if not ent.empty else 0

        line_info = race.get("line_info") or ""
        line_count = int(race.get("line_count") or 0)
        if not line_info or line_info == "不明" or pd.isna(line_info):
            try:
                line_info, line_count = update_line_from_api(race_id)
            except Exception:
                line_info, line_count = "不明", 0

        ro = odds[odds["race_id"] == race_id]
        fav_combo = ""
        fav_odds = None
        ninki_concentration = 0.0
        are_index = 0.0
        if not ro.empty:
            ro = ro.copy()
            ro["implied"] = 1.0 / ro["odds"]
            total = ro["implied"].sum()
            fav_row = ro.loc[ro["odds"].idxmin()]
            fav_combo = str(fav_row["combination"])
            fav_odds = float(fav_row["odds"])
            ninki_concentration = round(float(fav_row["implied"] / total * 100), 1) if total else 0
            cv = ro["odds"].std() / ro["odds"].mean() if ro["odds"].mean() else 0
            are_index = round(min(100.0, (1 - fav_row["implied"] / total) * 50 + cv * 30), 1)

        finish_order = race.get("finish_order")
        honmei_settle = False
        man_ticket = False
        winner_rank = None
        trifecta_pay = race.get("trifecta_pay")
        win_row = pd.DataFrame()
        if pd.notna(finish_order) and fav_combo and not ro.empty:
            wins = winning_combinations(bet_type, str(finish_order))
            honmei_settle = fav_combo in wins
            if wins:
                win_combo = next(iter(wins))
                win_row = ro[ro["combination"] == win_combo]
                if not win_row.empty:
                    winner_rank = int(
                        ro["odds"].rank(method="first", ascending=True).loc[win_row.index[0]]
                    )
            if pd.notna(trifecta_pay):
                man_ticket = int(trifecta_pay) >= MAN_TICKET_YEN
            elif not win_row.empty:
                win_odds = float(win_row["odds"].iloc[0])
                man_ticket = win_odds >= MAN_TICKET_ODDS or (
                    winner_rank is not None and winner_rank >= 7
                )

        rows.append(
            {
                "race_id": race_id,
                "race_date": race["race_date"],
                "venue_name": race["venue_name"],
                "race_no": race["race_no"],
                "line_info": line_info,
                "line_count": line_count,
                "nige_count": nige_count,
                "senko_count": senko_count,
                "ninki_concentration": ninki_concentration,
                "are_index": are_index,
                "fav_combo": fav_combo,
                "fav_odds": fav_odds,
                "honmei_settle": honmei_settle,
                "man_ticket": man_ticket,
                "winner_ninki_rank": winner_rank,
                "trifecta_pay": int(trifecta_pay) if pd.notna(trifecta_pay) else None,
            }
        )

    return pd.DataFrame(rows)


def venue_trends(metrics: pd.DataFrame) -> pd.DataFrame:
    """競輪場別傾向"""
    if metrics.empty:
        return pd.DataFrame()

    m = metrics.copy()
    m["honmei_settle"] = m["honmei_settle"].astype(int)
    m["man_ticket"] = m["man_ticket"].astype(int)

    agg = (
        m.groupby("venue_name", dropna=False)
        .agg(
            races=("race_id", "count"),
            avg_nige=("nige_count", "mean"),
            avg_line_count=("line_count", "mean"),
            avg_ninki_conc=("ninki_concentration", "mean"),
            avg_are_index=("are_index", "mean"),
            honmei_rate=("honmei_settle", "mean"),
            man_ticket_rate=("man_ticket", "mean"),
            avg_trifecta_pay=("trifecta_pay", "mean"),
        )
        .reset_index()
    )
    agg["honmei_rate"] = (agg["honmei_rate"] * 100).round(1)
    agg["man_ticket_rate"] = (agg["man_ticket_rate"] * 100).round(1)
    agg["avg_nige"] = agg["avg_nige"].round(1)
    agg["avg_line_count"] = agg["avg_line_count"].round(1)
    agg["avg_ninki_conc"] = agg["avg_ninki_conc"].round(1)
    agg["avg_are_index"] = agg["avg_are_index"].round(1)
    agg["avg_trifecta_pay"] = agg["avg_trifecta_pay"].fillna(0).round(0)
    return agg.sort_values("races", ascending=False)


def recovery_by_feature(
    bet_type: str,
    feature_col: str,
    metrics: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """特徴量別回収率"""
    m = metrics if metrics is not None else build_race_metrics(bet_type)
    df = load_bet_frame(bet_type=bet_type)
    if df.empty or m.empty:
        return pd.DataFrame()

    if feature_col == "nige_count":
        m["feature_bucket"] = m["nige_count"].apply(
            lambda x: f"逃げ{x}名" if x <= 3 else f"逃げ{x}名以上"
        )
    elif feature_col == "line_count":
        m["feature_bucket"] = m["line_count"].apply(
            lambda x: f"ライン{x}本" if x else "ライン不明"
        )
    elif feature_col == "are_index":
        m["feature_bucket"] = pd.cut(
            m["are_index"],
            bins=[-1, 30, 50, 70, 100],
            labels=["安定(〜30)", "やや安定(30〜50)", "やや荒れ(50〜70)", "荒れ(70〜)"],
        ).astype(str)
    elif feature_col == "ninki_concentration":
        m["feature_bucket"] = pd.cut(
            m["ninki_concentration"],
            bins=[-1, 5, 10, 15, 100],
            labels=["分散(〜5%)", "普通(5〜10%)", "集中(10〜15%)", "超集中(15%〜)"],
        ).astype(str)
    else:
        m["feature_bucket"] = m[feature_col].astype(str)

    merged = df.merge(
        m[["race_id", "feature_bucket"]],
        on="race_id",
        how="inner",
    )
    return summarize(merged, ["feature_bucket", "bet_type"])


def overall_rates(metrics: pd.DataFrame) -> dict:
    """集計率（全体）"""
    if metrics.empty:
        return {}
    valid = metrics[metrics["trifecta_pay"].notna()]
    n = len(valid) if len(valid) else len(metrics)
    if n == 0:
        return {}
    return {
        "races": n,
        "avg_ninki_concentration": round(metrics["ninki_concentration"].mean(), 1),
        "avg_are_index": round(metrics["are_index"].mean(), 1),
        "honmei_settle_rate": round(metrics["honmei_settle"].sum() / n * 100, 1),
        "man_ticket_rate": round(metrics["man_ticket"].sum() / n * 100, 1),
        "avg_nige_count": round(metrics["nige_count"].mean(), 1),
    }
