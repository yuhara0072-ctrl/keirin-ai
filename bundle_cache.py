"""UI 用バンドルキャッシュ — 予測ロジックは変更せず読込・キャッシュのみ"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from config import DB_PATH

DB_COUNTS_SESSION_KEY = "db_counts_cache"
TAB_BUNDLE_PREFIX = "tab_bundle_"
TAB_LOADED_PREFIX = "tab_loaded_"


def db_mtime() -> float:
    try:
        return float(DB_PATH.stat().st_mtime) if DB_PATH.exists() else 0.0
    except OSError:
        return 0.0


def invalidate_db_counts_cache() -> None:
    st.session_state.pop(DB_COUNTS_SESSION_KEY, None)


def invalidate_tab_bundles() -> None:
    for key in list(st.session_state.keys()):
        sk = str(key)
        if sk.startswith(TAB_BUNDLE_PREFIX) or sk.startswith(TAB_LOADED_PREFIX):
            st.session_state.pop(key, None)
        if sk.startswith("bundles_"):
            st.session_state.pop(key, None)


def clear_bundle_caches() -> None:
    """workflow 後など — session + st.cache_data"""
    from race_features import clear_race_metrics_cache

    invalidate_db_counts_cache()
    invalidate_tab_bundles()
    st.session_state.pop("full_bundles_data", None)
    clear_race_metrics_cache()
    st.cache_data.clear()


def tab_is_loaded(tab_key: str) -> bool:
    return bool(st.session_state.get(f"{TAB_LOADED_PREFIX}{tab_key}"))


def get_tab_bundle(tab_key: str, default: dict | None = None) -> dict:
    data = st.session_state.get(f"{TAB_BUNDLE_PREFIX}{tab_key}")
    if isinstance(data, dict):
        return data
    return default if default is not None else {}


def _mark_tab_loaded(tab_key: str, data: dict) -> None:
    st.session_state[f"{TAB_BUNDLE_PREFIX}{tab_key}"] = data
    st.session_state[f"{TAB_LOADED_PREFIX}{tab_key}"] = True
    print(f"[app] tab bundle loaded: {tab_key}", flush=True)


@st.cache_data(ttl=600, show_spinner=False)
def cached_ai_score_bundle(bet_type: str, _mtime: float) -> dict:
    from ai_score import get_ai_score_bundle

    return get_ai_score_bundle(bet_type)


@st.cache_data(ttl=600, show_spinner=False)
def cached_ai_recommend_bundle(bet_type: str, _mtime: float) -> dict:
    from ai_recommend import get_ai_recommend_bundle

    scores = cached_ai_score_bundle(bet_type, _mtime)
    return get_ai_recommend_bundle(bet_type, scores=scores["scores"])


@st.cache_data(ttl=600, show_spinner=False)
def cached_line_bundle(_mtime: float) -> dict:
    from line_analysis import get_line_analysis_bundle

    return get_line_analysis_bundle(fetch_missing=False)


@st.cache_data(ttl=600, show_spinner=False)
def cached_market_bundle(bet_type: str, _mtime: float) -> dict:
    from market_monitor import get_market_monitor_bundle

    return get_market_monitor_bundle(bet_type)


@st.cache_data(ttl=600, show_spinner=False)
def cached_pre_race_bundle(bet_type: str, _mtime: float) -> dict:
    from pre_race import get_pre_race_bundle

    return get_pre_race_bundle(bet_type)


@st.cache_data(ttl=600, show_spinner=False)
def cached_ml_bundle(bet_type: str, _mtime: float) -> dict:
    from ml_model import get_ml_bundle

    scores = cached_ai_score_bundle(bet_type, _mtime)
    return get_ml_bundle(bet_type, scores=scores["scores"], retrain=False)


@st.cache_data(ttl=600, show_spinner=False)
def cached_ai_insights_bundle(bet_type: str, _mtime: float) -> dict:
    from ai_insights import get_ai_insights_bundle

    return get_ai_insights_bundle(bet_type, fetch_missing=False, include_lines=True)


@st.cache_data(ttl=600, show_spinner=False)
def cached_charts_bundle(bet_type: str, min_score: int, _mtime: float) -> dict:
    from charts import get_charts_bundle

    return get_charts_bundle(bet_type, min_score=min_score)


@st.cache_data(ttl=600, show_spinner=False)
def cached_learning_bundle(bet_type: str, refresh: bool, _mtime: float) -> dict:
    from learning import get_learning_bundle

    return get_learning_bundle(bet_type, refresh=refresh)


@st.cache_data(ttl=600, show_spinner=False)
def cached_quality_bundle(bet_type: str, _mtime: float) -> dict:
    from data_quality import get_quality_bundle

    return get_quality_bundle(bet_type, refresh=False)


@st.cache_data(ttl=600, show_spinner=False)
def cached_advanced_bundle(bet_type: str, _mtime: float) -> dict:
    from advanced_learning import get_advanced_learning_bundle

    return get_advanced_learning_bundle(bet_type, retrain=False)


def _load_battle_dependencies(bet_type: str, mtime: float) -> dict:
    """実戦判定の入力 bundle を並列取得（計算ロジックは変更なし）"""
    from ml_model import get_ml_bundle

    with ThreadPoolExecutor(max_workers=7) as pool:
        f_score = pool.submit(cached_ai_score_bundle, bet_type, mtime)
        f_market = pool.submit(cached_market_bundle, bet_type, mtime)
        f_line = pool.submit(cached_line_bundle, mtime)
        f_pre = pool.submit(cached_pre_race_bundle, bet_type, mtime)
        f_quality = pool.submit(cached_quality_bundle, bet_type, mtime)
        f_advanced = pool.submit(cached_advanced_bundle, bet_type, mtime)
        score_bundle = f_score.result()
        f_ml = pool.submit(
            lambda sb=score_bundle: get_ml_bundle(
                bet_type, sb["scores"], retrain=False
            )
        )
        return {
            "scores": score_bundle["scores"],
            "market": f_market.result(),
            "line": f_line.result(),
            "pre_race": f_pre.result(),
            "ml": f_ml.result(),
            "quality": f_quality.result(),
            "advanced": f_advanced.result(),
        }


@st.cache_data(ttl=600, show_spinner=False)
def cached_battle_judge_bundle(bet_type: str, _mtime: float) -> dict:
    from battle_judge import get_battle_judge_bundle

    deps = _load_battle_dependencies(bet_type, _mtime)
    return get_battle_judge_bundle(
        bet_type,
        scores=deps["scores"],
        market=deps["market"],
        line=deps["line"],
        pre_race=deps["pre_race"],
        ml=deps["ml"],
        quality=deps["quality"],
        advanced=deps["advanced"],
    )


def _validation_bundle_from_battle(
    bet_type: str,
    battle: dict,
    *,
    sync_virtual: bool,
) -> dict:
    from bankroll import get_bankroll_bundle
    from validation_report import get_validation_bundle

    bankroll = get_bankroll_bundle(bet_type, battle_bundle=battle)
    return get_validation_bundle(
        bet_type,
        battle_bundle=battle,
        bankroll_plan=bankroll,
        sync_virtual=sync_virtual,
    )


@st.cache_data(ttl=600, show_spinner=False)
def cached_bankroll_bundle(bet_type: str, _mtime: float) -> dict:
    from bankroll import get_bankroll_bundle

    battle = cached_battle_judge_bundle(bet_type, _mtime)
    return get_bankroll_bundle(bet_type, battle_bundle=battle)


@st.cache_data(ttl=600, show_spinner=False)
def cached_pnl_bundle(bet_type: str, _mtime: float) -> dict:
    from bet_tracker import get_pnl_bundle

    rec = cached_ai_recommend_bundle(bet_type, _mtime)
    return get_pnl_bundle(bet_type, recommend=rec, sync_virtual=False)


@st.cache_data(ttl=600, show_spinner=False)
def cached_validation_bundle(bet_type: str, _mtime: float) -> dict:
    battle = cached_battle_judge_bundle(bet_type, _mtime)
    return _validation_bundle_from_battle(bet_type, battle, sync_virtual=False)


@st.cache_data(ttl=600, show_spinner=False)
def cached_improvement_bundle(bet_type: str, _mtime: float) -> dict:
    from improvement_ai import get_improvement_bundle

    battle = cached_battle_judge_bundle(bet_type, _mtime)
    from bankroll import get_bankroll_bundle

    bankroll = get_bankroll_bundle(bet_type, battle_bundle=battle)
    from validation_report import get_validation_bundle

    validation = get_validation_bundle(
        bet_type,
        battle_bundle=battle,
        bankroll_plan=bankroll,
        sync_virtual=False,
    )
    quality = cached_quality_bundle(bet_type, _mtime)
    advanced = cached_advanced_bundle(bet_type, _mtime)
    return get_improvement_bundle(
        bet_type,
        validation=validation,
        bankroll_plan=bankroll,
        quality=quality,
        advanced=advanced,
    )


@st.cache_data(ttl=600, show_spinner=False)
def cached_collect_bundle(target_races: int, _mtime: float) -> dict:
    from bulk_collect import get_collect_bundle

    return get_collect_bundle(target_races)


@st.cache_data(ttl=600, show_spinner=False)
def cached_backup_bundle(_mtime: float) -> dict:
    from backup import get_backup_bundle

    return get_backup_bundle()


@st.cache_data(ttl=600, show_spinner=False)
def cached_ops_status(bet_type: str, _mtime: float) -> dict:
    from ops import get_ops_status

    return get_ops_status(bet_type, fast=True, targets_count=0)


@st.cache_data(ttl=600, show_spinner=False)
def cached_system_check_bundle(bet_type: str, deep: bool, _mtime: float) -> dict:
    from system_check import get_system_check_bundle

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_quality = pool.submit(cached_quality_bundle, bet_type, _mtime)
        f_score = pool.submit(cached_ai_score_bundle, bet_type, _mtime)
        f_learning = pool.submit(cached_learning_bundle, bet_type, False, _mtime)
        f_backup = pool.submit(cached_backup_bundle, _mtime)
        quality = f_quality.result()
        score = f_score.result()
        learning = f_learning.result()
        backup = f_backup.result()
    return get_system_check_bundle(
        bet_type,
        deep=deep,
        quality=quality,
        score_bundle=score,
        learning_bundle=learning,
        backup_bundle=backup,
    )


def load_tab_bundle(tab_key: str, bet_type: str, *, deep_check: bool = False) -> None:
    """タブ単位で必要なバンドルのみ読込（GitHub 復元は ensure_db_ready 側）"""
    from auth import ensure_db_ready

    ensure_db_ready(force_restore=True)
    mtime = db_mtime()

    loaders = {
        "rec": lambda: {
            "score_bundle": cached_ai_score_bundle(bet_type, mtime),
            "recommend_bundle": cached_ai_recommend_bundle(bet_type, mtime),
        },
        "battle": lambda: {"battle_bundle": cached_battle_judge_bundle(bet_type, mtime)},
        "line": lambda: {"line_bundle": cached_line_bundle(mtime)},
        "predict_ai": lambda: {"ai_bundle": cached_ai_insights_bundle(bet_type, mtime)},
        "predict_ml": lambda: {"ml_bundle": cached_ml_bundle(bet_type, mtime)},
        "predict_prerace": lambda: {"pre_race_bundle": cached_pre_race_bundle(bet_type, mtime)},
        "predict_chart": lambda: {
            "charts_bundle": cached_charts_bundle(bet_type, 70, mtime),
            "score_bundle": cached_ai_score_bundle(bet_type, mtime),
        },
        "market": lambda: {"market_bundle": cached_market_bundle(bet_type, mtime)},
        "bankroll": lambda: {
            "bankroll_bundle": cached_bankroll_bundle(bet_type, mtime),
            "battle_bundle": cached_battle_judge_bundle(bet_type, mtime),
        },
        "validation": lambda: {"validation_bundle": cached_validation_bundle(bet_type, mtime)},
        "improve": lambda: {"improvement_bundle": cached_improvement_bundle(bet_type, mtime)},
        "pnl": lambda: {
            "pnl_bundle": cached_pnl_bundle(bet_type, mtime),
            "recommend_bundle": cached_ai_recommend_bundle(bet_type, mtime),
        },
        "learn": lambda: {"learning_bundle": cached_learning_bundle(bet_type, False, mtime)},
        "advanced": lambda: {"advanced_bundle": cached_advanced_bundle(bet_type, mtime)},
        "quality": lambda: {"quality_bundle": cached_quality_bundle(bet_type, mtime)},
        "collect": lambda: {"collect_bundle": cached_collect_bundle(100, mtime)},
        "backup": lambda: {"backup_bundle": cached_backup_bundle(mtime)},
        "check": lambda: {
            "system_check_bundle": cached_system_check_bundle(
                bet_type, deep_check, mtime
            )
        },
        "settings_ops": lambda: {"ops_status": cached_ops_status(bet_type, mtime)},
    }

    loader = loaders.get(tab_key)
    if loader is None:
        _mark_tab_loaded(tab_key, {})
        return

    with st.spinner("読み込み中..."):
        _mark_tab_loaded(tab_key, loader())


def prompt_load_tab(tab_label: str, tab_key: str, bet_type: str) -> bool:
    if tab_is_loaded(tab_key):
        return False
    st.info(f"「{tab_label}」はボタンを押すと読み込みます（初回のみ数秒かかることがあります）。")
    if st.button(
        f"{tab_label} を読み込む",
        key=f"load_tab_{tab_key}",
        use_container_width=True,
        type="primary",
    ):
        load_tab_bundle(tab_key, bet_type)
        st.rerun()
    return True


def should_render_tab(tab_label: str, tab_key: str, bet_type: str) -> bool:
    """True = タブ本文を描画してよい"""
    if tab_is_loaded(tab_key):
        return True
    prompt_load_tab(tab_label, tab_key, bet_type)
    return False
