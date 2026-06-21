"""本格学習モード — 品質チェック済みデータのみで AI 精度を向上"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from analyze import (
    load_bet_frame,
    load_entries_frame,
    summarize,
)
from data_quality import run_quality_audit
from db import db_session, get_connection
from learning import (
    HIGH_RECOVERY,
    LOW_RECOVERY,
    MIN_RACES_FOR_LEARN,
    _pattern_row,
    recovery_to_adjust,
)
from ml_model import _bet_type_slug, _race_recovery
from config import DATA_DIR
from race_features import build_race_metrics, recovery_by_feature, venue_trends
ADVANCED_MODEL_DIR = DATA_DIR / "models"
ADVANCED_MODEL_DIR.mkdir(parents=True, exist_ok=True)

MIN_VALID_RACES = 5
HIGH_EXTRACT = HIGH_RECOVERY
LOW_EXCLUDE = LOW_RECOVERY

SCORE_COMPONENTS = ("distortion", "ninki", "nige", "venue", "style", "line")
DEFAULT_SCORE_WEIGHTS = {k: 1.0 for k in SCORE_COMPONENTS}

ADVANCED_TABLE = """
CREATE TABLE IF NOT EXISTS advanced_learning_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    bet_type                TEXT NOT NULL,
    started_at              TEXT NOT NULL,
    finished_at             TEXT,
    n_valid_races           INTEGER NOT NULL DEFAULT 0,
    n_patterns              INTEGER NOT NULL DEFAULT 0,
    before_recovery         REAL,
    after_predicted_recovery REAL,
    score_correlation_before REAL,
    score_correlation_after  REAL,
    model_path              TEXT,
    weights_path            TEXT,
    status                  TEXT NOT NULL DEFAULT 'running',
    created_at              TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS advanced_patterns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bet_type        TEXT NOT NULL,
    category        TEXT NOT NULL,
    condition_key   TEXT NOT NULL,
    condition_label TEXT NOT NULL,
    races           INTEGER NOT NULL,
    recovery_rate   REAL NOT NULL,
    hit_rate        REAL NOT NULL,
    score_adjust    REAL NOT NULL,
    excluded        INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (bet_type, category, condition_key)
);

CREATE TABLE IF NOT EXISTS score_weights (
    bet_type    TEXT NOT NULL,
    component   TEXT NOT NULL,
    weight      REAL NOT NULL,
    correlation REAL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (bet_type, component)
);
CREATE INDEX IF NOT EXISTS idx_advanced_patterns_bet ON advanced_patterns(bet_type);
"""


def migrate_advanced_learning_table(conn) -> None:
    conn.executescript(ADVANCED_TABLE)


def advanced_model_path(bet_type: str) -> str:
    return str(ADVANCED_MODEL_DIR / f"advanced_{_bet_type_slug(bet_type)}.json")


def advanced_weights_path(bet_type: str) -> str:
    return str(ADVANCED_MODEL_DIR / f"advanced_weights_{_bet_type_slug(bet_type)}.json")


def get_valid_race_ids(bet_type: str = "3連単") -> set[str]:
    audit = run_quality_audit(bet_type)
    details = audit.get("race_details", pd.DataFrame())
    if details.empty:
        return set()
    return set(details.loc[details["learnable"], "race_id"].astype(str))


def load_valid_bet_frame(bet_type: str = "3連単") -> pd.DataFrame:
    valid = get_valid_race_ids(bet_type)
    df = load_bet_frame(bet_type=bet_type)
    if df.empty or not valid:
        return pd.DataFrame()
    return df[df["race_id"].isin(valid)].copy()


def _filtered_metrics(bet_type: str, valid_ids: set[str]) -> pd.DataFrame:
    metrics = build_race_metrics(bet_type)
    if metrics.empty or not valid_ids:
        return pd.DataFrame()
    return metrics[metrics["race_id"].isin(valid_ids)].copy()


def _style_tags(entries: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for race_id, group in entries.groupby("race_id"):
        nige = int((group["style"] == "逃").sum())
        makuri = int((group["style"] == "捲").sum())
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
        rows.append({"race_id": race_id, "race_style_tag": label})
    return pd.DataFrame(rows)


def compute_advanced_patterns(
    bet_type: str,
    bet_df: pd.DataFrame,
    metrics: pd.DataFrame,
    entries: pd.DataFrame,
) -> pd.DataFrame:
    """有効データのみで特徴量別回収率を学習"""
    if bet_df.empty:
        return pd.DataFrame()

    patterns: list[dict] = []

    venue_df = summarize(bet_df, ["venue_name", "bet_type"])
    for row in venue_df.itertuples():
        if row.races < MIN_RACES_FOR_LEARN:
            continue
        patterns.append(
            _pattern_row(
                bet_type, "venue", row.venue_name, f"競輪場:{row.venue_name}",
                row.races, row.recovery_rate, row.hit_rate,
            )
        )

    if "first_style" in bet_df.columns:
        style_df = summarize(bet_df, ["bet_type", "first_style"])
        for row in style_df.itertuples():
            if row.races < MIN_RACES_FOR_LEARN:
                continue
            patterns.append(
                _pattern_row(
                    bet_type, "style", row.first_style, f"1着脚質:{row.first_style}",
                    row.races, row.recovery_rate, row.hit_rate,
                )
            )

    tags = _style_tags(entries)
    tagged = bet_df.merge(tags, on="race_id", how="left")
    tagged["race_style_tag"] = tagged["race_style_tag"].fillna("不明")
    race_style_df = summarize(tagged, ["race_style_tag", "bet_type"])
    for row in race_style_df.itertuples():
        if row.races < MIN_RACES_FOR_LEARN:
            continue
        patterns.append(
            _pattern_row(
                bet_type, "race_style", row.race_style_tag, f"脚質構成:{row.race_style_tag}",
                row.races, row.recovery_rate, row.hit_rate,
            )
        )

    if "popularity_label" in bet_df.columns:
        pop_df = summarize(bet_df, ["bet_type", "popularity_label"])
        for row in pop_df.itertuples():
            if row.races < MIN_RACES_FOR_LEARN:
                continue
            patterns.append(
                _pattern_row(
                    bet_type, "popularity", row.popularity_label, f"人気帯:{row.popularity_label}",
                    row.races, row.recovery_rate, row.hit_rate,
                )
            )

    for feature_col, category, prefix in (
        ("line_count", "line", "ライン"),
        ("ninki_concentration", "ninki", "人気集中"),
        ("are_index", "are", "荒れ指数"),
        ("nige_count", "nige_feat", "逃げ人数"),
    ):
        feat_df = recovery_by_feature(bet_type, feature_col, metrics)
        if feat_df.empty:
            continue
        for row in feat_df.itertuples():
            if row.races < MIN_RACES_FOR_LEARN:
                continue
            patterns.append(
                _pattern_row(
                    bet_type, category, row.feature_bucket, f"{prefix}:{row.feature_bucket}",
                    row.races, row.recovery_rate, row.hit_rate,
                )
            )

    if not patterns:
        return pd.DataFrame()

    df = pd.DataFrame(patterns)
    df["excluded"] = df["recovery_rate"] <= LOW_EXCLUDE
    low_mask = df["excluded"]
    df.loc[low_mask, "score_adjust"] = df.loc[low_mask, "recovery_rate"].map(recovery_to_adjust)
    high_mask = df["recovery_rate"] >= HIGH_EXTRACT
    df.loc[high_mask, "score_adjust"] = df.loc[high_mask, "recovery_rate"].map(recovery_to_adjust)
    mid_mask = (~low_mask) & (~high_mask)
    df.loc[mid_mask, "score_adjust"] = 0.0
    return df


def _extract_score_components(
    metrics: pd.DataFrame,
    entries: pd.DataFrame,
    bet_type: str,
) -> pd.DataFrame:
    """レースごとの AI スコア構成要素（重み学習用）"""
    from ai_score import _line_head_brackets, _race_anomaly_map, _venue_lookup
    from detect_anomaly import detect_all

    anomalies = detect_all(bet_type)
    anomaly_map = _race_anomaly_map(anomalies, bet_type)
    venues = _venue_lookup(venue_trends(metrics))

    rows: list[dict] = []
    for _, row in metrics.iterrows():
        race_id = row["race_id"]
        ent = entries[entries["race_id"] == race_id]
        anom = anomaly_map.get(race_id, {})
        venue = venues.get(str(row.get("venue_name")), {})

        dist_max = float(anom.get("distortion_max", 0))
        dist_cnt = int(anom.get("distortion_count", 0))
        pts_distortion = min(25, dist_max * 0.4 + dist_cnt * 2)

        ninki = float(row.get("ninki_concentration") or 0)
        if 8 <= ninki <= 14:
            pts_ninki = 12
        elif 5 <= ninki < 8 or 14 < ninki <= 18:
            pts_ninki = 7
        elif ninki > 20:
            pts_ninki = 3
        else:
            pts_ninki = 5

        nige = int(row.get("nige_count") or 0)
        if nige == 1:
            pts_nige = 10
        elif nige == 2:
            pts_nige = 8
        elif nige == 0:
            pts_nige = 4
        else:
            pts_nige = 6

        honmei_v = float(venue.get("honmei_rate", 30))
        man_v = float(venue.get("man_ticket_rate", 20))
        if man_v >= 25 and honmei_v <= 35:
            pts_venue = 14
        elif man_v >= 15:
            pts_venue = 9
        else:
            pts_venue = 5

        senko = int(row.get("senko_count") or 0)
        pts_style = 0
        if senko == 1:
            pts_style += 8
        if not ent.empty:
            nige_br = set(ent.loc[ent["style"] == "逃", "bracket"].astype(int))
            line_heads = _line_head_brackets(str(row.get("line_info") or ""))
            if line_heads & nige_br:
                pts_style += 5
        pts_style = min(13, pts_style)

        line_count = int(row.get("line_count") or 0)
        pts_line = 8 if 2 <= line_count <= 3 else (4 if line_count else 0)

        rows.append(
            {
                "race_id": race_id,
                "distortion": pts_distortion,
                "ninki": pts_ninki,
                "nige": pts_nige,
                "venue": pts_venue,
                "style": pts_style,
                "line": pts_line,
            }
        )
    return pd.DataFrame(rows)


def compute_score_weights(
    components: pd.DataFrame,
    recovery: pd.DataFrame,
) -> tuple[dict[str, float], dict[str, float]]:
    """特徴量と回収率の相関から AI スコア重みを自動調整"""
    merged = components.merge(recovery[["race_id", "recovery_rate"]], on="race_id", how="inner")
    if len(merged) < MIN_VALID_RACES:
        return dict(DEFAULT_SCORE_WEIGHTS), {}

    weights: dict[str, float] = {}
    correlations: dict[str, float] = {}
    for col in SCORE_COMPONENTS:
        if merged[col].std() == 0:
            corr = 0.0
        else:
            corr = float(merged[col].corr(merged["recovery_rate"]))
            if np.isnan(corr):
                corr = 0.0
        correlations[col] = round(corr, 3)
        weights[col] = round(float(np.clip(1.0 + corr * 0.8, 0.5, 2.0)), 3)

    return weights, correlations


def _top_pick_recovery(
    scores: pd.DataFrame,
    bet_df: pd.DataFrame,
    valid_ids: set[str],
) -> float:
    """AI おすすめ1点の実績回収率（バックテスト）"""
    if scores.empty or bet_df.empty:
        return 0.0

    total_bet = 0
    total_return = 0
    race_bets = bet_df.groupby("race_id")

    for _, row in scores.iterrows():
        race_id = row["race_id"]
        if race_id not in valid_ids:
            continue
        combo = row.get("pick1_combo") or row.get("top_pick")
        if not combo:
            continue
        if race_id not in race_bets.groups:
            continue
        sub = race_bets.get_group(race_id)
        hit_row = sub[sub["combination"] == str(combo)]
        total_bet += 100
        if not hit_row.empty and bool(hit_row.iloc[0].get("hit")):
            total_return += int(hit_row.iloc[0].get("return_yen") or 0)

    if total_bet == 0:
        return 0.0
    return round(total_return / total_bet * 100, 1)


def _score_recovery_correlation(
    scores: pd.DataFrame,
    recovery: pd.DataFrame,
    score_col: str = "ai_total_score",
) -> float:
    merged = scores.merge(recovery[["race_id", "recovery_rate"]], on="race_id", how="inner")
    if len(merged) < 3 or merged[score_col].std() == 0:
        return 0.0
    corr = merged[score_col].corr(merged["recovery_rate"])
    return round(float(corr), 3) if not np.isnan(corr) else 0.0


def _feature_importance_from_patterns(patterns: pd.DataFrame) -> pd.DataFrame:
    if patterns.empty:
        return pd.DataFrame()
    imp = (
        patterns.groupby("category")
        .agg(
            patterns=("condition_key", "count"),
            avg_recovery=("recovery_rate", "mean"),
            max_adjust=("score_adjust", lambda x: abs(x).max()),
        )
        .reset_index()
        .rename(columns={"category": "feature"})
    )
    imp["importance"] = (
        imp["max_adjust"] * 0.6 + (imp["avg_recovery"] - 100).abs() * 0.04
    ).round(2)
    return imp.sort_values("importance", ascending=False)


def save_advanced_learning(
    bet_type: str,
    patterns: pd.DataFrame,
    weights: dict[str, float],
    correlations: dict[str, float],
    meta: dict,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        migrate_advanced_learning_table(conn)
        conn.execute("DELETE FROM advanced_patterns WHERE bet_type = ?", (bet_type,))
        if not patterns.empty:
            for _, row in patterns.iterrows():
                conn.execute(
                    """
                    INSERT INTO advanced_patterns (
                        bet_type, category, condition_key, condition_label,
                        races, recovery_rate, hit_rate, score_adjust, excluded, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bet_type,
                        row["category"],
                        row["condition_key"],
                        row["condition_label"],
                        int(row["races"]),
                        float(row["recovery_rate"]),
                        float(row["hit_rate"]),
                        float(row["score_adjust"]),
                        int(bool(row.get("excluded", False))),
                        now,
                    ),
                )
        for comp, weight in weights.items():
            conn.execute(
                """
                INSERT INTO score_weights (bet_type, component, weight, correlation, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(bet_type, component) DO UPDATE SET
                    weight = excluded.weight,
                    correlation = excluded.correlation,
                    updated_at = excluded.updated_at
                """,
                (bet_type, comp, float(weight), correlations.get(comp), now),
            )

    payload = {
        "bet_type": bet_type,
        "trained_at": now,
        "weights": weights,
        "correlations": correlations,
        "meta": meta,
        "n_patterns": len(patterns),
        "high_conditions": patterns[patterns["recovery_rate"] >= HIGH_EXTRACT].head(10).to_dict("records"),
        "low_conditions": patterns[patterns["recovery_rate"] <= LOW_EXCLUDE].sort_values("recovery_rate").head(10).to_dict("records"),
    }
    path = advanced_model_path(bet_type)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(advanced_weights_path(bet_type)).write_text(
        json.dumps({"bet_type": bet_type, "weights": weights, "correlations": correlations}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_advanced_patterns(bet_type: str = "3連単") -> pd.DataFrame:
    conn = get_connection()
    migrate_advanced_learning_table(conn)
    df = pd.read_sql(
        """
        SELECT bet_type, category, condition_key, condition_label,
               races, recovery_rate, hit_rate, score_adjust, excluded, updated_at
        FROM advanced_patterns
        WHERE bet_type = ?
        ORDER BY recovery_rate DESC
        """,
        conn,
        params=(bet_type,),
    )
    conn.close()
    return df


def get_score_weights(bet_type: str = "3連単") -> dict[str, float]:
    conn = get_connection()
    migrate_advanced_learning_table(conn)
    rows = conn.execute(
        "SELECT component, weight FROM score_weights WHERE bet_type = ?",
        (bet_type,),
    ).fetchall()
    conn.close()
    if not rows:
        wpath = advanced_weights_path(bet_type)
        if Path(wpath).exists():
            data = json.loads(Path(wpath).read_text(encoding="utf-8"))
            return {**DEFAULT_SCORE_WEIGHTS, **data.get("weights", {})}
        return dict(DEFAULT_SCORE_WEIGHTS)
    weights = dict(DEFAULT_SCORE_WEIGHTS)
    for row in rows:
        weights[str(row[0])] = float(row[1])
    return weights


def load_advanced_meta(bet_type: str = "3連単") -> dict:
    path = advanced_model_path(bet_type)
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_advanced_learning(bet_type: str = "3連単") -> dict:
    """本格学習パイプライン"""
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    valid_ids = get_valid_race_ids(bet_type)
    bet_df = load_valid_bet_frame(bet_type)
    entries = load_entries_frame()
    metrics = _filtered_metrics(bet_type, valid_ids)

    if len(valid_ids) < MIN_VALID_RACES:
        return {
            "ok": False,
            "error": f"有効レースが不足しています（{len(valid_ids)}/{MIN_VALID_RACES}）",
            "n_valid_races": len(valid_ids),
        }

    patterns = compute_advanced_patterns(bet_type, bet_df, metrics, entries)
    recovery = _race_recovery(bet_df)
    components = _extract_score_components(metrics, entries, bet_type)
    weights, correlations = compute_score_weights(components, recovery)

    from ai_score import build_race_scores_with_options

    before_scores = build_race_scores_with_options(
        bet_type, score_weights=DEFAULT_SCORE_WEIGHTS, patterns=pd.DataFrame()
    )
    before_scores = before_scores[before_scores["race_id"].isin(valid_ids)]
    before_recovery = _top_pick_recovery(before_scores, bet_df, valid_ids)
    corr_before = _score_recovery_correlation(before_scores, recovery)

    after_scores = build_race_scores_with_options(
        bet_type, score_weights=weights, patterns=patterns[~patterns["excluded"]] if not patterns.empty else pd.DataFrame()
    )
    after_scores = after_scores[after_scores["race_id"].isin(valid_ids)]
    after_predicted = _top_pick_recovery(after_scores, bet_df, valid_ids)
    corr_after = _score_recovery_correlation(after_scores, recovery)

    feature_importance = _feature_importance_from_patterns(patterns)
    meta = {
        "n_valid_races": len(valid_ids),
        "before_recovery": before_recovery,
        "after_predicted_recovery": after_predicted,
        "score_correlation_before": corr_before,
        "score_correlation_after": corr_after,
        "valid_race_ids": sorted(valid_ids),
    }
    save_advanced_learning(bet_type, patterns, weights, correlations, meta)

    finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model_path = advanced_model_path(bet_type)

    with db_session() as conn:
        migrate_advanced_learning_table(conn)
        conn.execute(
            """
            INSERT INTO advanced_learning_runs (
                bet_type, started_at, finished_at, n_valid_races, n_patterns,
                before_recovery, after_predicted_recovery,
                score_correlation_before, score_correlation_after,
                model_path, weights_path, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok')
            """,
            (
                bet_type,
                started,
                finished,
                len(valid_ids),
                len(patterns),
                before_recovery,
                after_predicted,
                corr_before,
                corr_after,
                model_path,
                advanced_weights_path(bet_type),
            ),
        )

    high = patterns[patterns["recovery_rate"] >= HIGH_EXTRACT].head(10)
    low = patterns[patterns["recovery_rate"] <= LOW_EXCLUDE].sort_values("recovery_rate").head(10)

    return {
        "ok": True,
        "bet_type": bet_type,
        "n_valid_races": len(valid_ids),
        "n_patterns": len(patterns),
        "before_recovery": before_recovery,
        "after_predicted_recovery": after_predicted,
        "score_correlation_before": corr_before,
        "score_correlation_after": corr_after,
        "weights": weights,
        "correlations": correlations,
        "feature_importance": feature_importance,
        "high_recovery_top10": high,
        "low_recovery_top10": low,
        "excluded_count": int(patterns["excluded"].sum()) if not patterns.empty else 0,
        "model_path": model_path,
        "trained_at": finished,
    }


def get_advanced_learning_bundle(
    bet_type: str = "3連単",
    *,
    retrain: bool = False,
) -> dict:
    patterns = load_advanced_patterns(bet_type)
    meta_file = load_advanced_meta(bet_type)
    train_result: dict[str, Any] = {}

    valid_ids = get_valid_race_ids(bet_type)
    if retrain:
        train_result = run_advanced_learning(bet_type)
        patterns = load_advanced_patterns(bet_type)
        meta_file = load_advanced_meta(bet_type)

    high = patterns[patterns["recovery_rate"] >= HIGH_EXTRACT].head(10) if not patterns.empty else pd.DataFrame()
    low = (
        patterns[patterns["recovery_rate"] <= LOW_EXCLUDE].sort_values("recovery_rate").head(10)
        if not patterns.empty
        else pd.DataFrame()
    )
    importance = _feature_importance_from_patterns(patterns)

    meta = meta_file.get("meta", meta_file)
    return {
        "has_data": not patterns.empty,
        "has_model": bool(meta_file),
        "can_train": len(valid_ids) >= MIN_VALID_RACES,
        "n_valid_races": len(valid_ids),
        "min_valid_races": MIN_VALID_RACES,
        "n_patterns": len(patterns),
        "before_recovery": meta.get("before_recovery"),
        "after_predicted_recovery": meta.get("after_predicted_recovery"),
        "score_correlation_before": meta.get("score_correlation_before"),
        "score_correlation_after": meta.get("score_correlation_after"),
        "weights": get_score_weights(bet_type),
        "feature_importance": importance,
        "high_recovery_top10": high,
        "low_recovery_top10": low,
        "excluded_count": int(patterns["excluded"].sum()) if not patterns.empty and "excluded" in patterns.columns else 0,
        "patterns": patterns,
        "train_result": train_result,
        "trained_at": meta_file.get("trained_at", ""),
        "model_path": advanced_model_path(bet_type) if meta_file else "",
    }


def build_advanced_learning_lines(bet_type: str = "3連単") -> list[str]:
    b = get_advanced_learning_bundle(bet_type, retrain=False)
    lines = [f"【本格学習】券種={bet_type}", ""]
    if not b["has_data"]:
        lines.append(f"  未学習。有効レース: {b['n_valid_races']} / 必要 {b['min_valid_races']}")
        lines.append("")
        return lines

    lines.append(f"  学習データ: {b['n_valid_races']} レース")
    lines.append(f"  学習前回収率: {b['before_recovery']}%")
    lines.append(f"  学習後予測回収率: {b['after_predicted_recovery']}%")
    lines.append(f"  更新: {b['trained_at']}")
    lines.append("")

    lines.append("--- 重要特徴量 ---")
    imp = b["feature_importance"]
    if imp.empty:
        lines.append("  （なし）")
    else:
        lines.append(imp[["feature", "importance", "avg_recovery"]].head(8).to_string(index=False))
    lines.append("")

    lines.append("--- 高回収条件 TOP10 ---")
    high = b["high_recovery_top10"]
    if high.empty:
        lines.append("  （100%以上なし）")
    else:
        lines.append(
            high[["condition_label", "races", "recovery_rate", "score_adjust"]].to_string(index=False)
        )
    lines.append("")

    lines.append("--- 低回収条件 TOP10 ---")
    low = b["low_recovery_top10"]
    if low.empty:
        lines.append("  （75%以下なし）")
    else:
        lines.append(
            low[["condition_label", "races", "recovery_rate", "score_adjust"]].to_string(index=False)
        )
    lines.append("")
    return lines
