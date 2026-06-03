"""競輪観測AI — Streamlit アプリ"""

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from ai_insights import build_ai_insights_lines, get_ai_insights_bundle
from ai_recommend import get_ai_recommend_bundle
from ai_score import get_ai_score_bundle
from charts import HIGH_SCORE_DEFAULT, get_charts_bundle
from fetch_odds import list_race_ids_in_db, poll_odds_for_races
from line_analysis import get_line_analysis_bundle
from learning import (
    build_learning_applied_frame,
    build_learning_lines,
    get_learning_bundle,
    learning_applied_summary,
    save_learned_patterns,
)
from market_monitor import get_market_monitor_bundle
from ml_model import build_ml_lines, get_ml_bundle, train_ml_model
from backup import (
    build_backup_lines,
    create_backup,
    format_size,
    get_backup_bundle,
    restore_backup,
)
from bet_tracker import (
    add_bet_record,
    add_bets_from_cards,
    build_pnl_lines,
    get_pnl_bundle,
    history_display,
    settle_pending_bets,
    sync_virtual_bets,
)
from bulk_collect import fetch_bulk, get_collect_bundle
from battle_judge import build_battle_judge_lines, get_battle_judge_bundle
from bankroll import (
    build_bankroll_lines,
    get_bankroll_bundle,
    save_bankroll_snapshot,
    set_bankroll_config,
)

NO_DATA_MESSAGE = "まだデータがありません。workflow実行してください"


def _validation_import_stubs() -> dict:
    """validation_report 読み込み失敗時の最小スタブ"""

    def empty_validation_report(bet_type: str = "3連単") -> dict:
        empty_summary = {
            "count": 0,
            "total_bet": 0,
            "total_payout": 0,
            "total_profit": 0,
            "recovery_rate": 0.0,
            "hit_rate": 0.0,
            "settled": 0,
            "pending": 0,
        }

        def _period(label: str) -> dict:
            return {
                "label": label,
                "summary": empty_summary.copy(),
                "by_ai_score": pd.DataFrame(),
                "by_verdict": pd.DataFrame(),
                "by_rank": pd.DataFrame(),
                "by_amount": pd.DataFrame(),
                "settled_count": 0,
            }

        return {
            "bet_type": bet_type,
            "ref_date": date.today().strftime("%Y%m%d"),
            "has_data": False,
            "today": _period("今日"),
            "today_virtual": _period("今日(仮想)"),
            "week": _period("今週"),
            "month": _period("今月"),
            "strong_conditions": pd.DataFrame(),
            "weak_conditions": pd.DataFrame(),
            "improvements": [],
            "lines": [],
            "history": pd.DataFrame(),
            "summary_all_actual": empty_summary.copy(),
            "summary_all_virtual": empty_summary.copy(),
            "streaks": {},
            "quality_valid_pct": 0.0,
        }

    def safe_validation_period(report: dict, key: str) -> dict:
        period = (report or {}).get(key)
        if isinstance(period, dict) and isinstance(period.get("summary"), dict):
            return period
        return empty_validation_report().get(key) or empty_validation_report()["month"]

    def get_validation_bundle(
        bet_type: str = "3連単",
        *,
        battle_bundle=None,
        bankroll_plan=None,
        sync_virtual: bool = True,
    ) -> dict:
        return empty_validation_report(bet_type)

    def build_validation_lines(report=None, bet_type: str = "3連単") -> list[str]:
        return [NO_DATA_MESSAGE]

    def run_daily_validation(bet_type: str = "3連単") -> dict:
        return {"ok": False, "improvements": [], "report_path": ""}

    def save_validation_report(bet_type: str = "3連単", **kwargs) -> Path:
        out = DATA_DIR / "validation" / "validation_latest.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            out.write_text(NO_DATA_MESSAGE, encoding="utf-8")
        return out

    return {
        "build_validation_lines": build_validation_lines,
        "empty_validation_report": empty_validation_report,
        "get_validation_bundle": get_validation_bundle,
        "run_daily_validation": run_daily_validation,
        "safe_validation_period": safe_validation_period,
        "save_validation_report": save_validation_report,
    }


try:
    from validation_report import (
        build_validation_lines,
        empty_validation_report,
        get_validation_bundle,
        run_daily_validation,
        safe_validation_period,
        save_validation_report,
    )
except ImportError:
    _validation_stubs = _validation_import_stubs()
    build_validation_lines = _validation_stubs["build_validation_lines"]
    empty_validation_report = _validation_stubs["empty_validation_report"]
    get_validation_bundle = _validation_stubs["get_validation_bundle"]
    run_daily_validation = _validation_stubs["run_daily_validation"]
    safe_validation_period = _validation_stubs["safe_validation_period"]
    save_validation_report = _validation_stubs["save_validation_report"]
from improvement_ai import (
    build_improvement_lines,
    get_improvement_bundle,
    save_improvement_report,
)
from system_check import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_WARN,
    build_system_check_lines,
    get_system_check_bundle,
    save_system_check_report,
)
from advanced_learning import (
    build_advanced_learning_lines,
    get_advanced_learning_bundle,
    run_advanced_learning,
)
from data_quality import build_quality_lines, get_quality_bundle
from notifications import (
    build_notify_lines,
    candidates_to_frame,
    get_notification_bundle,
    save_notifications,
)
from ui_mobile import (
    inject_mobile_style,
    mobile_metrics,
    render_ai_recommend_block,
    render_battle_card,
    render_danger_card,
    render_line_card,
    render_target_card,
)
from auth import (
    DEFER_HEAVY_BUNDLES,
    FULL_BUNDLES_LOADED,
    PENDING_DB_RESTORE,
    WORKFLOW_LAST_RESULT,
    ensure_db_ready,
    ensure_db_schema_only,
    init_auth_session,
    log_session_state,
    reinforce_authenticated,
    render_login_flash,
    render_logout_control,
    require_authentication,
)
from ui_guard import safe_call, safe_page, safe_plotly_chart
from config import (
    DAILY_FETCH_LIMIT,
    DATA_DIR,
    DEFAULT_BANKROLL,
    ENABLE_HOME_GOALS,
    TARGET_RACES,
)
from data_progress import get_data_progress_bundle, get_light_data_progress_bundle
from home_dashboard import build_stable_todos, get_home_dashboard_bundle
from monthly_goal import get_monthly_target, set_monthly_target
from db import ensure_db, get_db_status, init_db
from detect_anomaly import detect_all
from fetch_daily import fetch_daily
from pre_race import (
    build_pre_race_lines,
    capture_pre_race_snapshot,
    get_pre_race_bundle,
    poll_pre_race_due,
)
from ops import (
    build_ops_lines,
    get_ops_status,
    run_daily_auto,
    run_full_ops,
    set_ops_config,
    start_scheduler_thread,
)
from report import build_analyze_lines, build_detect_lines, save_report

REPORT_LATEST = DATA_DIR / "report_latest.txt"


def lines_to_text(lines: list[str]) -> str:
    return "\n".join(lines)


def db_status() -> dict:
    """後方互換 — get_db_status() へ委譲"""
    return get_db_status()


def get_quick_data_status() -> dict:
    """件数だけ先に表示（DB 優先、空なら GitHub meta）"""
    status = db_status()
    if status.get("races", 0) > 0:
        status["source"] = "db"
        return status
    try:
        from github_persist import get_persist_meta_summary

        meta = get_persist_meta_summary()
        if meta and int(meta.get("race_count") or 0) > 0:
            return {
                "races": int(meta.get("race_count") or 0),
                "results": int(meta.get("result_count") or 0),
                "odds": 0,
                "learning": int(meta.get("learning_count") or 0),
                "ready": True,
                "source": meta.get("_source", "meta"),
            }
    except Exception:
        pass
    status["source"] = "empty"
    return status


def load_full_bundles_and_restore(bet_type: str, *, deep_check: bool = False) -> None:
    """詳細タブ用 — GitHub 復元 + 全バンドル読み込み"""
    ensure_db_ready(force_restore=True)
    invalidate_bundles_cache()
    bundles, err = load_app_bundles_cached(bet_type, deep_check=deep_check)
    st.session_state[FULL_BUNDLES_LOADED] = True
    st.session_state["full_bundles_data"] = bundles
    st.session_state["full_bundles_error"] = err
    st.session_state.pop(DEFER_HEAVY_BUNDLES, None)
    print("[app] full bundles loaded", flush=True)


def prompt_load_bundles_if_needed(tab_label: str, bet_type: str) -> bool:
    """未読み込みならプレースホルダを表示して True（本文スキップ）"""
    if st.session_state.get(FULL_BUNDLES_LOADED):
        return False
    st.info(
        f"「{tab_label}」の詳細はまだ読み込んでいません。"
        " ホームの「詳細データを読み込む」または下のボタンで読み込めます。"
    )
    if st.button(f"{tab_label} を読み込む", key=f"load_tab_{tab_label}", use_container_width=True):
        load_full_bundles_and_restore(bet_type)
        st.rerun()
    return True


def empty_recommend_bundle() -> dict:
    return {
        "has_data": False,
        "targets": [],
        "dangerous_popular": [],
        "skip_races": [],
        "global_picks": [],
        "all_cards": [],
        "lines": [],
    }


def empty_score_bundle() -> dict:
    return {"scores": pd.DataFrame(), "has_data": False}


def empty_battle_bundle() -> dict:
    return {
        "has_data": False,
        "buy_candidates": [],
        "small_candidates": [],
        "skip_candidates": [],
        "dangerous_popular": [],
    }


def empty_validation_bundle() -> dict:
    return empty_validation_report()


def load_app_bundles(bet_type: str) -> dict:
    """データ0件・Cloud初回でも落ちないバンドル読み込み"""
    score_bundle = get_ai_score_bundle(bet_type)
    recommend_bundle = get_ai_recommend_bundle(
        bet_type, scores=score_bundle["scores"]
    )
    pre_race_bundle = get_pre_race_bundle(bet_type)
    market_bundle = get_market_monitor_bundle(bet_type)
    learning_bundle = get_learning_bundle(bet_type, refresh=True)
    ml_bundle = get_ml_bundle(bet_type, scores=score_bundle["scores"], retrain=False)
    quality_bundle = get_quality_bundle(bet_type)
    advanced_bundle = get_advanced_learning_bundle(bet_type, retrain=False)
    line_bundle = get_line_analysis_bundle()
    battle_bundle = get_battle_judge_bundle(
        bet_type,
        scores=score_bundle["scores"],
        market=market_bundle,
        line=line_bundle,
        pre_race=pre_race_bundle,
        ml=ml_bundle,
        quality=quality_bundle,
        advanced=advanced_bundle,
    )
    bankroll_bundle = get_bankroll_bundle(bet_type, battle_bundle=battle_bundle)
    validation_bundle = get_validation_bundle(
        bet_type,
        battle_bundle=battle_bundle,
        bankroll_plan=bankroll_bundle,
        sync_virtual=True,
    )
    backup_bundle = get_backup_bundle()
    return {
        "analyze_text": lines_to_text(build_analyze_lines(bet_type)),
        "ai_bundle": get_ai_insights_bundle(bet_type),
        "score_bundle": score_bundle,
        "recommend_bundle": recommend_bundle,
        "ops_status": get_ops_status(
            bet_type,
            fast=True,
            targets_count=len(recommend_bundle.get("targets") or []),
        ),
        "charts_bundle": get_charts_bundle(bet_type),
        "line_bundle": line_bundle,
        "market_bundle": market_bundle,
        "learning_bundle": learning_bundle,
        "pre_race_bundle": pre_race_bundle,
        "ml_bundle": ml_bundle,
        "notify_bundle": get_notification_bundle(
            bet_type,
            scores=score_bundle["scores"],
            recommend=recommend_bundle,
            pre_race=pre_race_bundle,
            market=market_bundle,
            persist=True,
        ),
        "backup_bundle": backup_bundle,
        "pnl_bundle": get_pnl_bundle(bet_type, recommend=recommend_bundle, sync_virtual=False),
        "collect_bundle": get_collect_bundle(TARGET_RACES),
        "quality_bundle": quality_bundle,
        "advanced_bundle": advanced_bundle,
        "battle_bundle": battle_bundle,
        "bankroll_bundle": bankroll_bundle,
        "validation_bundle": validation_bundle,
        "improvement_bundle": get_improvement_bundle(
            bet_type,
            validation=validation_bundle,
            bankroll_plan=bankroll_bundle,
        ),
        "detect_df": detect_all(bet_type),
    }


def invalidate_bundles_cache() -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith("bundles_"):
            st.session_state.pop(key, None)


def load_app_bundles_cached(
    bet_type: str,
    *,
    deep_check: bool = False,
    show_spinner: bool = True,
) -> tuple[dict, str | None]:
    cache_key = f"bundles_{bet_type}_{deep_check}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    try:
        if show_spinner:
            with st.spinner("詳細データを読み込み中..."):
                bundles, err = load_app_bundles_safe(bet_type, deep_check=deep_check)
        else:
            bundles, err = load_app_bundles_safe(bet_type, deep_check=deep_check)
        st.session_state[cache_key] = (bundles, err)
        return bundles, err
    except Exception as exc:
        print(f"[app] bundle cache error: {exc}", flush=True)
        bundles, err = load_app_bundles_safe(bet_type, deep_check=deep_check)
        return bundles, err


def load_app_bundles_safe(bet_type: str, *, deep_check: bool = False) -> tuple[dict, str | None]:
    try:
        bundles = load_app_bundles(bet_type)
        bundles["system_check_bundle"] = get_system_check_bundle(
            bet_type,
            deep=deep_check,
            quality=bundles["quality_bundle"],
            score_bundle=bundles["score_bundle"],
            learning_bundle=bundles["learning_bundle"],
            backup_bundle=bundles["backup_bundle"],
        )
        return bundles, None
    except Exception as e:
        score = empty_score_bundle()
        rec = empty_recommend_bundle()
        battle = empty_battle_bundle()
        validation = empty_validation_bundle()
        try:
            ops = get_ops_status(bet_type, fast=True, targets_count=0)
        except Exception:
            ops = {"last_finished_at": "—", "auto_enabled": False}
        try:
            backup = get_backup_bundle()
        except Exception:
            backup = {"backups": [], "latest_at": None, "db_size_bytes": 0}
        empty = {
            "analyze_text": NO_DATA_MESSAGE,
            "ai_bundle": {"overall": {}, "metrics": pd.DataFrame(), "venue_trends": pd.DataFrame()},
            "score_bundle": score,
            "recommend_bundle": rec,
            "ops_status": ops,
            "charts_bundle": {"has_data": False},
            "line_bundle": {"has_data": False},
            "market_bundle": {"has_data": False, "needs_poll_hint": False},
            "learning_bundle": {"has_data": False, "learning_count": 0, "patterns": pd.DataFrame()},
            "pre_race_bundle": {},
            "ml_bundle": {"has_model": False},
            "notify_bundle": {
                "candidate_count": 0,
                "high_score": [],
                "danger_popular": [],
                "odds_surge": [],
                "candidates": [],
            },
            "backup_bundle": backup,
            "pnl_bundle": {"summary_actual": {"pending": 0}},
            "collect_bundle": {"remaining": TARGET_RACES},
            "quality_bundle": {"valid_races": 0, "valid_pct": 0, "total_races": 0},
            "advanced_bundle": {"patterns": pd.DataFrame()},
            "battle_bundle": battle,
            "bankroll_bundle": {},
            "validation_bundle": validation,
            "improvement_bundle": {"top5_proposals": pd.DataFrame(), "weaknesses": [], "strengths": []},
            "system_check_bundle": {"overall_status": "warn", "checks_df": pd.DataFrame()},
            "detect_df": pd.DataFrame(),
        }
        return empty, str(e)


def minimal_app_bundles(bet_type: str) -> dict:
    """workflow 直後用 — 全バンドル読み込みを避け OOM / プロセス再起動を防ぐ"""
    score = empty_score_bundle()
    rec = empty_recommend_bundle()
    battle = empty_battle_bundle()
    validation = empty_validation_bundle()
    try:
        ops = get_ops_status(bet_type, fast=True, targets_count=0)
    except Exception:
        ops = {"last_finished_at": "—", "auto_enabled": False}
    try:
        backup = get_backup_bundle()
    except Exception:
        backup = {"backups": [], "latest_at": None, "db_size_bytes": 0}
    return {
        "analyze_text": "",
        "ai_bundle": {"overall": {}, "metrics": pd.DataFrame(), "venue_trends": pd.DataFrame()},
        "score_bundle": score,
        "recommend_bundle": rec,
        "ops_status": ops,
        "charts_bundle": {"has_data": False},
        "line_bundle": {"has_data": False},
        "market_bundle": {"has_data": False, "needs_poll_hint": False},
        "learning_bundle": {"has_data": False, "learning_count": 0, "patterns": pd.DataFrame()},
        "pre_race_bundle": {},
        "ml_bundle": {"has_model": False},
        "notify_bundle": {
            "candidate_count": 0,
            "high_score": [],
            "danger_popular": [],
            "odds_surge": [],
            "candidates": [],
        },
        "backup_bundle": backup,
        "pnl_bundle": {"summary_actual": {"pending": 0}},
        "collect_bundle": {"remaining": TARGET_RACES},
        "quality_bundle": {"valid_races": 0, "valid_pct": 0, "total_races": 0},
        "advanced_bundle": {"patterns": pd.DataFrame()},
        "battle_bundle": battle,
        "bankroll_bundle": {},
        "validation_bundle": validation,
        "improvement_bundle": {"top5_proposals": pd.DataFrame(), "weaknesses": [], "strengths": []},
        "system_check_bundle": {"overall_status": "warn", "checks_df": pd.DataFrame()},
        "detect_df": pd.DataFrame(),
    }


def run_workflow(
    kaisai_date: str,
    limit: int,
    with_result: bool,
    venue_code: str | None,
    bet_type: str,
) -> tuple[bool, str]:
    log: list[str] = []
    try:
        ensure_db()
        log.append(f"開催日: {kaisai_date} / 取得上限: {limit}件")
        log.append("STEP 1/4: レース取得中...")
        results = fetch_daily(
            kaisai_date=kaisai_date,
            limit=limit,
            with_result=with_result,
            venue_code=venue_code,
        )
        ok = sum(1 for r in results if not r.get("error"))
        log.append(f"  → {ok}/{len(results)} 件成功")

        log.append("STEP 2-4/4: 分析・検知・レポート保存...")
        path = save_report(bet_type=bet_type)
        log.append(f"  → 保存: {path}")
        log.append("STEP 4/4 完了（永続化は UI 側で 1 回のみ実行）")
        return True, "\n".join(log)
    except Exception as e:
        log.append(f"エラー: {e}")
        return False, "\n".join(log)


def load_report_latest() -> str:
    if not REPORT_LATEST.exists():
        return "（まだレポートがありません。workflowを実行してください）"
    return REPORT_LATEST.read_text(encoding="utf-8")


def get_last_updated_at(ops: dict) -> str:
    candidates: list[str] = []
    if ops.get("last_finished_at") and ops["last_finished_at"] != "—":
        candidates.append(str(ops["last_finished_at"])[:19])
    if REPORT_LATEST.exists():
        ts = datetime.fromtimestamp(REPORT_LATEST.stat().st_mtime)
        candidates.append(ts.strftime("%Y-%m-%d %H:%M:%S"))
    if candidates:
        return max(candidates)
    return "—"


def build_ai_status_summary(
    *,
    recommend: dict,
    battle: dict,
    learning: dict,
    ml: dict,
    validation: dict,
    quality: dict,
) -> dict:
    week = validation.get("week", {}).get("summary", {})
    return {
        "targets": len(recommend.get("targets") or []),
        "buy": len(battle.get("buy_candidates") or []),
        "skip": len(battle.get("skip_candidates") or []),
        "patterns": learning.get("learning_count", 0),
        "ml_ready": bool(ml.get("has_model")),
        "week_recovery": week.get("recovery_rate"),
        "week_settled": week.get("settled", 0),
        "valid_races": quality.get("valid_races", 0),
        "valid_pct": quality.get("valid_pct", 0),
    }


st.set_page_config(
    page_title="競輪観測AI",
    page_icon="🚴",
    layout="centered",
    initial_sidebar_state="expanded",
)

inject_mobile_style()

init_auth_session()

if not require_authentication():
    st.stop()

render_login_flash()

try:
    ensure_db_schema_only()
except Exception as _bootstrap_exc:
    print(f"[auth] ensure_db_schema_only error: {_bootstrap_exc}", flush=True)

st.title("🚴 競輪観測AI")
st.caption("🏠 運用モード — ホームから毎日の判断")

with safe_page("サイドバー"):
    with st.sidebar:
        render_logout_control()
        st.divider()
        st.header("毎日の操作")

        with st.expander("📥 データ取得", expanded=True):
            selected = st.date_input("開催日", value=date.today())
            kaisai_date = selected.strftime("%Y%m%d")
            limit = st.number_input(
                "取得件数", min_value=1, max_value=30, value=DAILY_FETCH_LIMIT, step=1
            )
            with_result = st.checkbox("結果・払戻も取得", value=True)
            venue_code = st.text_input("場コード（任意）", value="", placeholder="例: 56")
            run_btn = st.button("▶ workflow 実行", type="primary", use_container_width=True)

        with st.expander("⚙ 券種・分析", expanded=False):
            bet_type = st.selectbox(
                "分析券種",
                ["3連単", "2車単", "2車複", "3連複", "ワイド"],
                index=0,
            )

        status = get_quick_data_status()
        with st.expander("📊 データ件数", expanded=True):
            src = status.get("source", "")
            cap = f" ({src})" if src and src != "db" else ""
            mobile_metrics(
                [
                    (f"レース{cap}", status["races"]),
                    ("結果", status["results"]),
                    ("オッズ", status["odds"] or "—"),
                ],
                per_row=1,
            )

        if run_btn:
            venue = venue_code.strip() or None
            persist_lines: list[str] = []
            sync_result: dict = {}
            with st.spinner("workflow 実行中（数分かかります）..."):
                success, log_text = run_workflow(
                    kaisai_date=kaisai_date,
                    limit=int(limit),
                    with_result=with_result,
                    venue_code=venue,
                    bet_type=bet_type,
                )
            print(f"[workflow] done success={success}", flush=True)

            persist_ok = False
            if success:
                try:
                    from github_persist import execute_workflow_persist_with_print

                    sync_result, persist_lines = execute_workflow_persist_with_print(
                        "workflow"
                    )
                    persist_ok = bool(sync_result.get("ok")) and sync_result.get(
                        "race_count", 0
                    ) > 0
                except Exception as exc:
                    print(f"[persist] error: {exc}", flush=True)
                    persist_lines = [f"永続化エラー: {exc}"]
            else:
                persist_lines = ["fetch/report が失敗したため永続化をスキップしました"]

            combined_log = log_text
            if persist_lines:
                combined_log = f"{log_text}\n" + "\n".join(
                    f"  {line}" if not line.startswith("---") else line
                    for line in persist_lines
                )

            workflow_ok = success and persist_ok
            race_n = int(sync_result.get("race_count") or 0)
            if workflow_ok:
                msg = f"workflow 完了 — レース {race_n} 件を保存"
                if sync_result.get("github"):
                    msg += "（GitHub data ブランチ）"
            else:
                msg = "workflow エラー（ログを確認）"

            reinforce_authenticated()
            log_session_state("post-workflow")
            st.session_state[WORKFLOW_LAST_RESULT] = {
                "ok": workflow_ok,
                "message": msg,
                "log": combined_log,
                "race_count": race_n,
                "github": bool(sync_result.get("github")),
            }
            invalidate_bundles_cache()
            st.session_state.pop(FULL_BUNDLES_LOADED, None)
            st.session_state[PENDING_DB_RESTORE] = False
            status = db_status()
            print(
                f"[workflow] db after persist: races={status['races']} "
                f"results={status['results']} learning={status.get('learning', 0)}",
                flush=True,
            )

        _wf_side = st.session_state.get(WORKFLOW_LAST_RESULT)
        if _wf_side:
            if _wf_side.get("ok"):
                st.success(_wf_side["message"])
            else:
                st.error(_wf_side["message"])
            with st.expander("workflow ログ", expanded=False):
                st.text(_wf_side.get("log", ""))

        st.caption("🏠 ホームで完結 — データ収集と検証を優先")

_check_deep = st.session_state.pop("system_check_deep", False)
if _check_deep:
    invalidate_bundles_cache()
    load_full_bundles_and_restore(bet_type, deep_check=True)

_full_loaded = bool(st.session_state.get(FULL_BUNDLES_LOADED))
with safe_page("データ読み込み"):
    if _full_loaded and st.session_state.get("full_bundles_data"):
        _bundles = st.session_state["full_bundles_data"]
        _bundle_error = st.session_state.get("full_bundles_error")
    elif _full_loaded:
        _bundles, _bundle_error = load_app_bundles_cached(
            bet_type, deep_check=_check_deep, show_spinner=False
        )
        st.session_state["full_bundles_data"] = _bundles
        st.session_state["full_bundles_error"] = _bundle_error
    else:
        print("[app] lightweight mode — minimal bundles", flush=True)
        _bundles = minimal_app_bundles(bet_type)
        _bundle_error = None

analyze_text = _bundles["analyze_text"]
ai_bundle = _bundles["ai_bundle"]
score_bundle = _bundles["score_bundle"]
recommend_bundle = _bundles["recommend_bundle"]
ops_status = _bundles["ops_status"]
charts_bundle = _bundles["charts_bundle"]
line_bundle = _bundles["line_bundle"]
market_bundle = _bundles["market_bundle"]
learning_bundle = _bundles["learning_bundle"]
pre_race_bundle = _bundles["pre_race_bundle"]
ml_bundle = _bundles["ml_bundle"]
notify_bundle = _bundles["notify_bundle"]
backup_bundle = _bundles["backup_bundle"]
pnl_bundle = _bundles["pnl_bundle"]
collect_bundle = _bundles["collect_bundle"]
quality_bundle = _bundles["quality_bundle"]
advanced_bundle = _bundles["advanced_bundle"]
battle_bundle = _bundles["battle_bundle"]
bankroll_bundle = _bundles["bankroll_bundle"]
validation_bundle = _bundles["validation_bundle"]
improvement_bundle = _bundles["improvement_bundle"]
system_check_bundle = _bundles["system_check_bundle"]
detect_df = _bundles["detect_df"]

_dashboard_status = db_status()
if _dashboard_status.get("races", 0) == 0 and not _full_loaded:
    _dashboard_status = get_quick_data_status()

with safe_page("ダッシュボード"):
    if _dashboard_status["races"] == 0:
        st.info(NO_DATA_MESSAGE)
    elif _bundle_error:
        st.warning(f"一部データの読み込みに失敗しました: {_bundle_error}")

last_updated = "—"
ai_status: dict = {}
today_todos: list = []
data_progress: dict = {}
home_dashboard: dict = {
    "pillars": [],
    "prediction": {"has_data": False, "targets": [], "trust": {}},
    "monthly": {
        "target_profit": 10_000,
        "current_profit": 0,
        "remaining": 10_000,
        "achieved": False,
        "daily_required": 0,
        "progress_ratio": 0.0,
        "progress_pct": 0.0,
        "stance": "標準",
        "stance_reason": "",
        "month_label": "",
        "month_stats": {"recovery_rate": 0, "settled": 0},
    },
    "learning_snap": {},
    "bankroll": {},
    "todos": [],
}

with safe_page("メイン初期化"):
    if _full_loaded:
        if "ops_scheduler_started" not in st.session_state:
            st.session_state.ops_scheduler_started = True
            start_scheduler_thread(bet_type)

        last_updated = get_last_updated_at(ops_status)
        ai_status = build_ai_status_summary(
            recommend=recommend_bundle,
            battle=battle_bundle,
            learning=learning_bundle,
            ml=ml_bundle,
            validation=validation_bundle,
            quality=quality_bundle,
        )
        data_progress = get_data_progress_bundle(
            total_races=_dashboard_status["races"],
            valid_races=quality_bundle.get("valid_races", 0),
            result_races=_dashboard_status["results"],
        )
        if ENABLE_HOME_GOALS:
            home_dashboard = get_home_dashboard_bundle(
                bet_type=bet_type,
                status=_dashboard_status,
                recommend=recommend_bundle,
                battle=battle_bundle,
                market=market_bundle,
                pnl=pnl_bundle,
                validation=validation_bundle,
                quality=quality_bundle,
                ops=ops_status,
                bankroll=bankroll_bundle,
                learning=learning_bundle,
                data_progress=data_progress,
            )
            today_todos = home_dashboard["todos"]
        else:
            home_dashboard = None
            today_todos = build_stable_todos(
                status=_dashboard_status,
                recommend=recommend_bundle,
                battle=battle_bundle,
                market=market_bundle,
                pnl=pnl_bundle,
                validation=validation_bundle,
                quality=quality_bundle,
                ops=ops_status,
                bankroll=bankroll_bundle,
            )
    else:
        last_updated = "—"
        ai_status = {
            "targets": 0,
            "buy": "—",
            "skip": "—",
            "patterns": "—",
            "week_recovery": None,
            "week_settled": 0,
            "valid_races": 0,
            "valid_pct": 0,
        }
        data_progress = get_light_data_progress_bundle(
            total_races=_dashboard_status["races"],
            result_races=_dashboard_status["results"],
            valid_races=_dashboard_status["results"],
        )
        data_progress["trust"] = {
            **data_progress.get("trust", {}),
            "label": data_progress["trust"].get("label", "—")
            + "（詳細未読み込み）",
            "hint": "詳細データを読み込むと AI 信頼度・有効レース数が更新されます",
        }
        home_dashboard = None
        today_todos = [
            {
                "text": "詳細データを読み込む（ホーム上部のボタン）",
                "done": False,
                "tab": "ホーム",
            }
        ]
        if _dashboard_status["races"] == 0:
            today_todos.insert(
                0,
                {
                    "text": "サイドバーから workflow を実行してデータを取得",
                    "done": False,
                    "tab": "設定",
                },
            )

(
    tab_home,
    tab_rec,
    tab_battle,
    tab_market,
    tab_line,
    tab_predict,
    tab_learn_page,
    tab_pnl,
    tab_bankroll,
    tab_validation,
    tab_improve,
    tab_backup,
    tab_check,
    tab_settings,
) = st.tabs(
    [
        "🏠 ホーム",
        "⭐ 今日のAIおすすめ",
        "🎯 実戦判定",
        "📡 市場監視",
        "🔗 ライン分析",
        "🤖 予測AI",
        "🧠 学習状況",
        "📈 収支検証",
        "💰 資金管理",
        "📊 検証レポート",
        "💡 改善提案",
        "💾 バックアップ",
        "🔧 システムチェック",
        "⚙ 設定",
    ]
)

with tab_predict:
    t_ai, t_ml, t_prerace, t_chart = st.tabs(
        ["📊 AI指標", "🤖 ML予測", "⏱ 直前分析", "📈 グラフ"]
    )

with tab_learn_page:
    t_learn, t_advanced, t_quality, t_collect = st.tabs(
        ["🧠 パターン学習", "🎓 本格学習", "📋 データ品質", "📥 100レース収集"]
    )

with tab_settings:
    t_ops, t_notify, t_analyze, t_detect, t_report, t_help = st.tabs(
        ["⚙ 自動運用", "🔔 通知", "分析", "異常検知", "レポート", "使い方"]
    )

with tab_home, safe_page("ホーム"):
    st.subheader("ホーム")
    rec = recommend_bundle
    dp = data_progress
    trust = dp.get("trust") or {}

    if not _full_loaded:
        if st.button(
            "📥 詳細データを読み込む（分析・推奨・グラフ）",
            type="primary",
            use_container_width=True,
            key="home_load_full_bundles",
        ):
            load_full_bundles_and_restore(bet_type)
            st.rerun()
        st.caption("件数はサイドバーに表示済みです。詳細は読み込み後に表示されます。")

    if not ENABLE_HOME_GOALS:
        st.caption("安定化モード — データ永続化を最優先（月目標UIは検証後に有効化）")
        try:
            from github_persist import get_persist_meta_summary

            meta = get_persist_meta_summary()
            if meta:
                st.info(
                    f"永続化 ({meta.get('_source', '—')}): "
                    f"races={meta.get('race_count', 0)} "
                    f"results={meta.get('result_count', 0)} "
                    f"learning={meta.get('learning_count', 0)} "
                    f"branch={meta.get('persist_branch', '—')} "
                    f"（{meta.get('updated_at', '—')}）"
                )
            st.caption(
                f"DB: レース {_dashboard_status['races']} / 結果 {_dashboard_status['results']} / "
                f"学習 {_dashboard_status.get('learning', 0)}"
            )
        except Exception:
            pass

        st.markdown("#### 今日やること")
        for todo in today_todos:
            icon = "✅" if todo["done"] else "📌"
            st.markdown(f"{icon} **{todo['text']}** → `{todo['tab']}` タブ")

        st.markdown("#### データ件数")
        mobile_metrics(
            [
                ("レース", _dashboard_status["races"]),
                ("結果", _dashboard_status["results"]),
                ("オッズ", _dashboard_status.get("odds") or "—"),
                (
                    "推奨購入",
                    f"{bankroll_bundle.get('recommended_total', 0):,}円"
                    if _full_loaded
                    else "—",
                ),
            ]
        )
        trust_dp = data_progress.get("trust") or {}
        if trust_dp.get("label"):
            st.caption(f"AI信頼度: {trust_dp['label']}")

        if _full_loaded:
            st.markdown("#### 毎日予想")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("狙い目", len(rec.get("targets") or []))
            with c2:
                st.metric("見送り", len(battle_bundle.get("skip_candidates") or []))
            with c3:
                st.metric("危険人気", len(rec.get("dangerous_popular") or []))

            if trust.get("label"):
                st.caption(f"AI信頼度: {trust['label']}")

            if rec.get("targets"):
                for card in rec["targets"][:3]:
                    render_target_card(card)
            elif rec.get("has_data"):
                st.info("本日の狙い目はありません")
            else:
                st.warning(NO_DATA_MESSAGE)

        danger = rec.get("dangerous_popular") or []
        if danger:
            for card in danger[:3]:
                render_danger_card(card)

        st.divider()
    else:
        st.caption("毎日予想 → 学習 → 資金管理 → 月目標達成")

    if ENABLE_HOME_GOALS:
        hd = home_dashboard
        monthly = hd["monthly"]
        pred = hd["prediction"]
        learn = hd["learning_snap"]
        bk = hd["bankroll"]
        trust = pred["trust"]

        with st.expander("このアプリの4つの柱", expanded=False):
            for pillar in hd["pillars"]:
                st.markdown(f"**{pillar['title']}**")
                for item in pillar["items"]:
                    st.markdown(f"- {item}")

        st.markdown("#### 月目標進捗")
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("月目標", f"{monthly['target_profit']:,}円")
        with m2:
            st.metric(
                "今月収支",
                f"{monthly['current_profit']:+,}円",
                delta=f"回収{monthly['month_stats']['recovery_rate']}%"
                if monthly["month_stats"]["settled"]
                else None,
            )
        with m3:
            rem = monthly["remaining"]
            st.metric(
                "目標まで残り",
                "達成済み" if monthly["achieved"] else f"{rem:,}円",
            )
        with m4:
            st.metric("1日あたり必要", f"{monthly['daily_required']:,}円")

        stance = monthly["stance"]
        if stance == "攻める":
            st.success(f"今日の方針: **{stance}** — {monthly['stance_reason']}")
        elif stance == "守る":
            st.warning(f"今日の方針: **{stance}** — {monthly['stance_reason']}")
        else:
            st.info(f"今日の方針: **{stance}** — {monthly['stance_reason']}")
        st.progress(
            monthly["progress_ratio"],
            text=f"{monthly['month_label']} {monthly['progress_pct']}%",
        )

        st.markdown("#### 推奨購入額")
        b1, b2, b3, b4 = st.columns(4)
        with b1:
            st.metric("現在資金", f"{bk.get('current_bankroll', 0):,}円")
        with b2:
            st.metric("本日の推奨合計", f"{bk.get('recommended_total', 0):,}円")
        with b3:
            st.metric("1レース上限", f"{bk.get('max_per_race', 0):,}円")
        with b4:
            st.metric(
                "本日残り枠",
                f"{bk.get('daily_limit_remaining', 0):,}円",
                delta=f"使用 {bk.get('daily_used', 0):,}円",
            )
        if bk.get("warnings"):
            for w in bk["warnings"][:3]:
                st.caption(f"⚠ {w}")
        buy_today = bk.get("buy_today") or []
        if buy_today:
            st.caption("推奨が高いレース（上位3件）")
            for row in buy_today[:3]:
                st.markdown(
                    f"- **{row.get('venue_name')} {row.get('race_no')}R** "
                    f"{row.get('recommended_yen', 0):,}円 "
                    f"（{row.get('stake_reason', '')}）"
                )
        else:
            st.caption("本日の推奨購入は0円 — 見送り・危険レース中心")

        st.markdown("#### 毎日予想")
        p1, p2, p3 = st.columns(3)
        with p1:
            st.metric("狙い目", f"{pred['target_count']} 件")
        with p2:
            st.metric("見送り", f"{pred['skip_count']} 件")
        with p3:
            st.metric("危険人気", f"{pred['danger_count']} 件")

        if trust.get("label"):
            if trust["level"] == "insufficient":
                st.error(f"AI信頼度: **{trust['label']}**")
            elif trust["level"] == "reference":
                st.warning(f"AI信頼度: **{trust['label']}**")
            elif trust["level"] == "verifiable":
                st.info(f"AI信頼度: **{trust['label']}**")
            else:
                st.success(f"AI信頼度: **{trust['label']}**")
            st.caption(trust.get("hint", ""))

        if pred["targets"]:
            for card in pred["targets"][:3]:
                render_target_card(card)
        elif pred["has_data"]:
            st.info("本日の狙い目はありません — 見送り中心の日です")
        else:
            st.warning(NO_DATA_MESSAGE)

        if pred["danger_preview"]:
            st.caption("危険人気（要確認）")
            for card in pred["danger_preview"][:3]:
                render_danger_card(card)

        if pred["skip_preview"]:
            with st.expander(f"見送りレース（{pred['skip_count']}件）", expanded=False):
                for card in pred["skip_preview"]:
                    st.markdown(
                        f"- {card.get('venue_name')} {card.get('race_no')}R "
                        f"AI{card.get('ai_total_score', '—')} "
                        f"— {card.get('battle_hint') or card.get('verdict', '見送り')}"
                    )

        st.markdown("#### 学習（今月の振り返り）")
        if learn["has_data"]:
            st.caption(
                f"確定 {learn['month_settled']} 件 / "
                f"回収 {learn['month_recovery'] or '—'}% / "
                f"的中 {learn['month_hit_rate'] or '—'}% / "
                f"学習条件 {learn['learning_count']} 件"
            )
            if learn["strong_conditions"]:
                st.markdown("**強い条件:** " + " · ".join(learn["strong_conditions"]))
            if learn["weak_conditions"]:
                st.markdown("**弱い条件:** " + " · ".join(learn["weak_conditions"]))
        else:
            st.caption("学習データなし — workflow（結果付き）後に自動反映されます")

        st.markdown("#### 今日やること")
        for todo in today_todos:
            icon = "✅" if todo["done"] else "📌"
            st.markdown(f"{icon} **{todo['text']}** → `{todo['tab']}` タブ")

        st.divider()

    # --- ① 自動実行 ---
    st.markdown("#### ① 今日の自動実行")
    if st.button(
        "▶ 今日の自動実行",
        type="primary",
        use_container_width=True,
        key="home_auto_ops",
    ):
        venue = venue_code.strip() or None
        with st.spinner("自動運用実行中（数分かかります）..."):
            result = run_daily_auto(
                bet_type,
                limit=int(limit),
                with_result=with_result,
                venue_code=venue,
                trigger="home",
            )
        st.session_state["home_ops_result"] = result
        st.rerun()

    ops_result = st.session_state.get("home_ops_result")
    if ops_result:
        if ops_result.get("ok"):
            st.success(
                f"完了 {ops_result.get('finished_at', '')[:16]} "
                f"（取得 {ops_result.get('races_fetched', 0)} 件）"
            )
        else:
            st.error(f"エラー: {ops_result.get('error_message', '不明')}")
        with st.expander("実行ログ", expanded=not ops_result.get("ok")):
            st.text(ops_result.get("log_text", ""))

    with st.expander("データ収集進捗・詳細ステータス", expanded=False):
        dp = data_progress or {}
        st.metric(
            "保存レース数",
            f"{dp.get('saved_total', _dashboard_status.get('races', 0)):,} 件",
            f"有効 {dp.get('saved_valid', 0):,} / 結果 {dp.get('saved_results', _dashboard_status.get('results', 0)):,}",
        )
        for m in dp.get("milestones") or []:
            label = f"{m.get('target', 0):,}件"
            if m.get("done"):
                st.progress(
                    1.0,
                    text=f"{label} 達成 ({m.get('current', 0):,}/{m.get('target', 0):,})",
                )
            else:
                st.progress(
                    m.get("ratio", 0.0),
                    text=f"{label} {m.get('current', 0):,}/{m.get('target', 0):,} ({m.get('pct', 0)}%)",
                )

    with st.expander("詳細ステータス"):
        mobile_metrics(
            [
                ("最終更新", last_updated[:16] if last_updated != "—" else "—"),
                ("買い候補", ai_status.get("buy", "—")),
                ("学習パターン", ai_status.get("patterns", "—")),
                (
                    "今週回収率",
                    (
                        f"{ai_status['week_recovery']}%"
                        if ai_status.get("week_recovery") is not None
                        and ai_status.get("week_settled")
                        else "—"
                    ),
                ),
            ]
        )

    with st.expander("運用のヒント"):
        st.markdown(
            """
- **朝**: ① 自動実行 → ③④⑤ を確認
- **レース前**: 📡 市場監視でオッズ再取得（2回以上）
- **購入前**: 🎯 実戦判定 · 💰 資金管理
- **終わったら**: 📈 収支検証 → 📊 検証レポート
- **データ優先**: 有効100→300→1000件でAI信頼度が上がります

詳細は README の「毎日の運用手順」を参照してください。
            """
        )
        st.caption("※ 判断補助ツールです。自動購入ではありません。")

with tab_rec, safe_page("今日のAIおすすめ"):
    if not prompt_load_bundles_if_needed("今日のAIおすすめ", bet_type) and _full_loaded:
        st.subheader("今日のAIおすすめ")
        if recommend_bundle.get("has_data"):
            rec = recommend_bundle
            if rec["targets"]:
                for card in rec["targets"][:3]:
                    render_target_card(card)
            else:
                st.warning("本日の狙い目はありません")
            if rec["dangerous_popular"]:
                st.markdown(
                    '<div class="mobile-section">⚠ 危険な人気（要注意）</div>',
                    unsafe_allow_html=True,
                )
                for card in rec["dangerous_popular"][:3]:
                    render_danger_card(card)
        else:
            st.warning("データがありません。サイドバー → workflow 実行")

        st.divider()
        st.markdown("#### 詳細一覧")
        render_ai_recommend_block(
            recommend_bundle, score_bundle, bet_type, show_skip=True, show_table=True
        )
        with st.expander("テキストレポート"):
            st.text(lines_to_text(recommend_bundle.get("lines", [])))

with tab_battle, safe_page("実戦判定"):
    if not prompt_load_bundles_if_needed("実戦判定", bet_type) and _full_loaded:
        st.subheader("実戦判定")
        st.caption("AIスコア・学習・市場・ライン・直前・品質を総合した買い/見送り判定です。")

        bb = battle_bundle
        if not bb.get("has_data"):
            st.warning("データがありません。workflow またはデータ収集を実行してください。")
        else:
            mobile_metrics(
                [
                    ("買い候補", len(bb["buy_candidates"])),
                    ("少額候補", len(bb["small_candidates"])),
                    ("見送り", len(bb["skip_candidates"])),
                    ("危険人気", len(bb["dangerous_popular"])),
                ]
            )
            st.caption(
                f"対象日 {bb['today']} · 推奨合計 {bb.get('total_recommended_yen', 0)}円 "
                f"（1点{bb.get('base_amount', 100)}円基準）"
            )

            st.markdown("#### 本日の買い候補")
            if not bb["buy_candidates"]:
                st.info("本日の買い候補はありません。")
            else:
                for card in bb["buy_candidates"][:5]:
                    render_battle_card(card)

            st.markdown("#### 少額候補")
            if not bb["small_candidates"]:
                st.caption("少額候補なし")
            else:
                for card in bb["small_candidates"][:5]:
                    render_battle_card(card)

            col_check, col_skip = st.columns(2)
            with col_check:
                st.markdown("#### 要確認")
                if not bb["check_candidates"]:
                    st.caption("要確認なし")
                else:
                    for card in bb["check_candidates"][:3]:
                        render_battle_card(card)
            with col_skip:
                st.markdown("#### 見送り候補")
                if not bb["skip_candidates"]:
                    st.caption("見送りなし")
                else:
                    for card in bb["skip_candidates"][:5]:
                        st.caption(
                            f"{card['venue_name']} {card['race_no']}R — {card.get('battle_hint', '')}"
                        )

            st.markdown("#### 危険人気")
            if not bb["dangerous_popular"]:
                st.success("危険人気レースは検出されていません。")
            else:
                for card in bb["dangerous_popular"][:5]:
                    render_danger_card(card)

            st.markdown("#### 買ってはいけない条件")
            rules = bb.get("do_not_buy_rules", [])
            if rules:
                st.dataframe(
                    pd.DataFrame(rules)[["label", "desc"]],
                    use_container_width=True,
                    hide_index=True,
                )

            st.markdown("#### 資金配分目安")
            alloc_rows = []
            for verdict, guide in bb.get("allocation_guide", {}).items():
                if verdict == "見送り":
                    continue
                alloc_rows.append(
                    {
                        "判定": verdict,
                        "1レース目安": f"{guide['per_race_yen']}円",
                        "1点目安": f"{guide['per_combo_yen']}円",
                        "最大レース": guide["max_races"],
                        "予算比率": f"{guide['budget_pct']}%",
                    }
                )
            if alloc_rows:
                st.dataframe(pd.DataFrame(alloc_rows), use_container_width=True, hide_index=True)

            with st.expander("全レース一覧"):
                all_cards = bb.get("all_cards", [])
                if all_cards:
                    rows = [
                        {
                            "競輪場": c["venue_name"],
                            "R": c["race_no"],
                            "判定": c["battle_verdict"],
                            "総合": c["composite_score"],
                            "推奨円": c["recommended_yen"],
                            "理由": c.get("battle_hint", ""),
                        }
                        for c in all_cards
                    ]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            with st.expander("テキストレポート"):
                st.text(lines_to_text(bb.get("lines", [])))

        with st.expander("判定の仕組み"):
            st.markdown(
                """
**総合スコア（100点満点）** に以下を加重合成します。

| 材料 | 比重 |
|------|------|
| AIスコア | 25% |
| 予測回収率 | 20% |
| 学習補正 | 15% |
| オッズ歪み | 12% |
| ライン有利度 | 13% |
| 直前補正 | 10% |

**買ってはいけない条件** に1つでも該当すると見送りになります。  
資金配分は1点100円基準の目安です。無理のない範囲でご自身の予算に合わせてください。
                """
            )

with tab_bankroll, safe_page("資金管理"):
    if prompt_load_bundles_if_needed("資金管理", bet_type):
        pass
    elif _full_loaded:
        st.subheader("資金管理")
        st.caption(
            f"元手{DEFAULT_BANKROLL:,}円から始める前提で、AI判定に応じた購入金額を提案します。"
        )

        bk = bankroll_bundle
        cfg_initial = bk.get("initial_bankroll", DEFAULT_BANKROLL)

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            input_bankroll = st.number_input(
                "現在資金（円）",
                min_value=0,
                max_value=10_000_000,
                value=int(bk.get("current_bankroll", DEFAULT_BANKROLL)),
                step=100,
                key="bankroll_current",
            )
        with col_b:
            input_max_race = st.number_input(
                "1レース上限（円）",
                min_value=100,
                max_value=100_000,
                value=int(bk.get("max_per_race", 500)),
                step=100,
                key="bankroll_max_race",
            )
        with col_c:
            input_max_daily = st.number_input(
                "1日上限（円）",
                min_value=100,
                max_value=500_000,
                value=int(bk.get("max_daily", 1500)),
                step=100,
                key="bankroll_max_daily",
            )

        input_monthly_target = st.number_input(
            "月目標利益（円）",
            min_value=0,
            max_value=10_000_000,
            value=int(get_monthly_target()),
            step=1000,
            key="bankroll_monthly_target",
            help="ホームの月目標進捗・1日あたり必要額・攻め/守る判定に使用",
        )

        save_bank = st.button("💾 設定を保存", use_container_width=True)
        if save_bank:
            set_bankroll_config("current_bankroll", str(int(input_bankroll)))
            set_bankroll_config("max_per_race", str(int(input_max_race)))
            set_bankroll_config("max_daily", str(int(input_max_daily)))
            set_monthly_target(int(input_monthly_target))
            save_bankroll_snapshot(int(input_bankroll), bk.get("daily_used", 0), "手動更新")
            st.success("資金設定を保存しました")
            st.rerun()

        refreshed = get_bankroll_bundle(
            bet_type,
            battle_bundle=battle_bundle,
            current_bankroll=int(input_bankroll),
            max_per_race=int(input_max_race),
            max_daily=int(input_max_daily),
        )

        mobile_metrics(
            [
                ("現在資金", f"{refreshed['current_bankroll']:,}円"),
                ("本日上限", f"{refreshed['max_daily']:,}円"),
                ("推奨購入", f"{refreshed['recommended_total']:,}円"),
                ("残り資金", f"{refreshed['remaining_bankroll']:,}円"),
            ]
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("本日使用済", f"{refreshed['daily_used']:,}円")
        with c2:
            st.metric("本日残り上限", f"{refreshed['daily_limit_remaining']:,}円")
        with c3:
            st.metric(
                "連勝/連敗",
                f"{refreshed['streaks']['win_streak']}/{refreshed['streaks']['lose_streak']}",
                f"×{refreshed['streak_multiplier']}",
            )

        if refreshed.get("streak_notes"):
            st.caption(" / ".join(refreshed["streak_notes"]))

        if refreshed.get("warnings"):
            st.markdown("#### リスク警告")
            for w in refreshed["warnings"]:
                st.warning(w)

        st.markdown("#### S / A / B ランク別の金額目安")
        rank_rows = []
        for rank in ("S", "A", "B", "C"):
            rs = refreshed["rank_stakes"][rank]
            rank_rows.append(
                {
                    "ランク": rank,
                    "1レース目安": f"{rs['per_race']}円",
                    "1点目安": f"{rs['per_combo']}円",
                    "説明": rs["label"],
                }
            )
        st.dataframe(pd.DataFrame(rank_rows), use_container_width=True, hide_index=True)

        st.markdown("#### AIスコア別推奨金額")
        score_rows = [
            {"AIスコア": "80点以上", "目安": "300円/レース"},
            {"AIスコア": "65〜79点", "目安": "200円/レース"},
            {"AIスコア": "50〜64点", "目安": "100円/レース"},
            {"AIスコア": "49点以下", "目安": "0円（見送り）"},
        ]
        st.dataframe(pd.DataFrame(score_rows), use_container_width=True, hide_index=True)

        st.markdown("#### 本日の推奨購入")
        buy_today = refreshed.get("buy_today", [])
        if not buy_today:
            st.info("本日の推奨購入はありません（見送り・危険レース・上限到達）。")
        else:
            show = pd.DataFrame(
                [
                    {
                        "競輪場": a["venue_name"],
                        "R": a["race_no"],
                        "ランク": a.get("ev_rank"),
                        "判定": a.get("battle_verdict"),
                        "推奨円": a["recommended_yen"],
                        "1点": a["per_combo_yen"],
                        "理由": a.get("stake_reason"),
                    }
                    for a in buy_today
                ]
            )
            st.dataframe(show, use_container_width=True, hide_index=True)

        st.markdown("#### 全レース配分")
        all_alloc = refreshed.get("allocations", [])
        if all_alloc:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "競輪場": a["venue_name"],
                            "R": a["race_no"],
                            "危険": "⚠" if a.get("danger_popular") else "",
                            "推奨円": a["recommended_yen"],
                            "理由": a.get("stake_reason"),
                        }
                        for a in all_alloc
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 資金推移")
        fig = refreshed.get("fig_trend")
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True, key="bankroll_fig_trend")

        with st.expander("CLI出力"):
            st.text(lines_to_text(build_bankroll_lines(refreshed)))

        with st.expander("資金管理ルール"):
            st.markdown(
                f"""
    - **初期元手**: {cfg_initial:,}円
    - **1レース上限**: 資金の10%または設定上限の小さい方
    - **1日上限**: 既定1,500円（資金の30%目安）
    - **連敗2**: 70% / **連敗3**: 50% / **連敗5**: 30%に自動減額
    - **連勝3以上**: 最大+15%まで（急増しない）
    - **危険人気・見送り**: 0円

    実戦判定タブの結果と連動しています。
                """
            )

    with tab_validation, safe_page("検証レポート"):
        st.subheader("検証レポート")
        st.caption("AIおすすめ・実戦判定・資金管理の成績を日次/週次/月次で自動検証します。")

        vb = validation_bundle if isinstance(validation_bundle, dict) else empty_validation_report(bet_type)

        col_run, col_save = st.columns(2)
        with col_run:
            if st.button("🔄 検証を更新", type="primary", use_container_width=True):
                with st.spinner("検証中..."):
                    run_daily_validation(bet_type)
                st.success("検証レポートを更新しました")
                st.rerun()
        with col_save:
            if st.button("💾 レポート保存", use_container_width=True):
                path = save_validation_report(
                    bet_type,
                    battle_bundle=battle_bundle,
                    bankroll_plan=bankroll_bundle,
                )
                st.success(f"保存: {path.name}")

        def _period_metrics(period: dict | None, label: str) -> None:
            s = (period or {}).get("summary") or {}
            st.markdown(f"**{label}**")
            settled = int(s.get("settled") or 0)
            profit = int(s.get("total_profit") or 0)
            recovery = s.get("recovery_rate")
            hit = s.get("hit_rate")
            mobile_metrics(
                [
                    ("収支", f"{profit:,}円"),
                    ("回収率", f"{recovery if recovery is not None else 0}%"),
                ("的中率", f"{hit if hit is not None else 0}%"),
                ("件数", settled),
            ]
        )
        if settled == 0:
            st.caption("確定データなし")

    try:
        c1, c2, c3 = st.columns(3)
        with c1:
            _period_metrics(safe_validation_period(vb, "today"), "今日の成績")
        with c2:
            _period_metrics(safe_validation_period(vb, "week"), "今週の成績")
        with c3:
            _period_metrics(safe_validation_period(vb, "month"), "今月の成績")

        st.markdown("#### 買わなかった候補（仮想成績・今日）")
        tv = safe_validation_period(vb, "today_virtual").get("summary") or {}
        if tv.get("settled", 0) == 0:
            st.caption("仮想データなし — 検証更新で同期されます")
        else:
            st.info(
                f"仮想: 収支{tv.get('total_profit', 0):,}円 / "
                f"回収{tv.get('recovery_rate', 0)}% / "
                f"的中{tv.get('hit_rate', 0)}% ({tv.get('settled', 0)}件)"
            )

        today_period = safe_validation_period(vb, "today")
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("#### AIスコア別回収率（今日）")
            score_df = today_period.get("by_ai_score", pd.DataFrame())
            if score_df is None or score_df.empty:
                st.caption("データなし")
            else:
                st.dataframe(score_df, use_container_width=True, hide_index=True)
        with col_b:
            st.markdown("#### 判定別回収率（今日）")
            verdict_df = today_period.get("by_verdict", pd.DataFrame())
            if verdict_df is None or verdict_df.empty:
                st.caption("データなし")
            else:
                st.dataframe(verdict_df, use_container_width=True, hide_index=True)

        st.markdown("#### 推奨金額別収支（今日）")
        amount_df = today_period.get("by_amount", pd.DataFrame())
        if amount_df is None or amount_df.empty:
            st.caption("データなし")
        else:
            st.dataframe(amount_df, use_container_width=True, hide_index=True)

        col_strong, col_weak = st.columns(2)
        with col_strong:
            st.markdown("#### AIが強い条件 TOP10")
            strong = vb.get("strong_conditions", pd.DataFrame())
            if strong is None or strong.empty:
                st.caption("なし")
            else:
                st.dataframe(strong, use_container_width=True, hide_index=True)
        with col_weak:
            st.markdown("#### AIが弱い条件 TOP10")
            weak = vb.get("weak_conditions", pd.DataFrame())
            if weak is None or weak.empty:
                st.caption("なし")
            else:
                st.dataframe(weak, use_container_width=True, hide_index=True)

        st.markdown("#### 次に改善すべき項目")
        for tip in vb.get("improvements") or []:
            st.warning(tip)

        hist = vb.get("history", pd.DataFrame())
        if hist is not None and not hist.empty:
            st.markdown("#### 検証履歴")
            st.dataframe(hist, use_container_width=True, hide_index=True)

        with st.expander("テキストレポート"):
            st.text(lines_to_text(vb.get("lines", [])))
    except Exception as e:
        st.warning(f"検証レポートの表示中にエラーが発生しました: {e}")
        _period_metrics(safe_validation_period(vb, "month"), "今月の成績")

    with st.expander("自動検証について"):
        st.markdown(
            """
- **毎朝6時の自動運用**（ops）完了後に検証レポートを生成
- **仮想成績** = 実購入しなかった実戦判定候補のシミュレーション
- **改善ポイント** = 回収率・判定・金額帯から自動抽出

手動で「検証を更新」を押すと最新データで再計算します。
            """
        )

with tab_improve, safe_page("改善提案"):
    st.subheader("改善提案AI")
    st.caption("検証レポートをもとに、弱い条件・強い条件・次の改善案を自動抽出します。")

    ib = improvement_bundle

    col_run, col_save = st.columns(2)
    with col_run:
        if st.button("🔄 改善提案を更新", type="primary", use_container_width=True, key="improve_refresh"):
            with st.spinner("分析中..."):
                run_daily_validation(bet_type)
            st.success("検証・改善提案を更新しました")
            st.rerun()
    with col_save:
        if st.button("💾 提案レポート保存", use_container_width=True, key="improve_save"):
            path = save_improvement_report(bet_type, bundle=ib)
            st.success(f"保存: {path.name}")

    st.caption(
        f"基準日: {ib.get('ref_date', '—')} / "
        f"有効データ率: {ib.get('quality_valid_pct', 0)}%"
    )

    col_weak, col_strong = st.columns(2)
    with col_weak:
        st.markdown("#### 今のAIの弱点")
        for w in ib.get("weaknesses") or []:
            st.error(w)
        if not ib.get("weaknesses"):
            st.caption("弱点は未検出")
    with col_strong:
        st.markdown("#### 強み")
        for s in ib.get("strengths") or []:
            st.success(s)
        if not ib.get("strengths"):
            st.caption("強みは未検出")

    st.markdown("#### 改善案 TOP5")
    top5 = ib.get("top5_proposals", pd.DataFrame())
    if top5.empty:
        st.info("改善案はまだありません — 検証データを増やしてください")
    else:
        for rank, (_, row) in enumerate(top5.iterrows(), start=1):
            st.markdown(f"**{rank}. [{row['種別']}] {row['改善案']}**")
            st.caption(row["根拠"])

    col_hyp, col_avoid = st.columns(2)
    with col_hyp:
        st.markdown("#### 次に検証する仮説")
        hypos = ib.get("hypotheses") or []
        if not hypos:
            st.caption("仮説なし")
        else:
            for h in hypos:
                st.info(f"**{h['仮説']}**")
                st.caption(f"検証: {h['検証方法']} / 期待: {h['期待']}")
    with col_avoid:
        st.markdown("#### 買い控えるべき条件")
        avoid = ib.get("avoid_conditions", pd.DataFrame())
        if avoid.empty:
            st.caption("該当なし")
        else:
            st.dataframe(
                avoid[["条件", "回収率", "レース数", "理由"]],
                use_container_width=True,
                hide_index=True,
            )

    with st.expander("詳細: 弱い条件 / 強い条件 / 追加検証"):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**弱い条件**")
            weak_df = ib.get("weak_conditions", pd.DataFrame())
            if weak_df.empty:
                st.caption("なし")
            else:
                st.dataframe(weak_df, use_container_width=True, hide_index=True)
        with c2:
            st.markdown("**強い条件**")
            strong_df = ib.get("strong_conditions", pd.DataFrame())
            if strong_df.empty:
                st.caption("なし")
            else:
                st.dataframe(strong_df, use_container_width=True, hide_index=True)
        with c3:
            st.markdown("**もっと検証すべき条件**")
            verify_df = ib.get("verify_conditions", pd.DataFrame())
            if verify_df.empty:
                st.caption("なし")
            else:
                st.dataframe(verify_df, use_container_width=True, hide_index=True)

    with st.expander("AIスコア / 資金配分 / データ収集の提案"):
        for title, key in (
            ("AIスコア改善", "score_proposals"),
            ("資金配分改善", "bankroll_proposals"),
            ("次に集めるべきデータ", "data_proposals"),
        ):
            props = ib.get(key) or []
            st.markdown(f"**{title}**")
            if not props:
                st.caption("なし")
            else:
                for p in props:
                    st.markdown(f"- {p.get('提案', '')}")
                    st.caption(p.get("根拠", ""))

    with st.expander("テキストレポート"):
        st.text(lines_to_text(ib.get("lines", [])))

with t_notify, safe_page("通知"):
    st.subheader("通知")
    st.caption("AI高スコア・危険人気・直前急変を通知ログに記録します（LINE連携前）。")

    nb = notify_bundle
    if st.button("🔔 通知を更新", type="primary", use_container_width=True):
        with st.spinner("通知候補をスキャン中..."):
            fresh = get_notification_bundle(
                bet_type,
                scores=score_bundle["scores"],
                recommend=recommend_bundle,
                pre_race=pre_race_bundle,
                market=market_bundle,
                persist=True,
            )
        st.success(f"更新完了（候補 {fresh['candidate_count']} 件 / 新規記録 {fresh['saved_count']} 件）")
        st.rerun()

    mobile_metrics(
        [
            ("本日候補", nb["candidate_count"]),
            ("高期待値", len(nb["high_score"])),
            ("危険人気", len(nb["danger_popular"])),
            ("急変", len(nb["odds_surge"])),
        ]
    )
    if nb.get("saved_count"):
        st.caption(f"新規 {nb['saved_count']} 件をDBに記録しました")

    st.markdown("#### 本日の通知候補")
    all_cand = candidates_to_frame(nb["candidates"])
    if all_cand.empty:
        st.info("本日の通知候補はありません。")
    else:
        st.dataframe(all_cand, use_container_width=True, hide_index=True)

    st.markdown("#### 高期待値（AIスコア80以上）")
    high_df = candidates_to_frame(nb["high_score"])
    if high_df.empty:
        st.caption("該当なし")
    else:
        for c in nb["high_score"]:
            st.markdown(
                f'<div class="mobile-card mobile-card-target">'
                f'<div class="mobile-card-title">{c["title"]}</div>'
                f'<div class="mobile-meta">{c["message"]}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("#### 危険人気")
    if not nb["danger_popular"]:
        st.caption("該当なし")
    else:
        for c in nb["danger_popular"]:
            st.markdown(
                f'<div class="mobile-card mobile-card-danger">'
                f'<div class="mobile-card-title">{c["title"]}</div>'
                f'<div class="mobile-meta">{c["message"]}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("#### 急変アラート")
    if not nb["odds_surge"]:
        st.caption("該当なし（直前/T-30記録またはオッズ2回取得が必要）")
    else:
        surge_df = candidates_to_frame(nb["odds_surge"])
        st.dataframe(surge_df, use_container_width=True, hide_index=True)

    st.markdown("#### 通知済み履歴")
    hist = nb.get("history", pd.DataFrame())
    if hist.empty:
        st.info("履歴がありません。")
    else:
        type_map = {
            "high_score": "高期待値",
            "danger_popular": "危険人気",
            "odds_surge": "急変",
        }
        hist_show = hist.copy()
        hist_show["種別"] = hist_show["notify_type"].map(type_map).fillna(hist_show["notify_type"])
        st.dataframe(
            hist_show[
                ["notified_at", "種別", "title", "message", "severity", "score_value", "notify_date"]
            ].rename(
                columns={
                    "notified_at": "通知日時",
                    "title": "タイトル",
                    "message": "内容",
                    "severity": "重要度",
                    "score_value": "値",
                    "notify_date": "開催日",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("テキストレポート"):
        st.text(lines_to_text(build_notify_lines(bet_type)))

with t_analyze, safe_page("分析"):
    st.subheader("市場偏り分析")
    if "分析対象がありません" in analyze_text:
        st.warning("データがありません。左の「workflow 実行」を押してください。")
    st.text(analyze_text)

with t_ai, safe_page("AI指標"):
    st.subheader("AI予測強化指標")
    overall = ai_bundle["overall"]
    metrics_df = ai_bundle["metrics"]

    if not overall:
        st.warning("データがありません。workflow を実行してください。")
    else:
        mobile_metrics(
            [
                ("レース数", overall.get("races", 0)),
                ("逃げ(平均)", f"{overall.get('avg_nige_count', 0)}名"),
                ("人気集中", f"{overall.get('avg_ninki_concentration', 0)}%"),
                ("荒れ指数", overall.get("avg_are_index", 0)),
                ("本命決着", f"{overall.get('honmei_settle_rate', 0)}%"),
                ("万車券", f"{overall.get('man_ticket_rate', 0)}%"),
            ]
        )

        st.markdown("##### 競輪場別傾向")
        st.dataframe(ai_bundle["venue_trends"], use_container_width=True, hide_index=True)

        st.markdown("##### ライン本数別 回収率")
        st.dataframe(ai_bundle["recovery_line"], use_container_width=True, hide_index=True)
        st.markdown("##### 逃げ人数別 回収率")
        st.dataframe(ai_bundle["recovery_nige"], use_container_width=True, hide_index=True)
        st.markdown("##### 人気集中率帯別 回収率")
        st.dataframe(ai_bundle["recovery_ninki"], use_container_width=True, hide_index=True)
        st.markdown("##### 荒れ指数帯別 回収率")
        st.dataframe(ai_bundle["recovery_are"], use_container_width=True, hide_index=True)

        st.markdown("##### レース別（ライン・逃げ・指標）")
        if not metrics_df.empty:
            show = metrics_df[
                [
                    "race_id",
                    "venue_name",
                    "race_no",
                    "line_info",
                    "line_count",
                    "nige_count",
                    "ninki_concentration",
                    "are_index",
                    "honmei_settle",
                    "man_ticket",
                    "trifecta_pay",
                ]
            ]
            st.dataframe(show, use_container_width=True, hide_index=True)

        with st.expander("指標の説明"):
            st.markdown(
                """
- **ライン情報**: netkeirin 並び予想 API（0=ライン区切り）
- **逃げ人数**: 出走表の脚質「逃」の人数
- **人気集中率**: 1番人気組み合わせの暗黙確率シェア(%)
- **荒れ指数**: オッズ分散が高いほど上昇（0〜100）
- **本命決着**: 3連単1番人気が的中したか
- **万車券**: 3連単払戻1万円以上、または的中オッズ100倍以上
                """
            )

        st.text(lines_to_text(ai_bundle["lines"]))

with t_ml, safe_page("ML予測"):
    st.subheader("予測AI（XGBoost）")
    st.caption("過去データから回収率・期待値を機械学習で予測します。")

    mb = ml_bundle
    meta = mb.get("meta") or {}

    col_train, col_info = st.columns([1, 2])
    with col_train:
        train_btn = st.button("🤖 モデルを再学習", type="primary", use_container_width=True)
    with col_info:
        if mb["has_model"]:
            st.caption(
                f"学習: {meta.get('trained_at', '—')} · "
                f"件数 {meta.get('n_train', 0)} · CV R²={meta.get('cv_r2', '—')}"
            )
        else:
            st.caption(f"結果あり {mb['n_labeled_races']} レース（学習最低 {mb['min_train_races']}）")

    if train_btn:
        if not mb["can_train"]:
            st.error(
                f"学習には結果付き {mb['min_train_races']} レース以上が必要です。"
            )
        else:
            with st.spinner("XGBoost 学習中..."):
                result = train_ml_model(bet_type, scores=score_bundle["scores"])
            if result.get("ok"):
                st.success(f"学習完了: {result['n_train']} レース")
                st.rerun()
            else:
                st.error(result.get("error", "学習失敗"))

    if not mb["has_model"]:
        st.warning(
            "モデルが未学習です。上の **モデルを再学習** を押すか、"
            "`python main.py ml --train` を実行してください。"
        )
    else:
        mobile_metrics(
            [
                ("学習件数", meta.get("n_train", 0)),
                ("CV R²", meta.get("cv_r2", "—")),
                ("CV RMSE", meta.get("cv_rmse", "—")),
                ("予測", len(mb["predictions"])),
            ]
        )

        pred = mb["predictions"]
        if pred.empty:
            st.info("予測対象レースがありません。")
        else:
            st.markdown("#### レース別予測")
            show_pred = pred[
                [
                    c
                    for c in [
                        "venue_name",
                        "race_no",
                        "ai_total_score",
                        "pred_recovery",
                        "pred_confidence",
                        "pred_ev_rank",
                        "pred_ev",
                        "actual_recovery",
                    ]
                    if c in pred.columns
                ]
            ].rename(
                columns={
                    "venue_name": "競輪場",
                    "race_no": "R",
                    "ai_total_score": "AIスコア",
                    "pred_recovery": "予測回収率%",
                    "pred_confidence": "信頼度",
                    "pred_ev_rank": "期待値ランク",
                    "pred_ev": "期待値",
                    "actual_recovery": "実績回収%",
                }
            )
            st.dataframe(show_pred, use_container_width=True, hide_index=True)

        st.markdown("#### 重要特徴量 TOP10")
        imp = mb["feature_importance"]
        if imp.empty:
            st.caption("特徴量重要度なし")
        else:
            label_map = {
                "venue_name_enc": "競輪場",
                "race_style_enc": "脚質構成",
                "dominant_style_enc": "脚質",
                "line_bucket_enc": "ライン構成",
                "ninki_bucket_enc": "人気集中帯",
                "popularity_label_enc": "人気帯",
                "line_count": "ライン本数",
                "nige_count": "逃げ人数",
                "senko_count": "先行人数",
                "ninki_concentration": "人気集中率",
                "are_index": "荒れ指数",
                "fav_odds": "本命オッズ",
                "avg_odds": "平均オッズ",
                "median_odds": "中央オッズ",
                "ai_total_score": "AIスコア",
                "danger_level": "危険度",
                "honmei_trust": "本命信頼度",
                "are_forecast": "荒れ予想",
            }
            imp_show = imp.head(10).copy()
            imp_show["label"] = imp_show["feature"].map(
                lambda x: label_map.get(x, x)
            )
            st.bar_chart(
                imp_show.set_index("label")["importance"],
                use_container_width=True,
            )
            st.dataframe(
                imp_show[["label", "feature", "importance"]].rename(
                    columns={"label": "項目", "feature": "内部名", "importance": "重要度"}
                ),
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("モデル説明"):
            st.markdown(
                """
**学習対象（特徴量）**
- 競輪場 / 脚質 / ライン構成 / 人気帯 / オッズ / AIスコア ほか

**出力**
- **予測回収率** — 全組み合わせ100円購入シミュレーションの回収率(%)
- **期待値** — 予測回収率 × 信頼度 / 100
- **信頼度** — CVスコア・学習件数・予測値の安定性から算出
- **期待値ランク** — S/A/B/C/D（回収率基準）

※ 参考指標です。過学習に注意し、データが増えるほど精度が向上します。
                """
            )

        with st.expander("テキストレポート"):
            st.text(lines_to_text(build_ml_lines(bet_type)))

with t_learn, safe_page("パターン学習"):
    st.subheader("学習状況")
    st.caption("過去結果から勝ちパターンを学習し、AIスコアへ加点/減点します。")

    refresh_learn = st.button("🔄 学習を再実行", use_container_width=True)
    if refresh_learn:
        with st.spinner("学習中..."):
            save_learned_patterns(bet_type)
            learning_bundle = get_learning_bundle(bet_type, refresh=False)
        st.success("学習を更新しました")
        st.rerun()

    lb = learning_bundle
    if not lb["has_data"]:
        st.warning(
            "学習データがありません。結果付きで workflow を実行してください（--with-result）。"
        )
    else:
        mobile_metrics(
            [
                ("学習条件", lb["learning_count"]),
                ("結果あり", lb["result_races"]),
                ("更新", lb["updated_at"][:16] if lb["updated_at"] else "—"),
            ]
        )

        st.markdown("#### 高回収条件 TOP10")
        high = lb["high_recovery_top10"]
        if high.empty:
            st.info("回収率100%以上の条件はありません。")
        else:
            st.dataframe(
                high[
                    [
                        "condition_label",
                        "category",
                        "races",
                        "recovery_rate",
                        "hit_rate",
                        "score_adjust",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 低回収条件 TOP10")
        low = lb["low_recovery_top10"]
        if low.empty:
            st.info("回収率75%以下の条件はありません。")
        else:
            st.dataframe(
                low[
                    [
                        "condition_label",
                        "category",
                        "races",
                        "recovery_rate",
                        "hit_rate",
                        "score_adjust",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 競輪場別成績")
        venue_perf = lb["venue_performance"]
        if venue_perf.empty:
            st.caption("データなし")
        else:
            st.dataframe(
                venue_perf[
                    [
                        "condition_label",
                        "races",
                        "recovery_rate",
                        "hit_rate",
                        "score_adjust",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### AIスコアに反映された学習ポイント")
        applied_summary = learning_applied_summary(score_bundle["scores"])
        applied_df = build_learning_applied_frame(score_bundle["scores"])
        mobile_metrics(
            [
                ("反映あり", applied_summary["applied_races"]),
                ("加点", applied_summary["plus_races"]),
                ("減点", applied_summary["minus_races"]),
                ("平均pt", applied_summary["avg_adjust"]),
            ]
        )
        if applied_df.empty:
            st.info("学習ポイントの反映はありません（条件不一致または加点0）。")
        else:
            show_applied = applied_df[
                [c for c in ["競輪場", "R", "AIスコア", "学習pt", "ランク", "反映理由"] if c in applied_df.columns]
            ].copy()
            st.dataframe(show_applied, use_container_width=True, hide_index=True)

        with st.expander("学習カテゴリ"):
            st.markdown(
                """
| カテゴリ | 内容 |
|---------|------|
| venue | 競輪場別 |
| style | 1着脚質 |
| race_style | 脚質構成（逃2名以上 等） |
| line | ライン本数 |
| ninki | 人気集中率帯 |
| popularity | 人気帯（1番人気 等） |

回収率120%以上 → +10点 / 100%以上 → +6 / 90%以上 → +3  
40%以下 → -10 / 55%以下 → -6 / 75%以下 → -3（AIスコアへ反映、合計±12点上限）
                """
            )

        with st.expander("テキストレポート"):
            st.text(lines_to_text(build_learning_lines(bet_type)))

with t_advanced, safe_page("本格学習"):
    st.subheader("本格学習")
    st.caption("データ品質チェック済みの有効レースのみで学習し、AIスコア重みを自動調整します。")

    ab = advanced_bundle
    if not ab.get("can_train"):
        st.warning(
            f"有効レースが不足しています（{ab['n_valid_races']} / 必要 {ab['min_valid_races']}）。"
            " データ収集と品質チェックを先に実行してください。"
        )
    elif not ab.get("has_data"):
        st.info("未学習です。下のボタンで本格学習を実行してください。")

    retrain_adv = st.button("▶ 再学習を実行", type="primary", use_container_width=True)
    if retrain_adv:
        with st.spinner("本格学習中（有効データのみ）..."):
            result = run_advanced_learning(bet_type)
        if result.get("ok"):
            st.success(
                f"完了: {result['n_valid_races']}レース / "
                f"前{result['before_recovery']}% → 後{result['after_predicted_recovery']}%"
            )
        else:
            st.error(result.get("error", "学習に失敗しました"))
        st.rerun()

    if ab.get("has_data"):
        mobile_metrics(
            [
                ("学習データ", f"{ab['n_valid_races']}R"),
                ("学習前", f"{ab['before_recovery']}%"),
                ("学習後", f"{ab['after_predicted_recovery']}%"),
                ("除外条件", ab.get("excluded_count", 0)),
            ]
        )

        c1, c2 = st.columns(2)
        with c1:
            st.metric("学習前回収率", f"{ab['before_recovery']}%")
        with c2:
            st.metric("学習後予測回収率", f"{ab['after_predicted_recovery']}%")

        st.caption(
            f"スコア相関: {ab.get('score_correlation_before')} → {ab.get('score_correlation_after')} · "
            f"更新: {(ab.get('trained_at') or '—')[:16]}"
        )

        st.markdown("#### 重要特徴量")
        imp = ab.get("feature_importance", pd.DataFrame())
        if imp.empty:
            st.caption("特徴量データなし")
        else:
            st.dataframe(
                imp[["feature", "importance", "avg_recovery", "patterns"]],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### AIスコア重み")
        weights = ab.get("weights", {})
        if weights:
            wdf = pd.DataFrame(
                [{"component": k, "weight": v} for k, v in weights.items()]
            )
            st.dataframe(wdf, use_container_width=True, hide_index=True)

        st.markdown("#### 高回収条件 TOP10")
        high = ab.get("high_recovery_top10", pd.DataFrame())
        if high.empty:
            st.info("回収率100%以上の条件はありません。")
        else:
            st.dataframe(
                high[
                    [
                        "condition_label",
                        "category",
                        "races",
                        "recovery_rate",
                        "hit_rate",
                        "score_adjust",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 低回収条件 TOP10（自動除外）")
        low = ab.get("low_recovery_top10", pd.DataFrame())
        if low.empty:
            st.info("回収率75%以下の条件はありません。")
        else:
            st.dataframe(
                low[
                    [
                        "condition_label",
                        "category",
                        "races",
                        "recovery_rate",
                        "hit_rate",
                        "score_adjust",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("CLI出力"):
            st.text(lines_to_text(build_advanced_learning_lines(bet_type)))

    with st.expander("本格学習の仕組み"):
        st.markdown(
            """
- **有効レースのみ** — データ品質タブで OK 判定されたレースだけ使用
- **特徴量別回収率** — 競輪場・脚質・ライン・人気集中・荒れ指数など
- **重み自動調整** — 各スコア要素と回収率の相関から倍率を学習
- **高回収条件** — 100%以上を AI スコア加点に反映
- **低回収条件** — 75%以下は自動除外（減点のみ）
- **成績比較** — 学習前後の TOP1 買い目回収率をバックテスト
- **モデル保存** — `data/models/advanced_*.json` に保存
            """
        )

with t_ops, safe_page("自動運用"):
    st.subheader("運用状況（自動運用モード）")
    st.caption("毎朝6時に自動実行 · 取得 → 分析 → 学習 → レポート → AIおすすめ → 通知")

    os_ = ops_status
    auto_on = st.toggle(
        "朝6時の自動実行",
        value=os_["auto_enabled"],
        help="Streamlit起動中に毎朝6:00〜6:05に1回実行します",
    )
    if auto_on != os_["auto_enabled"]:
        set_ops_config("auto_enabled", "1" if auto_on else "0")
        st.rerun()

    mobile_metrics(
        [
            ("最終実行", (os_["last_started_at"] or "—")[:16]),
            ("取得", os_["races_fetched"]),
            ("学習", os_["learning_count"]),
            ("狙い目", os_["targets_count"]),
        ]
    )
    st.caption(
        f"スケジュール: {os_['schedule_label']} · "
        f"状態: {os_['last_status']} · "
        f"完了: {(os_['last_finished_at'] or '—')[:16]}"
    )

    run_all = st.button("▶ 今日の自動実行", type="primary", use_container_width=True)
    if run_all:
        venue = venue_code.strip() or None
        with st.spinner("全処理実行中（数分かかります）..."):
            result = run_daily_auto(
                bet_type,
                limit=int(limit),
                with_result=with_result,
                venue_code=venue,
                trigger="manual",
            )
        st.session_state["home_ops_result"] = result
        if result["ok"]:
            st.success("全処理が完了しました")
        else:
            st.error(f"エラー: {result.get('error_message', '不明')}")
        tr = result.get("today_results") or {}
        if tr:
            mobile_metrics(
                [
                    ("取得", result.get("races_fetched", 0)),
                    ("AIおすすめ", tr.get("targets_count", 0)),
                    ("危険人気", tr.get("danger_count", 0)),
                    ("通知", tr.get("notify_count", 0)),
                ]
            )
        with st.expander("処理ログ", expanded=not result.get("ok")):
            st.text(result.get("log_text", ""))
        st.rerun()

    st.markdown("#### 実行履歴")
    runs = os_.get("runs", pd.DataFrame())
    if runs.empty:
        st.info("まだ実行履歴がありません。")
    else:
        show_runs = runs[
            [
                "started_at",
                "finished_at",
                "status",
                "trigger_type",
                "races_fetched",
                "learning_count",
                "targets_count",
                "pre_race_count",
            ]
        ].head(15)
        st.dataframe(show_runs, use_container_width=True, hide_index=True)

    st.markdown("#### エラー履歴")
    errors = os_.get("errors", pd.DataFrame())
    if errors.empty:
        st.success("直近のエラーはありません")
    else:
        st.dataframe(
            errors[
                ["started_at", "trigger_type", "error_message", "log_path"]
            ],
            use_container_width=True,
            hide_index=True,
        )

    if os_.get("latest_log_text"):
        with st.expander("最新処理ログ"):
            st.text(os_["latest_log_text"])
            if os_.get("latest_log_path"):
                st.caption(f"保存先: {os_['latest_log_path']}")

    with st.expander("自動運用の内容"):
        st.markdown(
            """
1. **レース取得** — workflow と同様（出走表・オッズ・結果）
2. **学習データ更新** — 過去結果からパターン再学習
3. **直前分析更新** — 発走前レースの T-30/T-10/T-0 記録
4. **レポート保存** — `data/report_latest.txt` 更新
5. **AIおすすめ更新** — 狙い目・直前補正スコア反映

ログは `data/ops/logs/` に保存されます。

**常時自動実行（PC常時起動）:**
```powershell
python main.py ops --daemon
```
            """
        )

with t_collect, safe_page("100レース収集"):
    st.subheader("データ収集（100レースモード）")
    st.caption("複数日・複数開催をまとめて取得し、学習・レポートを自動更新します。")

    cb = collect_bundle
    mobile_metrics(
        [
            ("保存", f"{cb['saved_races']}R"),
            ("目標100まで", f"あと{cb['remaining_to_target']}"),
            ("達成率", f"{cb['progress_pct']:.0f}%"),
            ("全登録", cb["saved_races_all"]),
        ]
    )

    st.progress(min(1.0, cb["saved_races"] / TARGET_RACES))

    col1, col2, col3 = st.columns(3)
    with col1:
        collect_start = st.date_input(
            "開始日",
            value=date.today() - timedelta(days=14),
            key="collect_start",
        )
    with col2:
        collect_end = st.date_input(
            "終了日",
            value=date.today(),
            key="collect_end",
        )
    with col3:
        per_day = st.number_input(
            "1日あたり件数",
            min_value=1,
            max_value=30,
            value=5,
            step=1,
        )

    with_result_collect = st.checkbox("結果・払戻も取得（推奨）", value=True)
    venue_collect = st.text_input("場コード（任意）", value="", placeholder="例: 56")
    target_races = st.number_input(
        "目標レース数",
        min_value=10,
        max_value=500,
        value=TARGET_RACES,
        step=10,
    )

    run_collect = st.button("▶ 収集を実行", type="primary", use_container_width=True)

    if run_collect:
        progress_bar = st.progress(0.0)
        status_box = st.empty()
        log_box = st.empty()

        def on_progress(p: dict) -> None:
            progress_bar.progress(min(1.0, float(p.get("progress_pct") or 0)))
            status_box.info(
                f"{p.get('message', '')} | "
                f"新規{p['fetched_new']} / スキップ{p['skipped_dup']} / "
                f"エラー{p['error_count']} | 保存{p['saved_races']}R"
            )

        with st.spinner("収集中（数分〜数十分かかります）..."):
            result = fetch_bulk(
                collect_start.strftime("%Y%m%d"),
                collect_end.strftime("%Y%m%d"),
                per_day_limit=int(per_day),
                with_result=with_result_collect,
                venue_code=venue_collect.strip() or None,
                target_races=int(target_races),
                skip_existing=True,
                run_post=True,
                bet_type=bet_type,
                progress_callback=on_progress,
            )

        progress_bar.progress(1.0)
        if result["ok"]:
            st.success(
                f"完了: 新規{result['fetched_new']}件 / "
                f"保存{result['saved_races']}R / "
                f"あと{result['remaining_to_target']}件で目標"
            )
        log_box.text_area(
            "取得ログ",
            value=result.get("log_text", ""),
            height=300,
            label_visibility="collapsed",
        )
        st.rerun()

    st.markdown("#### 収集履歴")
    runs = cb.get("runs", pd.DataFrame())
    if runs.empty:
        st.caption("まだ収集履歴がありません")
    else:
        st.dataframe(
            runs[
                [
                    "started_at",
                    "start_date",
                    "end_date",
                    "per_day_limit",
                    "fetched_new",
                    "skipped_dup",
                    "error_count",
                    "status",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("#### 取得エラー履歴")
    errors = cb.get("errors", pd.DataFrame())
    if errors.empty:
        st.caption("エラー履歴なし")
    else:
        st.dataframe(errors, use_container_width=True, hide_index=True)

    with st.expander("使い方"):
        st.markdown(
            """
- **開始日〜終了日** の各開催日を順に取得します
- **1日あたり件数** でその日の上限を指定（API負荷に注意）
- **重複レース**（結果済み）は自動スキップ
- 目標レース数に達すると自動停止
- 取得後に **学習・レポート** を自動更新

過去日を指定する場合は **結果・払戻も取得** をONにしてください。
            """
        )

with t_quality, safe_page("データ品質"):
    st.subheader("データ品質")
    st.caption("保存済みレースの整合性を確認し、学習に使えるデータを判定します。")

    qb = quality_bundle
    if not qb.get("has_data"):
        st.warning("レースデータがありません。データ収集または workflow を実行してください。")
    else:
        mobile_metrics(
            [
                ("総レース", qb["total_races"]),
                ("有効", qb["valid_races"]),
                ("欠損", qb["missing_count"]),
                ("重複", qb["duplicate_count"]),
            ]
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("品質スコア", f"{qb['quality_score']}/100")
        with c2:
            st.metric("学習可", f"{qb['valid_races']}R ({qb['valid_pct']}%)")
        with c3:
            st.metric("除外", f"{qb['invalid_races']}R")

        st.progress(min(1.0, qb["quality_score"] / 100.0))

        st.markdown("#### チェック内訳")
        summary = qb.get("summary_by_issue", pd.DataFrame())
        if not summary.empty:
            st.dataframe(summary, use_container_width=True, hide_index=True)

        dup_groups = qb.get("duplicate_groups", pd.DataFrame())
        if not dup_groups.empty:
            st.markdown("#### 重複グループ")
            st.dataframe(dup_groups, use_container_width=True, hide_index=True)

        st.markdown("#### 修正候補")
        fixes = qb.get("fix_candidates", pd.DataFrame())
        if fixes.empty:
            st.success("問題のあるレースはありません。すべて学習に使えます。")
        else:
            st.dataframe(fixes, use_container_width=True, hide_index=True)

        st.markdown("#### 除外レース一覧")
        excluded = qb.get("excluded", pd.DataFrame())
        if excluded.empty:
            st.caption("除外レースなし")
        else:
            show_cols = [
                c
                for c in [
                    "race_date",
                    "venue_name",
                    "race_no",
                    "entry_count",
                    "bet_odds_count",
                    "issue_text",
                    "quality_score",
                ]
                if c in excluded.columns
            ]
            rename = {
                "race_date": "日付",
                "venue_name": "競輪場",
                "race_no": "R",
                "entry_count": "出走",
                "bet_odds_count": "オッズ数",
                "issue_text": "問題",
                "quality_score": "品質",
            }
            st.dataframe(
                excluded[show_cols].rename(columns=rename),
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("CLI出力"):
            st.text(lines_to_text(build_quality_lines(bet_type)))

    with st.expander("判定基準"):
        st.markdown(
            """
- **重複**: 同一日・場・Rで複数 race_id
- **欠損**: 日付/場/出走表/選手名の不足
- **オッズなし**: 指定券種のオッズが10件未満
- **結果なし**: 着順未登録または3着未満
- **異常値**: オッズ範囲外・着順不正・勝ち組未登録・払戻欠損
- **学習可**: 上記すべてクリア

修正は `python main.py fetch RACE_ID --with-result` またはデータ収集タブから再取得してください。
            """
        )

with tab_pnl, safe_page("収支検証"):
    st.subheader("収支検証")
    st.caption("AIおすすめの購入記録と、買わなかった候補の仮想成績を検証します。")

    pb = pnl_bundle
    sa = pb["summary_actual"]
    sv = pb["summary_virtual"]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 実購入")
        mobile_metrics(
            [
                ("総収支", f"{sa['total_profit']:,}円"),
                ("回収率", f"{sa['recovery_rate']}%"),
                ("的中率", f"{sa['hit_rate']}%"),
                ("確定", sa["settled"]),
            ]
        )
    with c2:
        st.markdown("##### 仮想購入（未購入候補）")
        mobile_metrics(
            [
                ("総収支", f"{sv['total_profit']:,}円"),
                ("回収率", f"{sv['recovery_rate']}%"),
                ("的中率", f"{sv['hit_rate']}%"),
                ("確定", sv["settled"]),
            ]
        )

    st.markdown("#### 購入記録")
    rec_col1, rec_col2, rec_col3 = st.columns(3)
    with rec_col1:
        if st.button("⭐ 狙い目TOP1を記録", use_container_width=True):
            n = add_bets_from_cards(
                recommend_bundle.get("targets") or [],
                bet_type,
                bet_amount=100,
                pick_ranks=(1,),
            )
            st.success(f"{n} 件記録しました")
            st.rerun()
    with rec_col2:
        if st.button("🔄 結果を反映", use_container_width=True):
            n = settle_pending_bets(bet_type)
            st.success(f"{n} 件を確定しました")
            st.rerun()
    with rec_col3:
        if st.button("👻 仮想成績を更新", use_container_width=True):
            n = sync_virtual_bets(recommend_bundle, bet_type)
            settle_pending_bets(bet_type)
            st.success(f"仮想 {n} 件を同期しました")
            st.rerun()

    with st.expander("手動で購入記録", expanded=False):
        scores_df = score_bundle.get("scores", pd.DataFrame())
        if scores_df.empty:
            st.caption("レースデータがありません")
        else:
            opts = {
                f"{r['venue_name']} {r['race_no']}R — {r['race_id']}": r
                for _, r in scores_df.iterrows()
            }
            sel = st.selectbox("レース", list(opts.keys()))
            row = opts[sel]
            combo = st.text_input(
                "買い目",
                value=str(row.get("pick1_combo") or ""),
                placeholder="例: 1-3-5",
            )
            amount = st.number_input("購入金額（円）", min_value=100, value=100, step=100)
            if st.button("記録する", use_container_width=True):
                if not combo.strip():
                    st.error("買い目を入力してください")
                else:
                    res = add_bet_record(
                        race_id=str(row["race_id"]),
                        bet_type=bet_type,
                        combination=combo.strip(),
                        bet_amount=int(amount),
                        race_date=str(row.get("race_date") or ""),
                        venue_name=str(row.get("venue_name") or ""),
                        race_no=int(row.get("race_no") or 0),
                        ai_score=float(
                            row.get("pre_race_score") or row.get("ai_total_score") or 0
                        ),
                        ev_rank=str(row.get("ev_rank") or ""),
                        odds=float(row["pick1_odds"])
                        if pd.notna(row.get("pick1_odds"))
                        else None,
                        note="手動",
                    )
                    if res.get("ok"):
                        st.success("記録しました")
                        st.rerun()
                    else:
                        st.error(res.get("error", "記録失敗"))

    st.markdown("#### AIスコア別回収率（実購入）")
    by_score = pb["by_ai_score_actual"]
    if by_score.empty:
        st.caption("確定データがありません")
    else:
        st.dataframe(by_score, use_container_width=True, hide_index=True)

    st.markdown("#### 期待値ランク別回収率（実購入）")
    by_rank = pb["by_rank_actual"]
    if by_rank.empty:
        st.caption("確定データがありません")
    else:
        st.dataframe(by_rank, use_container_width=True, hide_index=True)

    st.markdown("#### 買い目履歴（実購入）")
    hist_a = history_display(pb["history_actual"])
    if hist_a.empty:
        st.info("購入記録がありません。「狙い目TOP1を記録」または手動記録してください。")
    else:
        st.dataframe(hist_a, use_container_width=True, hide_index=True)

    st.markdown("#### 仮想購入成績")
    st.caption("AI候補のうち実際には買わなかった買い目のシミュレーション")
    by_score_v = pb["by_ai_score_virtual"]
    if not by_score_v.empty:
        st.dataframe(by_score_v, use_container_width=True, hide_index=True)
    hist_v = history_display(pb["history_virtual"])
    if hist_v.empty:
        st.caption("仮想記録なし。「仮想成績を更新」を実行してください。")
    else:
        st.dataframe(hist_v, use_container_width=True, hide_index=True)

    with st.expander("テキストレポート"):
        st.text(lines_to_text(build_pnl_lines(bet_type)))

with tab_line, safe_page("ライン分析"):
    st.subheader("ライン分析")
    st.caption("並び予想API・出走表からライン特徴量を算出（判断補助）")

    if not line_bundle["has_data"]:
        st.warning("データがありません。workflow で出走表・ラインを取得してください。")
    else:
        sel_races = line_bundle["race_reports"]
        race_options = {
            f"{r['venue_name']} {r['race_no']}R — {r['line_info']}": r
            for r in sel_races
        }
        pick = st.selectbox("レースを選択", list(race_options.keys()))
        rep = race_options[pick]

        mobile_metrics(
            [
                ("ライン数", rep["line_count"]),
                ("最長", rep["max_line_length"]),
                ("先行", rep["senko_line_count"]),
                ("単騎", rep["solo_count"]),
                ("自力", rep["total_jiriki"]),
                ("地区連携", rep["region_link_lines"]),
            ]
        )

        st.markdown("#### ライン構成")
        for ln in rep["lines"]:
            ln_display = {**ln, "venue_name": rep["venue_name"], "race_no": rep["race_no"]}
            render_line_card(ln_display)

        st.markdown("#### 有利ライン")
        if not rep["advantageous"]:
            st.caption("該当なし")
        else:
            for ln in rep["advantageous"]:
                ln_d = {**ln, "venue_name": rep["venue_name"], "race_no": rep["race_no"]}
                render_line_card(ln_d, kind="advantage")

        st.markdown("#### 危険ライン")
        if not rep["dangerous"]:
            st.caption("該当なし")
        else:
            for ln in rep["dangerous"]:
                ln_d = {**ln, "venue_name": rep["venue_name"], "race_no": rep["race_no"]}
                render_line_card(ln_d, kind="danger")

        st.markdown("#### 単騎警戒")
        if not rep["solo_alerts"]:
            st.success("単騎なし")
        else:
            for s in rep["solo_alerts"]:
                st.warning(
                    f"**{s['venue_name']} {s['race_no']}R** "
                    f"車番{s['bracket']} {s['racer_name']}（{s['style']}）— {s['alert']}"
                )

        st.markdown("#### 番手期待値")
        if not rep["ban_te"]:
            st.caption("2名以上のラインがありません")
        else:
            ban_df = pd.DataFrame(rep["ban_te"])
            show_ban = [
                "line_no",
                "line_label",
                "ban_bracket",
                "ban_racer",
                "ban_style",
                "ban_advantage",
                "ban_expect_score",
                "line_ai_score",
            ]
            st.dataframe(
                ban_df[show_ban],
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("全レース・全ライン一覧"):
            st.dataframe(
                line_bundle["lines_df"],
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("特徴量の見方"):
            st.markdown(
                """
| 特徴量 | 意味 |
|--------|------|
| ライン長 | ライン内の人数（長いほど団結力） |
| 番手有利 | 2番手が捲/両/逃など先行・自在脚質 |
| 単騎 | 1人ライン。消耗・挟まれに警戒 |
| 先行ライン数 | 逃・捲を含むラインの本数 |
| 自力型人数 | 逃・捲・両、またはS級選手 |
| 地区連携 | 同一地区が2名以上同ライン |
| ライン別AIスコア | 上記を加点した0〜100 |
| 番手期待値 | 番手選手の狙い目度合い（ラインスコア連動） |
                """
            )

with t_chart, safe_page("グラフ"):
    st.subheader("グラフ（Plotly）")
    if not charts_bundle["has_data"]:
        st.warning(
            "グラフ用データがありません。"
            "結果付きで workflow を実行してください（`--with-result`）。"
        )
    else:
        min_score = st.slider(
            "高スコアの閾値",
            min_value=50,
            max_value=90,
            value=HIGH_SCORE_DEFAULT,
            step=5,
        )
        if min_score != charts_bundle["min_score"]:
            refreshed = safe_call(
                "グラフ再生成",
                get_charts_bundle,
                bet_type,
                min_score=min_score,
            )
            if refreshed:
                charts_bundle = refreshed

        safe_plotly_chart(
            charts_bundle["fig_recovery_trend"],
            key="charts_fig_recovery_trend",
            label="回収率推移",
        )
        safe_plotly_chart(
            charts_bundle["fig_hit_rate"],
            key="charts_fig_hit_rate",
            label="的中率推移",
        )
        if not charts_bundle["by_date"].empty and len(charts_bundle["by_date"]) > 1:
            safe_plotly_chart(
                charts_bundle["fig_recovery_by_date"],
                key="charts_fig_recovery_by_date",
                label="開催日別回収率",
            )
        safe_plotly_chart(
            charts_bundle["fig_venue_ranking"],
            key="charts_fig_venue_ranking",
            label="競輪場別回収率",
        )
        safe_plotly_chart(
            charts_bundle["fig_ai_distribution"],
            key="charts_fig_ai_distribution",
            label="AIスコア分布",
        )
        safe_plotly_chart(
            charts_bundle["fig_score_scatter"],
            key="charts_fig_score_scatter",
            label="スコア×回収率",
        )

        st.markdown(f"##### 高スコアレース（≥ {min_score}）")
        high_df = charts_bundle["high_score_races"]
        if high_df.empty:
            st.info(f"スコア {min_score} 以上のレースはありません。")
        else:
            st.dataframe(high_df, use_container_width=True, hide_index=True)

with tab_market, safe_page("市場監視"):
    st.subheader("市場監視（直前の市場変化）")
    st.caption("判断補助ツールです。オッズを2回以上取得すると急変検知が有効になります。")

    poll_btn = st.button("📡 オッズ再取得", type="primary", use_container_width=True)
    poll_limit = st.number_input(
        "再取得レース数", min_value=1, max_value=50, value=20, step=1
    )

    if poll_btn:
        with st.spinner("オッズ取得中（1レース約1秒）..."):
            ids = list_race_ids_in_db(int(poll_limit))
            results = poll_odds_for_races(ids)
            ok = sum(1 for r in results if r.get("ok"))
        st.success(f"完了: {ok}/{len(results)} レース")
        st.rerun()

    mb = market_bundle

    if not mb["has_data"]:
        st.warning("オッズデータがありません。workflow を実行してください。")
    else:
        if mb["needs_poll_hint"]:
            st.info(
                "急変・異常売れを見るには **オッズ再取得** を数分おきに2回以上押してください。"
                "（スナップショットが蓄積されます）"
            )

        top_alert = (
            mb["race_alerts"].iloc[0]["market_alert_level"]
            if not mb["race_alerts"].empty
            else 0
        )
        mobile_metrics(
            [
                ("監視", mb["snapshot_races"]),
                ("2回取得+", mb["multi_snapshot_races"]),
                ("最高警戒", top_alert),
                ("急変", len(mb["sudden_ranking"])),
            ]
        )

        st.markdown("#### 急変ランキング")
        if mb["sudden_ranking"].empty:
            st.caption("前回スナップショットがないため急変なし。オッズ再取得を実行してください。")
        else:
            st.dataframe(
                mb["sudden_ranking"],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 危険人気（危険本命）")
        if mb["danger_favorites"].empty:
            st.caption("該当なし")
        else:
            st.dataframe(
                mb["danger_favorites"],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 短時間で急に売れた買い目")
        if mb["hot_sell"].empty:
            st.caption("該当なし")
        else:
            st.dataframe(
                mb["hot_sell"],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 過小評価の穴")
        if mb["undervalued_holes"].empty:
            st.caption("該当なし")
        else:
            st.dataframe(
                mb["undervalued_holes"],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 市場警戒レベル（レース別）")
        if not mb["race_alerts"].empty:
            st.dataframe(
                mb["race_alerts"],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 直前変化（人気集中率・最新オッズ）")
        if mb["changes"].empty:
            st.caption("前回スナップショットがないため変化量なし")
        else:
            last_cols = [
                "venue_name",
                "race_no",
                "combination",
                "odds_old",
                "odds_new",
                "change_pct",
                "share_delta",
                "ninki_rank_new",
                "latest_ts",
                "prev_ts",
            ]
            st.dataframe(
                mb["changes"].sort_values("change_pct", ascending=False)[last_cols].head(40),
                use_container_width=True,
                hide_index=True,
            )
        if not mb["ninki_by_race"].empty:
            st.caption("レース別 人気集中率")
            st.dataframe(
                mb["ninki_by_race"],
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("詳細（穴急上昇・歪みランキング・ヒートマップ）"):
            st.markdown("##### 穴人気急上昇")
            if mb["dark_horse_surge"].empty:
                st.caption("該当なし")
            else:
                st.dataframe(
                    mb["dark_horse_surge"],
                    use_container_width=True,
                    hide_index=True,
                )
            st.markdown("##### オッズ歪みランキング")
            st.dataframe(
                mb["distortion_ranking"],
                use_container_width=True,
                hide_index=True,
            )
            st.markdown("##### 市場ヒートマップ")
            st.plotly_chart(mb["fig_heatmap"], use_container_width=True, key="market_fig_heatmap")

        with st.expander("監視の見方"):
            st.markdown(
                """
| 項目 | 意味 |
|------|------|
| 急変ランキング | 前回取得比でオッズが大きく下落（＝売れ）した組み合わせ |
| 危険人気 | 本命への資金集中＋低オッズ |
| 過小評価の穴 | 人気薄だが投票シェアが相対的に高い（歪み） |
| 穴人気急上昇 | 6番人気以下で順位改善またはオッズ急落 |
| 市場警戒レベル | 急変・売れ・人気集中・歪みを統合した0〜100 |
| ヒートマップ | レース×組み合わせの変化率（赤＝売れ） |
                """
            )

with t_prerace, safe_page("直前分析"):
    st.subheader("直前分析（発走前モード）")
    st.caption(
        "発走30分前・10分前・直前のオッズを記録し、期待値の変化を検知します。"
    )

    pr_poll = st.button("⏱ 直前スナップショット取得", type="primary", use_container_width=True)
    if pr_poll:
        with st.spinner("発走前レースを走査中..."):
            results = poll_pre_race_due(within_hours=3)
            ok = sum(1 for r in results if r.get("ok"))
        if results:
            st.success(f"記録: {ok}/{len(results)} 件")
        else:
            st.info("記録対象がありません（発走30/10/0分前のウィンドウ外）")
        st.rerun()

    pb = pre_race_bundle
    if pb["needs_phase_hint"]:
        st.info(
            "T-30 / T-10 / T-0 の記録がまだありません。"
            "発走前に **直前スナップショット取得** を実行するか、"
            "下の手動記録を使ってください。"
        )

    phase_counts = pb.get("phase_counts") or {}
    mobile_metrics(
        [
            ("T-30", phase_counts.get("T-30", 0)),
            ("T-10", phase_counts.get("T-10", 0)),
            ("T-0", phase_counts.get("T-0", 0)),
            ("警戒MAX", (
                pb["race_alerts"]["market_alert_level"].max()
                if not pb["race_alerts"].empty
                else 0
            )),
        ]
    )

    upcoming = pb.get("upcoming_races", pd.DataFrame())
    if not upcoming.empty:
        with st.expander("手動フェーズ記録（発走前レース）", expanded=False):
            opts = {
                f"{r['venue_name']} {r['race_no']}R ({r['minutes_to_start']:.0f}分前)": r["race_id"]
                for _, r in upcoming.iterrows()
            }
            pick_race = st.selectbox("レース", list(opts.keys()))
            phase_pick = st.selectbox("フェーズ", ["T-30", "T-10", "T-0"])
            if st.button("このフェーズを記録", use_container_width=True):
                rid = opts[pick_race]
                try:
                    capture_pre_race_snapshot(str(rid), phase_pick)
                    st.success(f"記録しました: {pick_race} / {phase_pick}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    if not pb["has_data"]:
        st.warning("オッズデータがありません。workflow を実行してください。")
    else:
        st.markdown("#### 急上昇ランキング")
        if pb["surge_ranking"].empty:
            st.caption("該当なし（フェーズ記録が2段階以上必要）")
        else:
            st.dataframe(
                pb["surge_ranking"][
                    [
                        "venue_name",
                        "race_no",
                        "combination",
                        "odds_old",
                        "odds_new",
                        "change_pct",
                        "rank_delta",
                        "base_phase",
                        "latest_phase",
                    ]
                ].head(25),
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 急落ランキング")
        if pb["drop_ranking"].empty:
            st.caption("該当なし")
        else:
            st.dataframe(
                pb["drop_ranking"][
                    [
                        "venue_name",
                        "race_no",
                        "combination",
                        "odds_old",
                        "odds_new",
                        "change_pct",
                        "base_phase",
                        "latest_phase",
                    ]
                ].head(25),
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 危険人気")
        danger = pb.get("honmei_overheat", pb.get("danger_favorites", pd.DataFrame()))
        if danger.empty:
            st.caption("該当なし")
        else:
            cols = [
                c
                for c in [
                    "venue_name",
                    "race_no",
                    "ninki_concentration",
                    "ninki_concentration_delta",
                    "fav_combo",
                    "fav_odds",
                    "market_alert_level",
                ]
                if c in danger.columns
            ]
            st.dataframe(danger[cols], use_container_width=True, hide_index=True)

        st.markdown("#### 穴候補")
        if pb["hole_candidates"].empty:
            st.caption("該当なし")
        else:
            st.dataframe(
                pb["hole_candidates"][
                    [
                        c
                        for c in [
                            "venue_name",
                            "race_no",
                            "combination",
                            "odds",
                            "ninki_rank",
                            "change_pct",
                            "distortion_ratio",
                            "phase",
                        ]
                        if c in pb["hole_candidates"].columns
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 市場警戒レベル")
        if pb["race_alerts"].empty:
            st.caption("該当なし")
        else:
            st.dataframe(
                pb["race_alerts"],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### 市場ヒートマップ")
        st.plotly_chart(pb["fig_heatmap"], use_container_width=True, key="prerace_fig_heatmap")

        st.markdown("#### AIスコアへの直前補正")
        if recommend_bundle.get("has_data"):
            adj_rows = []
            for c in recommend_bundle.get("all_cards", []):
                if c.get("pre_race_adjust"):
                    adj_rows.append(
                        {
                            "競輪場": c["venue_name"],
                            "R": c["race_no"],
                            "AIスコア": c["ai_total_score"],
                            "直前補正": c["pre_race_adjust"],
                            "補正後": c.get("pre_race_score"),
                            "理由": c.get("pre_race_reasons", ""),
                        }
                    )
            if adj_rows:
                st.dataframe(
                    pd.DataFrame(adj_rows),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("直前補正の反映はありません")
        else:
            st.caption("AIおすすめデータなし")

        with st.expander("テキストレポート"):
            st.text(lines_to_text(build_pre_race_lines(bet_type)))

with t_detect, safe_page("異常検知"):
    st.subheader("異常・オッズ歪み検知")
    if detect_df.empty:
        st.info("異常は検出されませんでした。")
    else:
        st.metric("検出件数", len(detect_df))
        st.dataframe(detect_df, use_container_width=True, hide_index=True)
        st.text(lines_to_text(build_detect_lines(bet_type)))

with t_report, safe_page("レポート"):
    st.subheader("report_latest.txt")
    st.caption(str(REPORT_LATEST))
    report_body = load_report_latest()
    st.text_area("レポート内容", value=report_body, height=500, label_visibility="collapsed")
    if REPORT_LATEST.exists():
        st.download_button(
            "txtをダウンロード",
            data=report_body,
            file_name="report_latest.txt",
            mime="text/plain",
        )

with tab_backup, safe_page("バックアップ"):
    st.subheader("バックアップ")
    st.caption("DB・設定・レポートをまとめて保存し、必要なときに復元できます。")

    bb = backup_bundle
    mobile_metrics(
        [
            ("最新", (bb["latest_at"] or "—")[:16]),
            ("履歴", len(bb["backups"])),
            ("現行DB", format_size(bb["db_size_bytes"])),
        ]
    )
    st.caption(f"保存先: {bb['backup_root']}")

    if st.button("💾 今すぐバックアップ", type="primary", use_container_width=True):
        with st.spinner("バックアップ作成中..."):
            result = create_backup()
        if result["ok"]:
            st.success(f"完了: {result['backup_id']}")
            st.rerun()
        else:
            st.error("バックアップに失敗しました")

    st.markdown("#### バックアップ履歴")
    backups = bb["backups"]
    if not backups:
        st.info("まだバックアップがありません。「今すぐバックアップ」を実行してください。")
    else:
        hist_rows = [
            {
                "日時": b["created_at"],
                "ID": b["backup_id"],
                "DB": format_size(b["db_size_bytes"]),
                "レポート": b["report_count"],
                "モデル": b.get("model_count", 0),
                "メモ": b.get("note", ""),
            }
            for b in backups
        ]
        st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, hide_index=True)

        st.markdown("#### 復元")
        st.warning("復元すると現在の DB / 設定 / レポートが上書きされます。実行前に自動でDB退避します。")

        restore_options = {
            f"{b['created_at']} — {b['backup_id']}": b["backup_id"]
            for b in backups
        }
        pick_id = st.selectbox("復元するバックアップ", list(restore_options.keys()))
        restore_id = restore_options[pick_id]

        c1, c2, c3 = st.columns(3)
        restore_db = c1.checkbox("DB", value=True)
        restore_settings = c2.checkbox("設定", value=True)
        restore_reports = c3.checkbox("レポート", value=True)
        restore_models = st.checkbox("MLモデル", value=True)

        if st.button("⏪ 選択したバックアップから復元", use_container_width=True):
            with st.spinner("復元中..."):
                res = restore_backup(
                    restore_id,
                    restore_db=restore_db,
                    restore_settings=restore_settings,
                    restore_reports=restore_reports,
                    restore_models=restore_models,
                )
            if res["ok"]:
                st.success("復元が完了しました。反映のためページを再読み込みしてください。")
                for line in res.get("log", []):
                    st.caption(line)
                st.rerun()
            else:
                st.error(res.get("error", "復元失敗"))

    with st.expander("バックアップ内容"):
        st.markdown(
            """
| 項目 | 内容 |
|------|------|
| DB | `data/keirin.db`（SQLiteバックアップAPI） |
| 設定 | `ops_config` + `requirements.txt` |
| レポート | `report_*.txt` / `report_latest.txt` |
| モデル | `data/models/*.json`（XGBoost） |

各バックアップは `data/backups/YYYYMMDD_HHMMSS/` に保存されます。
            """
        )

    with st.expander("テキストレポート"):
        st.text(lines_to_text(build_backup_lines()))

with tab_check, safe_page("システムチェック"):
    st.subheader("システムチェック")
    st.caption("DB・取得・分析・AI・学習・レポート・バックアップを一括確認します。")

    sc = system_check_bundle
    summary = sc.get("summary", {})

    col_run, col_deep, col_save = st.columns(3)
    with col_run:
        if st.button("🔄 再チェック", type="primary", use_container_width=True, key="check_refresh"):
            st.session_state.system_check_deep = False
            st.rerun()
    with col_deep:
        if st.button("🌐 API含む再チェック", use_container_width=True, key="check_deep"):
            st.session_state.system_check_deep = True
            st.rerun()
    with col_save:
        if st.button("💾 結果保存", use_container_width=True, key="check_save"):
            path = save_system_check_report(bet_type, bundle=sc)
            st.success(f"保存: {path.name}")

    overall = sc.get("overall_status", STATUS_OK)
    if overall == STATUS_ERROR:
        st.error(f"総合判定: {sc.get('overall_label', 'エラー')}")
    elif overall == STATUS_WARN:
        st.warning(f"総合判定: {sc.get('overall_label', '注意')}")
    else:
        st.success(f"総合判定: {sc.get('overall_label', '正常')}")

    st.caption(f"実行: {sc.get('checked_at', '—')}")

    mobile_metrics(
        [
            ("正常", summary.get(STATUS_OK, 0)),
            ("注意", summary.get(STATUS_WARN, 0)),
            ("エラー", summary.get(STATUS_ERROR, 0)),
        ]
    )

    checks_df = sc.get("checks_df", pd.DataFrame())
    if not checks_df.empty:
        show_df = checks_df[["項目", "状態", "メッセージ", "詳細", "修正候補"]].copy()

        st.markdown("#### チェック結果")
        st.dataframe(show_df, use_container_width=True, hide_index=True)

    col_miss, col_task = st.columns(2)
    with col_miss:
        st.markdown("#### 不足データ")
        missing = sc.get("missing_data") or []
        if not missing:
            st.caption("特になし")
        else:
            for m in missing:
                st.warning(m)
    with col_task:
        st.markdown("#### 次にやるべき作業")
        for task in sc.get("next_tasks") or []:
            st.info(task)

    st.markdown("#### 修正候補")
    fixes = sc.get("fix_suggestions") or []
    if not fixes:
        st.caption("修正候補なし")
    else:
        for fix in fixes:
            st.markdown(f"- {fix}")

    st.markdown("#### エラー一覧")
    err_df = sc.get("errors_df", pd.DataFrame())
    if err_df.empty:
        st.success("直近のエラーはありません")
    else:
        st.dataframe(err_df, use_container_width=True, hide_index=True)

    with st.expander("テキストレポート"):
        st.text(lines_to_text(sc.get("lines", [])))

with t_help, safe_page("使い方"):
    st.markdown(
        """
### メニュー構成（14タブ）

| タブ | 用途 |
|------|------|
| 🏠 ホーム | **毎日の運用ダッシュボード（メイン）** |
| ⭐ 今日のAIおすすめ | 狙い目・危険人気 |
| 🎯 実戦判定 | 買い/少額/見送り |
| 📡 市場監視 | オッズ急変・危険本命 |
| 🔗 ライン分析 | ライン構成 |
| 🤖 予測AI | AI指標 / ML / 直前 / グラフ |
| 🧠 学習状況 | パターン学習 / 本格学習 / 品質 / 収集 |
| 📈 収支検証 | 購入記録・仮想成績 |
| 💰 資金管理 | 推奨金額・元手管理 |
| 📊 検証レポート | 日次/週次/月次成績 |
| 💡 改善提案 | 弱点・改善案TOP5 |
| 💾 バックアップ | DB・設定の保存/復元 |
| 🔧 システムチェック | 全体の正常/注意/エラー確認 |
| ⚙ 設定 | 自動運用 / 通知 / 分析 / 異常 / レポート |

### 毎日の流れ（運用モード）

1. **🏠 ホーム** → ① 自動実行 → ②〜⑤ を確認
2. **🎯 実戦判定** → **💰 資金管理** で購入判断
3. レース前に **📡 市場監視** でオッズ再取得
4. 終わったら **📈 収支検証** → **📊 検証レポート**
5. データ収集: 有効100→300→1000件（**🧠 学習状況**）

README「毎日の運用手順」に詳細あり。

### 起動

```powershell
streamlit run app.py
```

※ 判断補助ツールです。自動購入ではありません。
        """
    )

st.divider()
st.caption(f"最終更新: {last_updated} · 券種: {bet_type}")
