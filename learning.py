"""過去結果から勝ちパターンを学習し AI スコアへ反映"""

from datetime import datetime
from typing import Optional

import pandas as pd

from analyze import (
    _popularity_label,
    analyze_by_popularity,
    analyze_by_style,
    analyze_by_style_in_race,
    analyze_by_venue,
    load_bet_frame,
)
from db import db_session, get_connection
from race_features import build_race_metrics, recovery_by_feature

MIN_RACES_FOR_LEARN = 1
HIGH_RECOVERY = 100.0
LOW_RECOVERY = 75.0

LEARN_TABLE = """
CREATE TABLE IF NOT EXISTS learned_patterns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bet_type        TEXT NOT NULL,
    category        TEXT NOT NULL,
    condition_key   TEXT NOT NULL,
    condition_label TEXT NOT NULL,
    races           INTEGER NOT NULL,
    recovery_rate   REAL NOT NULL,
    hit_rate        REAL NOT NULL,
    score_adjust    REAL NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (bet_type, category, condition_key)
);
CREATE INDEX IF NOT EXISTS idx_learned_bet ON learned_patterns(bet_type);
"""


def migrate_learning_table(conn) -> None:
    conn.executescript(LEARN_TABLE)


def recovery_to_adjust(recovery_rate: float) -> float:
    """回収率から AI スコア加点/減点（-10〜+10）"""
    if recovery_rate >= 120:
        return 10.0
    if recovery_rate >= 100:
        return 6.0
    if recovery_rate >= 90:
        return 3.0
    if recovery_rate <= 40:
        return -10.0
    if recovery_rate <= 55:
        return -6.0
    if recovery_rate <= LOW_RECOVERY:
        return -3.0
    return 0.0


def _pattern_row(
    bet_type: str,
    category: str,
    key: str,
    label: str,
    races: int,
    recovery_rate: float,
    hit_rate: float,
) -> dict:
    return {
        "bet_type": bet_type,
        "category": category,
        "condition_key": str(key),
        "condition_label": label,
        "races": int(races),
        "recovery_rate": round(float(recovery_rate), 1),
        "hit_rate": round(float(hit_rate), 1),
        "score_adjust": recovery_to_adjust(float(recovery_rate)),
    }


def compute_learning_patterns(bet_type: str = "3連単") -> pd.DataFrame:
    """過去データから条件別回収率を学習"""
    df = load_bet_frame(bet_type=bet_type)
    if df.empty:
        return pd.DataFrame()

    patterns: list[dict] = []

    venue_df = analyze_by_venue(bet_type)
    for row in venue_df.itertuples():
        if row.races < MIN_RACES_FOR_LEARN:
            continue
        patterns.append(
            _pattern_row(
                bet_type,
                "venue",
                row.venue_name,
                f"競輪場:{row.venue_name}",
                row.races,
                row.recovery_rate,
                row.hit_rate,
            )
        )

    style_df = analyze_by_style(bet_type)
    for row in style_df.itertuples():
        if row.races < MIN_RACES_FOR_LEARN:
            continue
        patterns.append(
            _pattern_row(
                bet_type,
                "style",
                row.first_style,
                f"1着脚質:{row.first_style}",
                row.races,
                row.recovery_rate,
                row.hit_rate,
            )
        )

    race_style_df = analyze_by_style_in_race(bet_type)
    for row in race_style_df.itertuples():
        if row.races < MIN_RACES_FOR_LEARN:
            continue
        patterns.append(
            _pattern_row(
                bet_type,
                "race_style",
                row.race_style_tag,
                f"脚質構成:{row.race_style_tag}",
                row.races,
                row.recovery_rate,
                row.hit_rate,
            )
        )

    metrics = build_race_metrics(bet_type)
    line_df = recovery_by_feature(bet_type, "line_count", metrics)
    for row in line_df.itertuples():
        if row.races < MIN_RACES_FOR_LEARN:
            continue
        patterns.append(
            _pattern_row(
                bet_type,
                "line",
                row.feature_bucket,
                f"ライン:{row.feature_bucket}",
                row.races,
                row.recovery_rate,
                row.hit_rate,
            )
        )

    ninki_df = recovery_by_feature(bet_type, "ninki_concentration", metrics)
    for row in ninki_df.itertuples():
        if row.races < MIN_RACES_FOR_LEARN:
            continue
        patterns.append(
            _pattern_row(
                bet_type,
                "ninki",
                row.feature_bucket,
                f"人気集中:{row.feature_bucket}",
                row.races,
                row.recovery_rate,
                row.hit_rate,
            )
        )

    pop_df = analyze_by_popularity(bet_type)
    for row in pop_df.itertuples():
        if row.races < MIN_RACES_FOR_LEARN:
            continue
        patterns.append(
            _pattern_row(
                bet_type,
                "popularity",
                row.popularity_label,
                f"人気帯:{row.popularity_label}",
                row.races,
                row.recovery_rate,
                row.hit_rate,
            )
        )

    if not patterns:
        return pd.DataFrame()
    return pd.DataFrame(patterns)


def save_learned_patterns(bet_type: str = "3連単") -> int:
    """学習結果を DB に保存"""
    patterns = compute_learning_patterns(bet_type)
    with db_session() as conn:
        migrate_learning_table(conn)
        conn.execute("DELETE FROM learned_patterns WHERE bet_type = ?", (bet_type,))
        if patterns.empty:
            return 0
        for _, row in patterns.iterrows():
            conn.execute(
                """
                INSERT INTO learned_patterns (
                    bet_type, category, condition_key, condition_label,
                    races, recovery_rate, hit_rate, score_adjust, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["bet_type"],
                    row["category"],
                    row["condition_key"],
                    row["condition_label"],
                    row["races"],
                    row["recovery_rate"],
                    row["hit_rate"],
                    row["score_adjust"],
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
    from github_persist import format_sync_result, maybe_sync

    sync_result = maybe_sync("learning")
    if not sync_result.get("ok"):
        print(format_sync_result(sync_result))
    return len(patterns)


def load_learned_patterns(bet_type: str = "3連単") -> pd.DataFrame:
    conn = get_connection()
    migrate_learning_table(conn)
    df = pd.read_sql(
        """
        SELECT bet_type, category, condition_key, condition_label,
               races, recovery_rate, hit_rate, score_adjust, updated_at
        FROM learned_patterns
        WHERE bet_type = ?
        ORDER BY recovery_rate DESC
        """,
        conn,
        params=(bet_type,),
    )
    conn.close()
    return df


def _line_bucket(line_count: int) -> str:
    return f"ライン{line_count}本" if line_count else "ライン不明"


def _ninki_bucket(ninki: float) -> str:
    if ninki <= 5:
        return "分散(〜5%)"
    if ninki <= 10:
        return "普通(5〜10%)"
    if ninki <= 15:
        return "集中(10〜15%)"
    return "超集中(15%〜)"


def _race_style_tag(row: pd.Series, entries: pd.DataFrame) -> str:
    race_id = row["race_id"]
    ent = entries[entries["race_id"] == race_id]
    if ent.empty:
        return "不明"
    nige = int((ent["style"] == "逃").sum())
    makuri = int((ent["style"] == "捲").sum())
    if nige >= 2:
        return "逃2名以上"
    if nige == 1 and makuri == 0:
        return "逃1名のみ"
    if nige == 1:
        return "逃1+捲"
    if makuri >= 1:
        return "捲主体"
    return "逃・捲なし"


def _race_popularity_label(row: pd.Series) -> str:
    """本命組み合わせの人気帯（1番人気）"""
    fav_odds = row.get("fav_odds")
    if fav_odds is not None and pd.notna(fav_odds):
        return _popularity_label(1)
    return "不明"


def _dominant_first_style(row: pd.Series, entries: pd.DataFrame) -> str:
    """レースの先行脚質（逃・捲優先）"""
    ent = entries[entries["race_id"] == row["race_id"]]
    if ent.empty:
        return "不明"
    for s in ("逃", "捲", "両", "追", "差"):
        if (ent["style"] == s).any():
            return s
    return str(ent.iloc[0]["style"] or "不明")


def build_pattern_lookup(patterns: pd.DataFrame) -> dict[tuple[str, str], dict]:
    lookup: dict[tuple[str, str], dict] = {}
    if patterns.empty:
        return lookup
    for _, row in patterns.iterrows():
        lookup[(str(row["category"]), str(row["condition_key"]))] = row.to_dict()
    return lookup


def apply_learning_adjustment(
    row: pd.Series,
    patterns: pd.DataFrame,
    entries: pd.DataFrame,
) -> tuple[float, list[str]]:
    """現在レースに学習パターンを照合し加点/減点"""
    if patterns.empty:
        return 0.0, []

    lookup = build_pattern_lookup(patterns)
    tags: list[tuple[str, str, str]] = [
        ("venue", str(row.get("venue_name") or ""), "競輪場"),
        ("line", _line_bucket(int(row.get("line_count") or 0)), "ライン"),
        ("ninki", _ninki_bucket(float(row.get("ninki_concentration") or 0)), "人気集中"),
        ("race_style", _race_style_tag(row, entries), "脚質構成"),
        ("style", _dominant_first_style(row, entries), "脚質"),
        ("popularity", _race_popularity_label(row), "人気帯"),
    ]

    total = 0.0
    reasons: list[str] = []
    for category, key, prefix in tags:
        pat = lookup.get((category, key))
        if not pat or pat["score_adjust"] == 0:
            continue
        adj = float(pat["score_adjust"])
        total += adj
        sign = "+" if adj > 0 else ""
        reasons.append(
            f"{prefix}{key} 回収{pat['recovery_rate']}%→{sign}{adj:.0f}"
        )

    total = max(-12.0, min(12.0, total))
    return round(total, 1), reasons[:5]


def get_learning_bundle(bet_type: str = "3連単", *, refresh: bool = True) -> dict:
    """Streamlit / レポート用"""
    bet_df = load_bet_frame(bet_type=bet_type)
    result_count = bet_df["race_id"].nunique() if not bet_df.empty else 0

    if refresh or load_learned_patterns(bet_type).empty:
        saved = save_learned_patterns(bet_type)
    else:
        saved = len(load_learned_patterns(bet_type))

    patterns = load_learned_patterns(bet_type)
    venue_df = patterns[patterns["category"] == "venue"].sort_values(
        "recovery_rate", ascending=False
    )

    high = patterns[patterns["recovery_rate"] >= HIGH_RECOVERY].head(10)
    low = patterns[patterns["recovery_rate"] <= LOW_RECOVERY].sort_values(
        "recovery_rate"
    ).head(10)

    return {
        "has_data": not patterns.empty,
        "result_races": result_count,
        "learning_count": len(patterns),
        "saved_count": saved,
        "patterns": patterns,
        "high_recovery_top10": high,
        "low_recovery_top10": low,
        "venue_performance": venue_df,
        "updated_at": (
            patterns["updated_at"].iloc[0] if not patterns.empty else ""
        ),
    }


def build_learning_applied_frame(scores: pd.DataFrame) -> pd.DataFrame:
    """AIスコアへ反映された学習ポイント一覧"""
    if scores.empty or "learn_adjust" not in scores.columns:
        return pd.DataFrame()

    df = scores.copy()
    df["learn_adjust"] = pd.to_numeric(df["learn_adjust"], errors="coerce").fillna(0)
    df = df[df["learn_adjust"] != 0]
    if df.empty:
        return pd.DataFrame()

    cols = [
        "venue_name",
        "race_no",
        "race_id",
        "ai_total_score",
        "learn_adjust",
        "learn_reasons",
        "ev_rank",
    ]
    available = [c for c in cols if c in df.columns]
    out = df[available].sort_values("learn_adjust", ascending=False)
    rename = {
        "venue_name": "競輪場",
        "race_no": "R",
        "race_id": "race_id",
        "ai_total_score": "AIスコア",
        "learn_adjust": "学習pt",
        "learn_reasons": "反映理由",
        "ev_rank": "ランク",
    }
    return out.rename(columns={k: v for k, v in rename.items() if k in out.columns})


def learning_applied_summary(scores: pd.DataFrame) -> dict:
    """学習ポイント反映の集計"""
    applied = build_learning_applied_frame(scores)
    if applied.empty:
        return {"applied_races": 0, "plus_races": 0, "minus_races": 0, "avg_adjust": 0.0}
    adj = pd.to_numeric(scores.get("learn_adjust", 0), errors="coerce").fillna(0)
    return {
        "applied_races": int((adj != 0).sum()),
        "plus_races": int((adj > 0).sum()),
        "minus_races": int((adj < 0).sum()),
        "avg_adjust": round(float(adj[adj != 0].mean()), 1) if (adj != 0).any() else 0.0,
    }


def build_learning_lines(bet_type: str = "3連単") -> list[str]:
    bundle = get_learning_bundle(bet_type, refresh=True)
    lines = [f"【学習状況】券種={bet_type}", ""]
    if not bundle["has_data"]:
        lines.append("学習データがありません。結果付きで workflow を実行してください。")
        lines.append("")
        return lines

    lines.append(f"  学習条件数: {bundle['learning_count']}")
    lines.append(f"  結果ありレース: {bundle['result_races']}")
    lines.append(f"  更新: {bundle['updated_at']}")
    lines.append("")

    lines.append("--- 高回収条件 TOP10 ---")
    if bundle["high_recovery_top10"].empty:
        lines.append("  （100%以上なし）")
    else:
        lines.append(
            bundle["high_recovery_top10"][
                ["condition_label", "races", "recovery_rate", "score_adjust"]
            ].to_string(index=False)
        )
    lines.append("")

    lines.append("--- 低回収条件 TOP10 ---")
    if bundle["low_recovery_top10"].empty:
        lines.append("  （75%以下なし）")
    else:
        lines.append(
            bundle["low_recovery_top10"][
                ["condition_label", "races", "recovery_rate", "score_adjust"]
            ].to_string(index=False)
        )
    lines.append("")

    lines.append("--- 競輪場別成績 ---")
    if bundle["venue_performance"].empty:
        lines.append("  （データなし）")
    else:
        lines.append(
            bundle["venue_performance"][
                ["condition_label", "races", "recovery_rate", "hit_rate", "score_adjust"]
            ].to_string(index=False)
        )
    lines.append("")

    lines.append("--- AIスコアへ反映された学習ポイント ---")
    from ai_score import build_race_scores

    applied = build_learning_applied_frame(build_race_scores(bet_type))
    if applied.empty:
        lines.append("  （反映なし）")
    else:
        lines.append(
            applied[
                [c for c in ["競輪場", "R", "AIスコア", "学習pt", "反映理由"] if c in applied.columns]
            ].to_string(index=False)
        )
    lines.append("")
    return lines
