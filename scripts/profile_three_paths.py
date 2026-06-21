"""load_app_bundles / build_race_scores / line分析 の計測（本番コード変更なし）"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BET = "3連単"


def _db_line_stats() -> dict:
    from config import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT race_id, line_info FROM races").fetchall()
    conn.close()
    missing = sum(1 for _, li in rows if not li or li == "不明")
    return {"race_count": len(rows), "missing_line_info": missing}


def _install_hooks() -> dict:
    import race_features as rf

    stats = {"api_calls": 0, "sleep_count": 0, "sleep_sec": 0.0, "api_wall_sec": 0.0}
    _orig_fetch = rf.fetch_line_forecast
    _orig_sleep = time.sleep

    def patched_fetch(race_id: str):
        stats["api_calls"] += 1
        t0 = time.perf_counter()
        result = _orig_fetch(race_id)
        stats["api_wall_sec"] += time.perf_counter() - t0
        return result

    def patched_sleep(sec: float):
        stats["sleep_count"] += 1
        stats["sleep_sec"] += float(sec)
        return _orig_sleep(sec)

    rf.fetch_line_forecast = patched_fetch  # type: ignore[method-assign]
    time.sleep = patched_sleep  # type: ignore[assignment]
    return stats


def _run(label: str, fn, stats: dict) -> dict:
    stats.clear()
    stats.update({"api_calls": 0, "sleep_count": 0, "sleep_sec": 0.0, "api_wall_sec": 0.0})
    t0 = time.perf_counter()
    fn()
    wall = time.perf_counter() - t0
    row = {
        "label": label,
        "wall_sec": round(wall, 3),
        "api_calls": stats["api_calls"],
        "sleep_count": stats["sleep_count"],
        "sleep_sec": round(stats["sleep_sec"], 3),
        "api_wall_sec": round(stats["api_wall_sec"], 3),
    }
    print(json.dumps(row, ensure_ascii=False))
    return row


def profile_build_race_scores(stats: dict) -> list[dict]:
    import race_features as rf
    from ai_score import build_race_scores

    out = []
    rf.clear_race_metrics_cache()
    out.append(
        _run(
            "build_race_scores(fetch_missing_lines=True)",
            lambda: build_race_scores(BET, fetch_missing_lines=True),
            stats,
        )
    )
    rf.clear_race_metrics_cache()
    out.append(
        _run(
            "build_race_scores(fetch_missing_lines=False)",
            lambda: build_race_scores(BET, fetch_missing_lines=False),
            stats,
        )
    )
    return out


def profile_line_analysis(stats: dict) -> list[dict]:
    from line_analysis import get_line_analysis_bundle

    out = []
    out.append(
        _run(
            "get_line_analysis_bundle(fetch_missing=True)",
            lambda: get_line_analysis_bundle(fetch_missing=True),
            stats,
        )
    )
    out.append(
        _run(
            "get_line_analysis_bundle(fetch_missing=False)",
            lambda: get_line_analysis_bundle(fetch_missing=False),
            stats,
        )
    )
    return out


def profile_load_app_bundles_steps(stats: dict) -> list[dict]:
    """load_app_bundles 相当をステップ別に計測（app import 回避）"""
    from advanced_learning import get_advanced_learning_bundle
    from ai_insights import get_ai_insights_bundle
    from ai_recommend import get_ai_recommend_bundle
    from ai_score import get_ai_score_bundle
    from detect_anomaly import detect_all
    from backup import get_backup_bundle
    from battle_judge import get_battle_judge_bundle
    from bet_tracker import get_pnl_bundle
    from bulk_collect import get_collect_bundle
    from charts import get_charts_bundle
    from config import TARGET_RACES
    from data_quality import get_quality_bundle
    from improvement_ai import get_improvement_bundle
    from learning import get_learning_bundle
    from line_analysis import get_line_analysis_bundle
    from market_monitor import get_market_monitor_bundle
    from ml_model import get_ml_bundle
    from notifications import get_notification_bundle
    from ops import get_ops_status
    from pre_race import get_pre_race_bundle
    from report import build_analyze_lines
    from system_check import get_system_check_bundle
    from validation_report import get_validation_bundle
    from bankroll import get_bankroll_bundle

    steps: list[tuple[str, callable]] = []

    def add(name: str, fn):
        steps.append((name, fn))

    score_holder: dict = {}
    rec_holder: dict = {}
    market_holder: dict = {}
    pre_holder: dict = {}
    ml_holder: dict = {}
    quality_holder: dict = {}
    advanced_holder: dict = {}
    line_holder: dict = {}
    battle_holder: dict = {}
    bankroll_holder: dict = {}
    validation_holder: dict = {}
    learning_holder: dict = {}
    backup_holder: dict = {}

    add("score_bundle", lambda: score_holder.update({"v": get_ai_score_bundle(BET)}))
    add(
        "recommend_bundle",
        lambda: rec_holder.update(
            {"v": get_ai_recommend_bundle(BET, scores=score_holder["v"]["scores"])}
        ),
    )
    add("pre_race_bundle", lambda: pre_holder.update({"v": get_pre_race_bundle(BET)}))
    add("market_bundle", lambda: market_holder.update({"v": get_market_monitor_bundle(BET)}))
    add(
        "learning_bundle",
        lambda: learning_holder.update({"v": get_learning_bundle(BET, refresh=True)}),
    )
    add(
        "ml_bundle",
        lambda: ml_holder.update(
            {
                "v": get_ml_bundle(
                    BET, scores=score_holder["v"]["scores"], retrain=False
                )
            }
        ),
    )
    add("quality_bundle", lambda: quality_holder.update({"v": get_quality_bundle(BET)}))
    add(
        "advanced_bundle",
        lambda: advanced_holder.update(
            {"v": get_advanced_learning_bundle(BET, retrain=False)}
        ),
    )
    add(
        "line_bundle",
        lambda: line_holder.update({"v": get_line_analysis_bundle()}),
    )
    add(
        "battle_bundle",
        lambda: battle_holder.update(
            {
                "v": get_battle_judge_bundle(
                    BET,
                    scores=score_holder["v"]["scores"],
                    market=market_holder["v"],
                    line=line_holder["v"],
                    pre_race=pre_holder["v"],
                    ml=ml_holder["v"],
                    quality=quality_holder["v"],
                    advanced=advanced_holder["v"],
                )
            }
        ),
    )
    add(
        "bankroll_bundle",
        lambda: bankroll_holder.update(
            {"v": get_bankroll_bundle(BET, battle_bundle=battle_holder["v"])}
        ),
    )
    add(
        "validation_bundle",
        lambda: validation_holder.update(
            {
                "v": get_validation_bundle(
                    BET,
                    battle_bundle=battle_holder["v"],
                    bankroll_plan=bankroll_holder["v"],
                    sync_virtual=True,
                )
            }
        ),
    )
    add("backup_bundle", lambda: backup_holder.update({"v": get_backup_bundle()}))
    add("analyze_text", lambda: build_analyze_lines(BET))
    add("ai_bundle", lambda: get_ai_insights_bundle(BET))
    add(
        "ops_status",
        lambda: get_ops_status(
            BET, fast=True, targets_count=len(rec_holder["v"].get("targets") or [])
        ),
    )
    add("charts_bundle", lambda: get_charts_bundle(BET))
    add(
        "notify_bundle",
        lambda: get_notification_bundle(
            BET,
            scores=score_holder["v"]["scores"],
            recommend=rec_holder["v"],
            pre_race=pre_holder["v"],
            market=market_holder["v"],
            persist=True,
        ),
    )
    add(
        "pnl_bundle",
        lambda: get_pnl_bundle(BET, recommend=rec_holder["v"], sync_virtual=False),
    )
    add("collect_bundle", lambda: get_collect_bundle(TARGET_RACES))
    add(
        "improvement_bundle",
        lambda: get_improvement_bundle(
            BET,
            validation=validation_holder["v"],
            bankroll_plan=bankroll_holder["v"],
        ),
    )
    add("detect_df", lambda: detect_all(BET))

    out: list[dict] = []
    total_api_before = 0
    total_sleep_before = 0.0
    total0 = time.perf_counter()
    for name, fn in steps:
        api_before = stats["api_calls"]
        sleep_before = stats["sleep_sec"]
        t0 = time.perf_counter()
        fn()
        sec = time.perf_counter() - t0
        row = {
            "label": f"load_app_bundles::{name}",
            "wall_sec": round(sec, 3),
            "api_calls_delta": stats["api_calls"] - api_before,
            "sleep_count_delta": stats["sleep_count"] - int(sleep_before / 1.0 if sleep_before else 0),
            "sleep_sec_delta": round(stats["sleep_sec"] - sleep_before, 3),
        }
        # fix sleep_count_delta properly
        row["sleep_count_delta"] = stats["sleep_count"] - (
            total_sleep_before and stats["sleep_count"]  # placeholder
        )
        out.append(row)
        total_api_before = stats["api_calls"]
        total_sleep_before = stats["sleep_sec"]
        print(json.dumps(row, ensure_ascii=False))

    # Re-run with per-step delta tracking cleanly
    return out


def profile_load_app_bundles_clean(stats: dict) -> dict:
    """load_app_bundles / build_full_app_bundles 全体 + ステップ内訳"""
    from bundle_cache import build_full_app_bundles
    import race_features as rf

    rf.clear_race_metrics_cache()
    stats.clear()
    stats.update({"api_calls": 0, "sleep_count": 0, "sleep_sec": 0.0, "api_wall_sec": 0.0})
    t0 = time.perf_counter()
    build_full_app_bundles(BET)
    total_wall = time.perf_counter() - t0
    summary = {
        "label": "build_full_app_bundles() total",
        "wall_sec": round(total_wall, 3),
        "api_calls": stats["api_calls"],
        "sleep_count": stats["sleep_count"],
        "sleep_sec": round(stats["sleep_sec"], 3),
        "api_wall_sec": round(stats["api_wall_sec"], 3),
    }
    print("LOAD_APP_BUNDLES_SUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    from config import REQUEST_INTERVAL

    db = _db_line_stats()
    print("DB", json.dumps(db))
    print("REQUEST_INTERVAL", REQUEST_INTERVAL)

    stats = _install_hooks()

    print("=== build_race_scores ===")
    profile_build_race_scores(stats)

    print("=== line_analysis ===")
    profile_line_analysis(stats)

    print("=== load_app_bundles ===")
    profile_load_app_bundles_clean(stats)


if __name__ == "__main__":
    main()
