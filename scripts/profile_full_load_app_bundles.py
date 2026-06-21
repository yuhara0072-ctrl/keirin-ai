"""load_app_bundles() 全ステップの内訳計測（app.py import 回避）"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BET = "3連単"


def timed(label: str, fn):
    t0 = time.perf_counter()
    r = fn()
    sec = time.perf_counter() - t0
    print(f"{sec:7.3f}s  {label}")
    return r, sec


def main() -> None:
    from report import build_analyze_lines
    from ai_insights import get_ai_insights_bundle
    from ai_recommend import get_ai_recommend_bundle
    from ai_score import get_ai_score_bundle
    from advanced_learning import get_advanced_learning_bundle
    from backup import get_backup_bundle
    from battle_judge import get_battle_judge_bundle
    from bet_tracker import get_pnl_bundle
    from bulk_collect import get_collect_bundle
    from charts import get_charts_bundle
    from config import TARGET_RACES
    from data_quality import get_quality_bundle
    from detect_anomaly import detect_all
    from improvement_ai import get_improvement_bundle
    from learning import get_learning_bundle
    from line_analysis import get_line_analysis_bundle
    from market_monitor import get_market_monitor_bundle
    from ml_model import get_ml_bundle
    from notifications import get_notification_bundle
    from ops import get_ops_status
    from pre_race import get_pre_race_bundle
    from system_check import get_system_check_bundle
    from validation_report import get_validation_bundle
    from bankroll import get_bankroll_bundle

    total0 = time.perf_counter()
    score_bundle, _ = timed("score", lambda: get_ai_score_bundle(BET))
    recommend, _ = timed("recommend", lambda: get_ai_recommend_bundle(BET, scores=score_bundle["scores"]))
    pre, _ = timed("pre_race", lambda: get_pre_race_bundle(BET))
    market, _ = timed("market", lambda: get_market_monitor_bundle(BET))
    learning, _ = timed("learning refresh=True", lambda: get_learning_bundle(BET, refresh=True))
    ml, _ = timed("ml", lambda: get_ml_bundle(BET, scores=score_bundle["scores"], retrain=False))
    quality, _ = timed("quality", lambda: get_quality_bundle(BET))
    advanced, _ = timed("advanced", lambda: get_advanced_learning_bundle(BET, retrain=False))
    line, _ = timed("line fetch_missing=True", lambda: get_line_analysis_bundle())
    battle, _ = timed("battle", lambda: get_battle_judge_bundle(
        BET, scores=score_bundle["scores"], market=market, line=line,
        pre_race=pre, ml=ml, quality=quality, advanced=advanced,
    ))
    bankroll, _ = timed("bankroll", lambda: get_bankroll_bundle(BET, battle_bundle=battle))
    validation, _ = timed("validation sync_virtual=True", lambda: get_validation_bundle(
        BET, battle_bundle=battle, bankroll_plan=bankroll, sync_virtual=True,
    ))
    timed("backup", lambda: get_backup_bundle())
    timed("ai_insights", lambda: get_ai_insights_bundle(BET))
    timed("ops", lambda: get_ops_status(BET, fast=True, targets_count=len(recommend.get("targets") or [])))
    timed("charts", lambda: get_charts_bundle(BET))
    timed("notify persist=True", lambda: get_notification_bundle(
        BET, scores=score_bundle["scores"], recommend=recommend,
        pre_race=pre, market=market, persist=True,
    ))
    timed("pnl", lambda: get_pnl_bundle(BET, recommend=recommend, sync_virtual=False))
    timed("collect", lambda: get_collect_bundle(TARGET_RACES))
    timed("improvement", lambda: get_improvement_bundle(BET, validation=validation, bankroll_plan=bankroll))
    timed("detect", lambda: detect_all(BET))
    timed("analyze lines", lambda: build_analyze_lines(BET))
    timed("system_check", lambda: get_system_check_bundle(
        BET, deep=False, quality=quality, score_bundle=score_bundle,
        learning_bundle=learning, backup_bundle=get_backup_bundle(),
    ))
    print(f"\nTOTAL load_app_bundles + system_check: {time.perf_counter() - total0:.3f}s")


if __name__ == "__main__":
    main()
