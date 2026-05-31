"""条件別回収率分析（市場の偏り）"""

from typing import Optional

import pandas as pd

from db import get_connection

BET_UNIT = 100

ODDS_BUCKETS = [
    (0, 5, "〜5倍"),
    (5, 10, "5〜10倍"),
    (10, 30, "10〜30倍"),
    (30, 100, "30〜100倍"),
    (100, float("inf"), "100倍〜"),
]

POPULARITY_BUCKETS = [
    (1, 1, "1番人気"),
    (2, 2, "2番人気"),
    (3, 3, "3番人気"),
    (4, 6, "4〜6番人気"),
    (7, 999, "7番人気以下"),
]

SENKO_STYLES = ("逃", "捲")


def _sorted_pair(a: str, b: str) -> str:
    x, y = sorted((a, b), key=int)
    return f"{x}-{y}"


def winning_combinations(bet_type: str, finish_order: str) -> set[str]:
    parts = [p.strip() for p in finish_order.split(",") if p.strip()]
    if len(parts) < 2:
        return set()

    first, second = parts[0], parts[1]
    wins: set[str] = set()

    if bet_type == "2車単":
        wins.add(f"{first}-{second}")
    elif bet_type == "2車複":
        wins.add(_sorted_pair(first, second))
    elif bet_type == "3連単" and len(parts) >= 3:
        wins.add(f"{parts[0]}-{parts[1]}-{parts[2]}")
    elif bet_type == "3連複" and len(parts) >= 3:
        wins.add("-".join(sorted(parts[:3], key=int)))
    elif bet_type == "ワイド" and len(parts) >= 3:
        top3 = parts[:3]
        for i in range(3):
            for j in range(i + 1, 3):
                wins.add(_sorted_pair(top3[i], top3[j]))
    return wins


def first_bracket(combination: str) -> Optional[int]:
    parts = combination.split("-")
    if not parts:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def _payout_yen(row: pd.Series) -> int:
    if not row["hit"]:
        return 0
    if row["bet_type"] == "3連単" and pd.notna(row["trifecta_pay"]):
        return int(row["trifecta_pay"])
    if row["bet_type"] == "2車単" and pd.notna(row["exacta_pay"]):
        return int(row["exacta_pay"])
    return int(row["odds"] * BET_UNIT)


def _odds_label(odds: float) -> str:
    for low, high, label in ODDS_BUCKETS:
        if low <= odds < high:
            return label
    return "不明"


def _popularity_label(rank: int) -> str:
    for low, high, label in POPULARITY_BUCKETS:
        if low <= rank <= high:
            return label
    return "不明"


def load_entries_frame() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        "SELECT race_id, bracket, racer_name, style FROM entries",
        conn,
    )
    conn.close()
    return df


def build_senko1_map(entries: pd.DataFrame) -> pd.DataFrame:
    """先行1車: 逃・捲がちょうど1名のレース"""
    flags = []
    for race_id, group in entries.groupby("race_id"):
        senko_count = group["style"].isin(SENKO_STYLES).sum()
        flags.append(
            {
                "race_id": race_id,
                "senko_count": int(senko_count),
                "senko1": senko_count == 1,
                "senko_brackets": ",".join(
                    str(b)
                    for b, s in zip(group["bracket"], group["style"])
                    if s in SENKO_STYLES
                ),
            }
        )
    return pd.DataFrame(flags)


def enrich_bet_frame(df: pd.DataFrame, entries: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    style_map = entries.set_index(["race_id", "bracket"])["style"]
    senko = build_senko1_map(entries)

    df = df.merge(senko[["race_id", "senko1", "senko_count", "senko_brackets"]], on="race_id", how="left")
    df["senko1_label"] = df["senko1"].map({True: "先行1車", False: "先行複数/なし"})

    df["first_bracket"] = df["combination"].map(first_bracket)
    df["first_style"] = df.apply(
        lambda r: style_map.get((r["race_id"], r["first_bracket"]), ""),
        axis=1,
    )
    df["first_style"] = df["first_style"].replace("", "不明")

    df["ninki_rank"] = df.groupby(["race_id", "bet_type"])["odds"].rank(method="first", ascending=True)
    df["ninki_rank"] = df["ninki_rank"].astype(int)
    df["popularity_label"] = df["ninki_rank"].map(_popularity_label)

    return df


def load_bet_frame(
    bet_type: Optional[str] = None,
    odds_min: Optional[float] = None,
    odds_max: Optional[float] = None,
) -> pd.DataFrame:
    conn = get_connection()
    query = """
        WITH latest AS (
            SELECT race_id, MAX(captured_at) AS ts
            FROM odds
            GROUP BY race_id
        )
        SELECT
            r.race_id,
            r.race_date,
            r.venue_code,
            r.venue_name,
            r.grade,
            r.race_start,
            r.time_slot,
            res.finish_order,
            res.trifecta_pay,
            res.exacta_pay,
            o.bet_type,
            o.combination,
            o.odds
        FROM odds o
        JOIN latest l
          ON o.race_id = l.race_id AND o.captured_at = l.ts
        JOIN results res ON o.race_id = res.race_id
        JOIN races r ON o.race_id = r.race_id
    """
    df = pd.read_sql(query, conn)
    conn.close()

    if bet_type:
        df = df[df["bet_type"] == bet_type]
    if odds_min is not None:
        df = df[df["odds"] >= odds_min]
    if odds_max is not None:
        df = df[df["odds"] < odds_max]
    if df.empty:
        return df

    win_rows = []
    for rid, bt, fo in df[["race_id", "bet_type", "finish_order"]].drop_duplicates().itertuples(index=False):
        for combo in winning_combinations(bt, fo):
            win_rows.append({"race_id": rid, "bet_type": bt, "combination": combo})
    if win_rows:
        hits = pd.DataFrame(win_rows).assign(hit=True)
        df = df.merge(hits, on=["race_id", "bet_type", "combination"], how="left")
        df["hit"] = df["hit"].fillna(False).astype(bool)
    else:
        df["hit"] = False

    df["return_yen"] = 0
    df.loc[df["hit"], "return_yen"] = df.loc[df["hit"]].apply(_payout_yen, axis=1)
    df["bet_yen"] = BET_UNIT
    df["odds_bucket"] = df["odds"].map(_odds_label)

    entries = load_entries_frame()
    return enrich_bet_frame(df, entries)


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    if not group_cols:
        total_bet = int(df["bet_yen"].sum())
        total_return = int(df["return_yen"].sum())
        hits = int(df["hit"].sum())
        bets = len(df)
        return pd.DataFrame(
            [{
                "races": df["race_id"].nunique(),
                "bets": bets,
                "hits": hits,
                "total_bet": total_bet,
                "total_return": total_return,
                "hit_rate": round(hits / bets * 100, 1) if bets else 0,
                "recovery_rate": round(total_return / total_bet * 100, 1) if total_bet else 0,
            }]
        )

    agg = (
        df.groupby(group_cols, dropna=False)
        .agg(
            races=("race_id", "nunique"),
            bets=("bet_yen", "count"),
            hits=("hit", "sum"),
            total_bet=("bet_yen", "sum"),
            total_return=("return_yen", "sum"),
        )
        .reset_index()
    )
    agg["hit_rate"] = (agg["hits"] / agg["bets"] * 100).round(1)
    agg["recovery_rate"] = (agg["total_return"] / agg["total_bet"] * 100).round(1)
    return agg.sort_values("recovery_rate", ascending=False)


def analyze_by_bet_type() -> pd.DataFrame:
    return summarize(load_bet_frame(), ["bet_type"])


def analyze_by_odds_bucket(bet_type: str = "3連単") -> pd.DataFrame:
    return summarize(load_bet_frame(bet_type=bet_type), ["bet_type", "odds_bucket"])


def analyze_by_popularity(bet_type: str = "3連単") -> pd.DataFrame:
    """人気別回収率（レース内オッズ順位）"""
    df = load_bet_frame(bet_type=bet_type)
    return summarize(df, ["bet_type", "popularity_label"])


def analyze_by_venue(bet_type: str = "3連単") -> pd.DataFrame:
    """競輪場別回収率"""
    df = load_bet_frame(bet_type=bet_type)
    return summarize(df, ["venue_name", "bet_type"])


def analyze_by_time_slot(bet_type: str = "3連単") -> pd.DataFrame:
    """時間帯別回収率（モーニング/ナイター等）"""
    df = load_bet_frame(bet_type=bet_type)
    if "time_slot" not in df.columns:
        return pd.DataFrame()
    df["time_slot"] = df["time_slot"].fillna("不明")
    return summarize(df, ["time_slot", "bet_type"])


def analyze_by_style(bet_type: str = "3連単") -> pd.DataFrame:
    """脚質別回収率（3連単の1着目車番の脚質）"""
    df = load_bet_frame(bet_type=bet_type)
    return summarize(df, ["bet_type", "first_style"])


def analyze_by_style_in_race(bet_type: str = "3連単") -> pd.DataFrame:
    """脚質別: レースに含まれる脚質構成（逃脚2名以上 等）"""
    entries = load_entries_frame()
    style_counts = []
    for race_id, group in entries.groupby("race_id"):
        nige = (group["style"] == "逃").sum()
        makuri = (group["style"] == "捲").sum()
        if nige >= 2:
            label = "逃2名以上"
        elif nige == 1 and makuri == 0:
            label = "逃1名のみ"
        elif nige == 1:
            label = "逃1+捲"
        elif makuri >= 1:
            label = "捲主体"
        else:
            label = "逃・捲なし"
        style_counts.append({"race_id": race_id, "race_style_tag": label})

    tags = pd.DataFrame(style_counts)
    df = load_bet_frame(bet_type=bet_type)
    df = df.merge(tags, on="race_id", how="left")
    df["race_style_tag"] = df["race_style_tag"].fillna("不明")
    return summarize(df, ["race_style_tag", "bet_type"])


def analyze_by_senko1(bet_type: str = "3連単") -> pd.DataFrame:
    """先行1車あり/なし別回収率"""
    df = load_bet_frame(bet_type=bet_type)
    return summarize(df, ["bet_type", "senko1_label"])


def list_senko1_races() -> pd.DataFrame:
    """先行1車と判定されたレース一覧"""
    entries = load_entries_frame()
    senko = build_senko1_map(entries)
    conn = get_connection()
    races = pd.read_sql(
        "SELECT race_id, race_date, venue_name, race_no, grade FROM races",
        conn,
    )
    conn.close()
    out = senko.merge(races, on="race_id", how="left")
    out = out[out["senko1"]].sort_values("race_date")
    return out[["race_id", "race_date", "venue_name", "race_no", "senko_brackets", "senko_count"]]


def analyze(
    bet_type: str = "3連単",
    odds_min: Optional[float] = None,
    odds_max: Optional[float] = None,
) -> pd.DataFrame:
    df = load_bet_frame(bet_type=bet_type, odds_min=odds_min, odds_max=odds_max)
    label = bet_type
    if odds_min is not None or odds_max is not None:
        label += f" / オッズ{odds_min or 0}〜{odds_max or '∞'}"
    result = summarize(df, [])
    if not result.empty:
        result.insert(0, "condition", label)
    return result


def print_report(bet_type: str = "3連単") -> None:
    from report import build_analyze_lines

    for line in build_analyze_lines(bet_type):
        print(line)


if __name__ == "__main__":
    import sys

    bt = sys.argv[1] if len(sys.argv) > 1 else "3連単"
    print_report(bt)
