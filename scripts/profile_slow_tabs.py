"""4タブの関数単位プロファイル"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BET = "3連単"


def timed(label: str, fn):
    t0 = time.perf_counter()
    fn()
    sec = time.perf_counter() - t0
    print(f"{sec:7.3f}s  {label}")
    return sec


def profile_battle():
    print("\n=== battle ===")
    from advanced_learning import get_advanced_learning_bundle
    from ai_score import get_ai_score_bundle
    from battle_judge import get_battle_judge_bundle
    from data_quality import get_quality_bundle
    from line_analysis import get_line_analysis_bundle
    from market_monitor import get_market_monitor_bundle
    from ml_model import get_ml_bundle
    from pre_race import get_pre_race_bundle

    scores_b = {}
    timed("cached_ai_score_bundle", lambda: scores_b.update(get_ai_score_bundle(BET)))
    scores = scores_b["scores"] if "scores" in scores_b else get_ai_score_bundle(BET)["scores"]
    market = {}
    line = {}
    pre = {}
    quality = {}
    advanced = {}
    ml = {}
    timed("market", lambda: market.update(get_market_monitor_bundle(BET)))
    timed("line (no api)", lambda: line.update(get_line_analysis_bundle(fetch_missing=False)))
    timed("pre_race", lambda: pre.update(get_pre_race_bundle(BET)))
    timed("quality", lambda: quality.update(get_quality_bundle(BET, refresh=False)))
    timed("advanced", lambda: advanced.update(get_advanced_learning_bundle(BET, retrain=False)))
    timed("ml", lambda: ml.update(get_ml_bundle(BET, scores=scores, retrain=False)))
    timed(
        "battle_judge merge",
        lambda: get_battle_judge_bundle(
            BET,
            scores=scores,
            market=market,
            line=line,
            pre_race=pre,
            ml=ml,
            quality=quality,
            advanced=advanced,
        ),
    )


def profile_ai_insights():
    print("\n=== predict_ai ===")
    from ai_insights import get_ai_insights_bundle
    from race_features import build_race_metrics, recovery_by_feature

    metrics = {}
    timed("build_race_metrics", lambda: metrics.update({"m": build_race_metrics(BET)}))
    m = metrics["m"]
    timed("recovery line", lambda: recovery_by_feature(BET, "line_count", m))
    timed("recovery nige", lambda: recovery_by_feature(BET, "nige_count", m))
    timed("recovery ninki", lambda: recovery_by_feature(BET, "ninki_concentration", m))
    timed("recovery are", lambda: recovery_by_feature(BET, "are_index", m))
    timed("get_ai_insights_bundle", lambda: get_ai_insights_bundle(BET))


def profile_improve():
    print("\n=== improve ===")
    from bankroll import get_bankroll_bundle
    from battle_judge import get_battle_judge_bundle
    from improvement_ai import build_improvement_proposals, get_improvement_bundle
    from validation_report import get_validation_bundle

    battle = {}
    timed("battle (bare)", lambda: battle.update(get_battle_judge_bundle(BET)))
    b = battle if isinstance(battle, dict) and battle.get("has_data") is not None else get_battle_judge_bundle(BET)
    bankroll = {}
    timed("bankroll", lambda: bankroll.update(get_bankroll_bundle(BET, battle_bundle=b)))
    validation = {}
    timed(
        "validation",
        lambda: validation.update(
            get_validation_bundle(BET, battle_bundle=b, bankroll_plan=bankroll, sync_virtual=False)
        ),
    )
    timed(
        "build_improvement_proposals",
        lambda: build_improvement_proposals(BET, validation=validation, bankroll_plan=bankroll),
    )
    timed(
        "get_improvement_bundle",
        lambda: get_improvement_bundle(BET, validation=validation, bankroll_plan=bankroll),
    )


def profile_check():
    print("\n=== check ===")
    from ai_score import get_ai_score_bundle
    from backup import get_backup_bundle
    from data_quality import get_quality_bundle
    from learning import get_learning_bundle
    from system_check import get_system_check_bundle, run_system_checks

    q = s = l = bk = {}
    timed("quality", lambda: q.update(get_quality_bundle(BET, refresh=False)))
    timed("score", lambda: s.update(get_ai_score_bundle(BET)))
    timed("learning", lambda: l.update(get_learning_bundle(BET, refresh=False)))
    timed("backup", lambda: bk.update(get_backup_bundle()))
    timed(
        "run_system_checks deep=True",
        lambda: run_system_checks(
            BET, deep=True, quality=q, score_bundle=s, learning_bundle=l, backup_bundle=bk
        ),
    )
    timed(
        "get_system_check_bundle",
        lambda: get_system_check_bundle(
            BET, deep=True, quality=q, score_bundle=s, learning_bundle=l, backup_bundle=bk
        ),
    )


if __name__ == "__main__":
    from auth import ensure_db_schema_only

    ensure_db_schema_only()
    profile_battle()
    profile_ai_insights()
    profile_improve()
    profile_check()
