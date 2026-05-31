"""収支管理・検証 — 購入記録とAI成績検証"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from ai_recommend import VERDICT_SKIP
from analyze import winning_combinations
from db import db_session, get_connection

BET_TABLE = """
CREATE TABLE IF NOT EXISTS bet_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id         TEXT NOT NULL,
    race_date       TEXT,
    venue_name      TEXT,
    race_no         INTEGER,
    bet_type        TEXT NOT NULL,
    combination     TEXT NOT NULL,
    bet_amount      INTEGER NOT NULL DEFAULT 100,
    is_virtual      INTEGER NOT NULL DEFAULT 0,
    ai_score        REAL,
    ev_rank         TEXT,
    pick_rank       INTEGER,
    verdict         TEXT,
    odds            REAL,
    hit             INTEGER,
    payout          INTEGER DEFAULT 0,
    profit          INTEGER DEFAULT 0,
    recovery_rate   REAL,
    status          TEXT NOT NULL DEFAULT 'pending',
    note            TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    settled_at      TEXT,
    UNIQUE (race_id, bet_type, combination, is_virtual)
);
CREATE INDEX IF NOT EXISTS idx_bet_virtual ON bet_records(is_virtual);
CREATE INDEX IF NOT EXISTS idx_bet_status ON bet_records(status);
"""

SCORE_BANDS = [
    (80, "80+"),
    (65, "65-79"),
    (50, "50-64"),
    (0, "〜49"),
]


def migrate_bet_table(conn) -> None:
    conn.executescript(BET_TABLE)


def _score_band(score: float) -> str:
    for threshold, label in SCORE_BANDS:
        if score >= threshold:
            return label
    return "〜49"


def load_bet_records(
    bet_type: Optional[str] = None,
    *,
    is_virtual: Optional[int] = None,
    status: Optional[str] = None,
) -> pd.DataFrame:
    conn = get_connection()
    migrate_bet_table(conn)
    query = "SELECT * FROM bet_records WHERE 1=1"
    params: list = []
    if bet_type:
        query += " AND bet_type = ?"
        params.append(bet_type)
    if is_virtual is not None:
        query += " AND is_virtual = ?"
        params.append(is_virtual)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id DESC"
    df = pd.read_sql(query, conn, params=params or None)
    conn.close()
    return df


def _race_result(race_id: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT finish_order, trifecta_pay, exacta_pay
        FROM results WHERE race_id = ?
        """,
        (race_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _calc_payout(
    bet_type: str,
    combination: str,
    hit: bool,
    odds: Optional[float],
    bet_amount: int,
    result: dict,
) -> int:
    if not hit:
        return 0
    if bet_type == "3連単" and result.get("trifecta_pay"):
        return int(result["trifecta_pay"])
    if bet_type == "2車単" and result.get("exacta_pay"):
        return int(result["exacta_pay"])
    if odds:
        return int(float(odds) * bet_amount)
    return 0


def add_bet_record(
    *,
    race_id: str,
    bet_type: str,
    combination: str,
    bet_amount: int = 100,
    is_virtual: int = 0,
    race_date: Optional[str] = None,
    venue_name: Optional[str] = None,
    race_no: Optional[int] = None,
    ai_score: Optional[float] = None,
    ev_rank: Optional[str] = None,
    pick_rank: Optional[int] = None,
    verdict: Optional[str] = None,
    odds: Optional[float] = None,
    note: str = "",
) -> dict:
    with db_session() as conn:
        migrate_bet_table(conn)
        try:
            conn.execute(
                """
                INSERT INTO bet_records (
                    race_id, race_date, venue_name, race_no, bet_type, combination,
                    bet_amount, is_virtual, ai_score, ev_rank, pick_rank, verdict,
                    odds, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    race_id,
                    race_date,
                    venue_name,
                    race_no,
                    bet_type,
                    combination,
                    bet_amount,
                    is_virtual,
                    ai_score,
                    ev_rank,
                    pick_rank,
                    verdict,
                    odds,
                    note,
                ),
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}
    settle_pending_bets(bet_type)
    return {"ok": True}


def add_bets_from_cards(
    cards: list[dict],
    bet_type: str,
    bet_amount: int = 100,
    *,
    pick_ranks: tuple[int, ...] = (1,),
    is_virtual: int = 0,
) -> int:
    """AIおすすめカードから購入記録を追加"""
    added = 0
    for card in cards:
        for pick in card.get("picks") or []:
            if pick.get("rank") not in pick_ranks:
                continue
            combo = pick.get("combination")
            if not combo:
                continue
            res = add_bet_record(
                race_id=str(card["race_id"]),
                bet_type=bet_type,
                combination=str(combo),
                bet_amount=bet_amount,
                is_virtual=is_virtual,
                race_date=str(card.get("race_date") or ""),
                venue_name=str(card.get("venue_name") or ""),
                race_no=int(card.get("race_no") or 0),
                ai_score=float(card.get("pre_race_score") or card.get("ai_total_score") or 0),
                ev_rank=str(card.get("ev_rank") or ""),
                pick_rank=int(pick.get("rank") or 0),
                verdict=str(card.get("verdict") or ""),
                odds=float(pick["odds"]) if pick.get("odds") else None,
                note="AIおすすめ",
            )
            if res.get("ok"):
                added += 1
    return added


def sync_virtual_bets(
    recommend: dict,
    bet_type: str,
    bet_amount: int = 100,
) -> int:
    """買わなかったAI候補の仮想購入を同期"""
    actual = load_bet_records(bet_type, is_virtual=0)
    bought = set(
        zip(actual["race_id"].astype(str), actual["combination"].astype(str))
        if not actual.empty
        else []
    )

    added = 0
    for card in recommend.get("all_cards") or []:
        if card.get("verdict") == VERDICT_SKIP:
            continue
        for pick in card.get("picks") or []:
            combo = str(pick.get("combination") or "")
            rid = str(card["race_id"])
            if not combo or (rid, combo) in bought:
                continue
            res = add_bet_record(
                race_id=rid,
                bet_type=bet_type,
                combination=combo,
                bet_amount=bet_amount,
                is_virtual=1,
                race_date=str(card.get("race_date") or ""),
                venue_name=str(card.get("venue_name") or ""),
                race_no=int(card.get("race_no") or 0),
                ai_score=float(card.get("pre_race_score") or card.get("ai_total_score") or 0),
                ev_rank=str(card.get("ev_rank") or ""),
                pick_rank=int(pick.get("rank") or 0),
                verdict=str(card.get("verdict") or ""),
                odds=float(pick["odds"]) if pick.get("odds") else None,
                note="仮想（未購入候補）",
            )
            if res.get("ok"):
                added += 1
    return added


def settle_pending_bets(bet_type: Optional[str] = None) -> int:
    """結果確定済みレースの収支を計算"""
    pending = load_bet_records(bet_type, status="pending")
    if pending.empty:
        return 0

    settled = 0
    with db_session() as conn:
        for _, row in pending.iterrows():
            result = _race_result(str(row["race_id"]))
            if not result:
                continue
            wins = winning_combinations(
                str(row["bet_type"]), str(result["finish_order"])
            )
            hit = str(row["combination"]) in wins
            payout = _calc_payout(
                str(row["bet_type"]),
                str(row["combination"]),
                hit,
                row.get("odds"),
                int(row["bet_amount"]),
                result,
            )
            bet_amount = int(row["bet_amount"])
            profit = payout - bet_amount
            recovery = round(payout / bet_amount * 100, 1) if bet_amount else 0.0

            conn.execute(
                """
                UPDATE bet_records SET
                    hit = ?, payout = ?, profit = ?, recovery_rate = ?,
                    status = 'settled', settled_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (1 if hit else 0, payout, profit, recovery, int(row["id"])),
            )
            settled += 1
    return settled


def _summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "count": 0,
            "total_bet": 0,
            "total_payout": 0,
            "total_profit": 0,
            "recovery_rate": 0.0,
            "hit_rate": 0.0,
            "settled": 0,
            "pending": 0,
        }
    settled = df[df["status"] == "settled"]
    pending_n = int((df["status"] == "pending").sum())
    total_bet = int(settled["bet_amount"].sum()) if not settled.empty else 0
    total_payout = int(settled["payout"].sum()) if not settled.empty else 0
    total_profit = int(settled["profit"].sum()) if not settled.empty else 0
    hits = int(settled["hit"].sum()) if not settled.empty else 0
    n = len(settled)
    return {
        "count": len(df),
        "total_bet": total_bet,
        "total_payout": total_payout,
        "total_profit": total_profit,
        "recovery_rate": round(total_payout / total_bet * 100, 1) if total_bet else 0.0,
        "hit_rate": round(hits / n * 100, 1) if n else 0.0,
        "settled": n,
        "pending": pending_n,
    }


def _group_stats(df: pd.DataFrame, col: str, label_col: str) -> pd.DataFrame:
    settled = df[df["status"] == "settled"].copy()
    if settled.empty:
        return pd.DataFrame()
    if col == "ai_score":
        settled[label_col] = settled["ai_score"].fillna(0).astype(float).map(_score_band)
    else:
        settled[label_col] = settled[col].fillna("不明").astype(str)

    agg = (
        settled.groupby(label_col, dropna=False)
        .agg(
            件数=("id", "count"),
            的中=("hit", "sum"),
            購入額=("bet_amount", "sum"),
            払戻=("payout", "sum"),
            収支=("profit", "sum"),
        )
        .reset_index()
    )
    agg["的中率"] = (agg["的中"] / agg["件数"] * 100).round(1)
    agg["回収率"] = (agg["払戻"] / agg["購入額"].replace(0, pd.NA) * 100).round(1)
    return agg.rename(columns={label_col: "区分"})


def get_pnl_bundle(
    bet_type: str = "3連単",
    recommend: Optional[dict] = None,
    *,
    sync_virtual: bool = False,
) -> dict:
    if sync_virtual and recommend:
        sync_virtual_bets(recommend, bet_type)

    settle_pending_bets(bet_type)

    actual = load_bet_records(bet_type, is_virtual=0)
    virtual = load_bet_records(bet_type, is_virtual=1)

    return {
        "bet_type": bet_type,
        "summary_actual": _summarize(actual),
        "summary_virtual": _summarize(virtual),
        "by_ai_score_actual": _group_stats(actual, "ai_score", "score_band"),
        "by_ai_score_virtual": _group_stats(virtual, "ai_score", "score_band"),
        "by_rank_actual": _group_stats(actual, "ev_rank", "ev_rank"),
        "by_rank_virtual": _group_stats(virtual, "ev_rank", "ev_rank"),
        "history_actual": actual,
        "history_virtual": virtual,
    }


def history_display(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    kind = "仮想" if df["is_virtual"].iloc[0] else "実購入"
    _ = kind
    cols = [
        "created_at",
        "venue_name",
        "race_no",
        "combination",
        "bet_amount",
        "ai_score",
        "ev_rank",
        "odds",
        "hit",
        "payout",
        "profit",
        "recovery_rate",
        "status",
        "verdict",
    ]
    show = df[[c for c in cols if c in df.columns]].copy()
    rename = {
        "created_at": "記録日時",
        "venue_name": "競輪場",
        "race_no": "R",
        "combination": "買い目",
        "bet_amount": "購入",
        "ai_score": "AIスコア",
        "ev_rank": "ランク",
        "odds": "オッズ",
        "hit": "的中",
        "payout": "払戻",
        "profit": "収支",
        "recovery_rate": "回収率",
        "status": "状態",
        "verdict": "判定",
    }
    return show.rename(columns=rename)


def build_pnl_lines(bet_type: str = "3連単") -> list[str]:
    bundle = get_pnl_bundle(bet_type)
    sa = bundle["summary_actual"]
    sv = bundle["summary_virtual"]
    lines = [f"【収支検証】券種={bet_type}", ""]
    lines.append(
        f"  実購入: 収支{sa['total_profit']:,}円 回収{sa['recovery_rate']}% "
        f"的中{sa['hit_rate']}% ({sa['settled']}件)"
    )
    lines.append(
        f"  仮想: 収支{sv['total_profit']:,}円 回収{sv['recovery_rate']}% "
        f"的中{sv['hit_rate']}% ({sv['settled']}件)"
    )
    lines.append("")
    return lines
