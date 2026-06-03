"""資金管理 — AI判定に応じた購入金額・元手管理"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pandas as pd
import plotly.graph_objects as go

from battle_judge import VERDICT_BUY, VERDICT_CHECK, VERDICT_SKIP, VERDICT_SMALL
from bet_tracker import load_bet_records, settle_pending_bets
from db import db_session, get_connection

DEFAULT_INITIAL_BANKROLL = 5000
DEFAULT_MAX_PER_RACE = 500
DEFAULT_MAX_DAILY = 1500
DEFAULT_BASE_UNIT = 100

RANK_STAKES = {
    "S": {"per_race": 300, "per_combo": 100, "label": "Sランク（本命）"},
    "A": {"per_race": 200, "per_combo": 100, "label": "Aランク（有力）"},
    "B": {"per_race": 100, "per_combo": 100, "label": "Bランク（様子見）"},
    "C": {"per_race": 100, "per_combo": 100, "label": "Cランク（最小）"},
    "D": {"per_race": 0, "per_combo": 0, "label": "Dランク（見送り）"},
}

SCORE_STAKES = [
    (80, 300),
    (65, 200),
    (50, 100),
    (0, 0),
]

BANKROLL_CONFIG_DDL = """
CREATE TABLE IF NOT EXISTS bankroll_config (
    config_key  TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

BANKROLL_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS bankroll_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL,
    balance         INTEGER NOT NULL,
    daily_used      INTEGER NOT NULL DEFAULT 0,
    note            TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

BANKROLL_SNAPSHOTS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_bankroll_snap_date "
    "ON bankroll_snapshots(snapshot_date)"
)


def _bankroll_config_columns(conn) -> set[str]:
    from db import table_exists

    if not table_exists(conn, "bankroll_config"):
        return set()
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(bankroll_config)")}


def _upgrade_bankroll_config_schema(conn) -> None:
    """旧スキーマ (key 列) を config_key へ移行"""
    cols = _bankroll_config_columns(conn)
    if not cols or "config_key" in cols or "key" not in cols:
        return
    try:
        conn.execute('ALTER TABLE bankroll_config RENAME COLUMN "key" TO config_key')
        return
    except Exception:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bankroll_config_new (
            config_key  TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO bankroll_config_new (config_key, value, updated_at)
        SELECT "key", value, updated_at FROM bankroll_config
        """
    )
    conn.execute("DROP TABLE bankroll_config")
    conn.execute("ALTER TABLE bankroll_config_new RENAME TO bankroll_config")


DEFAULT_CONFIG = {
    "initial_bankroll": str(DEFAULT_INITIAL_BANKROLL),
    "current_bankroll": str(DEFAULT_INITIAL_BANKROLL),
    "max_per_race": str(DEFAULT_MAX_PER_RACE),
    "max_daily": str(DEFAULT_MAX_DAILY),
    "base_unit": str(DEFAULT_BASE_UNIT),
    "monthly_target_profit": "10000",
}


def migrate_bankroll_table(conn) -> None:
    conn.execute(BANKROLL_CONFIG_DDL)
    conn.execute(BANKROLL_SNAPSHOTS_DDL)
    conn.execute(BANKROLL_SNAPSHOTS_INDEX)
    _upgrade_bankroll_config_schema(conn)
    for config_key, config_value in DEFAULT_CONFIG.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO bankroll_config (config_key, value)
            VALUES (?, ?)
            """,
            (config_key, config_value),
        )


def get_bankroll_config() -> dict[str, str]:
    conn = get_connection()
    migrate_bankroll_table(conn)
    rows = conn.execute("SELECT config_key, value FROM bankroll_config").fetchall()
    conn.close()
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({str(r[0]): str(r[1]) for r in rows})
    return cfg


def set_bankroll_config(key: str, value: str) -> None:
    with db_session() as conn:
        migrate_bankroll_table(conn)
        conn.execute(
            """
            INSERT INTO bankroll_config (config_key, value, updated_at)
            VALUES (?, ?, datetime('now', 'localtime'))
            ON CONFLICT(config_key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value),
        )


def save_bankroll_snapshot(balance: int, daily_used: int = 0, note: str = "") -> None:
    today = date.today().isoformat()
    with db_session() as conn:
        migrate_bankroll_table(conn)
        conn.execute(
            """
            INSERT INTO bankroll_snapshots (snapshot_date, balance, daily_used, note)
            VALUES (?, ?, ?, ?)
            """,
            (today, int(balance), int(daily_used), note),
        )


def load_bankroll_snapshots(limit: int = 60) -> pd.DataFrame:
    conn = get_connection()
    migrate_bankroll_table(conn)
    df = pd.read_sql(
        """
        SELECT snapshot_date, balance, daily_used, note, created_at
        FROM bankroll_snapshots
        ORDER BY id DESC
        LIMIT ?
        """,
        conn,
        params=(limit,),
    )
    conn.close()
    return df


def _int_cfg(cfg: dict[str, str], key: str, default: int) -> int:
    try:
        return int(float(cfg.get(key, default)))
    except (TypeError, ValueError):
        return default


def compute_profit_balance(initial: int, bet_type: str = "3連単") -> int:
    """初期元手 + 実購入の確定収支"""
    df = load_bet_records(bet_type, is_virtual=0, status="settled")
    if df.empty:
        return initial
    return initial + int(df["profit"].sum())


def compute_streaks(bet_type: str = "3連単") -> dict[str, int]:
    """連勝・連敗（直近の確定購入）"""
    df = load_bet_records(bet_type, is_virtual=0, status="settled")
    if df.empty:
        return {"win_streak": 0, "lose_streak": 0}

    ordered = df.sort_values(
        by=["settled_at", "id"],
        ascending=[False, False],
    )
    win_streak = 0
    lose_streak = 0
    for _, row in ordered.iterrows():
        hit = bool(row.get("hit"))
        if win_streak == 0 and lose_streak == 0:
            if hit:
                win_streak = 1
            else:
                lose_streak = 1
            continue
        if win_streak > 0:
            if hit:
                win_streak += 1
            else:
                break
        elif lose_streak > 0:
            if not hit:
                lose_streak += 1
            else:
                break
    return {"win_streak": win_streak, "lose_streak": lose_streak}


def streak_multiplier(win_streak: int, lose_streak: int) -> tuple[float, list[str]]:
    """連敗減額・連勝時も急増しない"""
    mult = 1.0
    notes: list[str] = []
    if lose_streak >= 5:
        mult *= 0.3
        notes.append(f"5連敗→30%に減額")
    elif lose_streak >= 3:
        mult *= 0.5
        notes.append(f"{lose_streak}連敗→50%に減額")
    elif lose_streak >= 2:
        mult *= 0.7
        notes.append(f"{lose_streak}連敗→70%に減額")

    if win_streak >= 3:
        capped = min(1.15, 1.0 + win_streak * 0.03)
        mult *= capped
        notes.append(f"{win_streak}連勝→最大+15%まで（{capped:.2f}倍）")
    return round(mult, 2), notes


def rank_base_stake(ev_rank: str) -> dict:
    rank = str(ev_rank or "D").upper()
    if rank not in RANK_STAKES:
        rank = "D"
    return RANK_STAKES[rank]


def score_base_stake(ai_score: float) -> int:
    for threshold, amount in SCORE_STAKES:
        if ai_score >= threshold:
            return amount
    return 0


def today_bet_usage(bet_type: str, today: str) -> int:
    df = load_bet_records(bet_type, is_virtual=0)
    if df.empty:
        return 0
    sub = df[df["race_date"].astype(str) == str(today)]
    return int(sub["bet_amount"].sum()) if not sub.empty else 0


def compute_race_stake(
    card: dict,
    *,
    max_per_race: int,
    max_daily: int,
    daily_used: int,
    current_bankroll: int,
    streak_mult: float,
) -> dict:
    """1レースの推奨購入額"""
    verdict = card.get("battle_verdict") or card.get("verdict") or VERDICT_SKIP
    ev_rank = str(card.get("ev_rank") or "D")
    ai_score = float(card.get("pre_race_score") or card.get("ai_total_score") or 0)
    reasons: list[str] = []

    if card.get("danger_popular") or card.get("do_not_buy"):
        return {
            "recommended_yen": 0,
            "per_combo_yen": 0,
            "stake_reason": "危険レースのため0円",
            "rank_label": rank_base_stake(ev_rank)["label"],
            "blocked": True,
        }

    if verdict == VERDICT_SKIP:
        return {
            "recommended_yen": 0,
            "per_combo_yen": 0,
            "stake_reason": card.get("battle_hint") or "見送り",
            "rank_label": rank_base_stake(ev_rank)["label"],
            "blocked": True,
        }

    rank_stake = rank_base_stake(ev_rank)
    score_stake = score_base_stake(ai_score)
    base = min(rank_stake["per_race"], score_stake) if score_stake else rank_stake["per_race"]

    verdict_map = {
        VERDICT_BUY: 1.0,
        VERDICT_SMALL: 0.7,
        VERDICT_CHECK: 0.5,
    }
    base = int(base * verdict_map.get(verdict, 0.5))
    base = int(base * streak_mult)

    bankroll_cap = max(DEFAULT_BASE_UNIT, int(current_bankroll * 0.1))
    cap = min(max_per_race, bankroll_cap)
    amount = min(base, cap)

    daily_remaining = max(0, max_daily - daily_used)
    if amount > daily_remaining:
        amount = daily_remaining
        reasons.append("本日上限に達するため調整")

    if amount > current_bankroll:
        amount = max(0, int(current_bankroll // DEFAULT_BASE_UNIT) * DEFAULT_BASE_UNIT)
        reasons.append("残資金不足のため調整")

    amount = max(0, (amount // DEFAULT_BASE_UNIT) * DEFAULT_BASE_UNIT)
    per_combo = min(rank_stake["per_combo"], amount) if amount else 0

    if amount == 0:
        stake_reason = "上限または残資金の都合で0円"
    else:
        stake_reason = f"{rank_stake['label']} / {verdict} / AI{ai_score:.0f}点"
        if reasons:
            stake_reason += " / " + " / ".join(reasons)

    return {
        "recommended_yen": amount,
        "per_combo_yen": per_combo,
        "stake_reason": stake_reason,
        "rank_label": rank_stake["label"],
        "blocked": amount == 0,
    }


def build_bankroll_trend(
    initial: int,
    bet_type: str = "3連単",
    snapshots: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """資金推移（初期元手 + 日次収支）"""
    df = load_bet_records(bet_type, is_virtual=0, status="settled")
    rows: list[dict] = [{"date": "開始", "balance": initial, "profit": 0, "source": "初期"}]

    if not df.empty and "race_date" in df.columns:
        daily = (
            df.groupby("race_date", as_index=False)
            .agg(profit=("profit", "sum"))
            .sort_values("race_date")
        )
        balance = initial
        for _, row in daily.iterrows():
            balance += int(row["profit"])
            rows.append(
                {
                    "date": str(row["race_date"]),
                    "balance": balance,
                    "profit": int(row["profit"]),
                    "source": "収支",
                }
            )

    if snapshots is not None and not snapshots.empty:
        for _, row in snapshots.sort_values("snapshot_date").iterrows():
            rows.append(
                {
                    "date": str(row["snapshot_date"]),
                    "balance": int(row["balance"]),
                    "profit": 0,
                    "source": "記録",
                }
            )

    trend = pd.DataFrame(rows)
    if trend.empty:
        return pd.DataFrame([{"date": date.today().isoformat(), "balance": initial, "profit": 0, "source": "現在"}])
    return trend.drop_duplicates(subset=["date", "balance"], keep="last")


def fig_bankroll_trend(trend: pd.DataFrame):
    if trend.empty:
        fig = go.Figure()
        fig.update_layout(title="資金推移（データなし）", height=360)
        return fig

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=trend["date"],
            y=trend["balance"],
            mode="lines+markers",
            name="資金",
            line={"color": "#059669", "width": 2},
            fill="tozeroy",
            fillcolor="rgba(5, 150, 105, 0.08)",
        )
    )
    fig.update_layout(
        title="資金推移",
        xaxis_title="日付",
        yaxis_title="円",
        height=380,
        margin=dict(l=40, r=20, t=50, b=40),
    )
    return fig


def build_risk_warnings(
    *,
    current_bankroll: int,
    initial: int,
    daily_used: int,
    max_daily: int,
    lose_streak: int,
    win_streak: int,
    recommended_total: int,
) -> list[str]:
    warnings: list[str] = []
    if current_bankroll < initial * 0.5:
        warnings.append(f"元手の50%を下回っています（{current_bankroll:,}円）")
    if daily_used >= max_daily:
        warnings.append("本日の使用上限に達しています")
    elif daily_used + recommended_total > max_daily:
        warnings.append("推奨合計が本日上限を超える可能性があります")
    if lose_streak >= 3:
        warnings.append(f"{lose_streak}連敗中 — 自動減額モード")
    if win_streak >= 5:
        warnings.append(f"{win_streak}連勝中 — 増額は+15%上限（冷静に）")
    if current_bankroll < recommended_total:
        warnings.append("推奨購入額が残資金を上回っています")
    if current_bankroll < DEFAULT_BASE_UNIT * 3:
        warnings.append("残資金が少なく、購入は非推奨です")
    return warnings


def build_bankroll_plan(
    battle_bundle: dict,
    bet_type: str = "3連単",
    *,
    current_bankroll: Optional[int] = None,
    max_per_race: Optional[int] = None,
    max_daily: Optional[int] = None,
) -> dict:
    """本日の購入計画"""
    cfg = get_bankroll_config()
    initial = _int_cfg(cfg, "initial_bankroll", DEFAULT_INITIAL_BANKROLL)
    if current_bankroll is None:
        saved = _int_cfg(cfg, "current_bankroll", initial)
        computed = compute_profit_balance(initial, bet_type)
        current_bankroll = saved if saved != initial else computed
    max_per_race = max_per_race or _int_cfg(cfg, "max_per_race", DEFAULT_MAX_PER_RACE)
    max_daily = max_daily or _int_cfg(cfg, "max_daily", DEFAULT_MAX_DAILY)

    today = battle_bundle.get("today") or date.today().strftime("%Y%m%d")
    daily_used = today_bet_usage(bet_type, today)
    streaks = compute_streaks(bet_type)
    streak_mult, streak_notes = streak_multiplier(
        streaks["win_streak"], streaks["lose_streak"]
    )

    allocations: list[dict] = []
    daily_allocated = daily_used

    for card in battle_bundle.get("all_cards") or []:
        stake = compute_race_stake(
            card,
            max_per_race=max_per_race,
            max_daily=max_daily,
            daily_used=daily_allocated,
            current_bankroll=current_bankroll,
            streak_mult=streak_mult,
        )
        item = {
            "race_id": card.get("race_id"),
            "venue_name": card.get("venue_name"),
            "race_no": card.get("race_no"),
            "ev_rank": card.get("ev_rank"),
            "ai_score": card.get("pre_race_score") or card.get("ai_total_score"),
            "battle_verdict": card.get("battle_verdict"),
            "danger_popular": card.get("danger_popular"),
            **stake,
        }
        allocations.append(item)
        if stake["recommended_yen"] > 0:
            daily_allocated += stake["recommended_yen"]

    recommended_total = sum(a["recommended_yen"] for a in allocations)
    remaining = max(0, current_bankroll - recommended_total)
    daily_remaining = max(0, max_daily - daily_used - recommended_total)

    snapshots = load_bankroll_snapshots()
    trend = build_bankroll_trend(initial, bet_type, snapshots)
    warnings = build_risk_warnings(
        current_bankroll=current_bankroll,
        initial=initial,
        daily_used=daily_used,
        max_daily=max_daily,
        lose_streak=streaks["lose_streak"],
        win_streak=streaks["win_streak"],
        recommended_total=recommended_total,
    )

    buy_today = [a for a in allocations if a["recommended_yen"] > 0]
    buy_today.sort(key=lambda x: x["recommended_yen"], reverse=True)

    return {
        "has_data": battle_bundle.get("has_data", False),
        "today": today,
        "bet_type": bet_type,
        "initial_bankroll": initial,
        "current_bankroll": current_bankroll,
        "max_per_race": max_per_race,
        "max_daily": max_daily,
        "daily_used": daily_used,
        "daily_limit_remaining": max(0, max_daily - daily_used),
        "recommended_total": recommended_total,
        "remaining_bankroll": remaining,
        "daily_remaining_after_plan": daily_remaining,
        "streaks": streaks,
        "streak_multiplier": streak_mult,
        "streak_notes": streak_notes,
        "rank_stakes": RANK_STAKES,
        "score_stakes": SCORE_STAKES,
        "allocations": allocations,
        "buy_today": buy_today,
        "trend": trend,
        "fig_trend": fig_bankroll_trend(trend),
        "warnings": warnings,
    }


def get_bankroll_bundle(
    bet_type: str = "3連単",
    *,
    battle_bundle: Optional[dict] = None,
    current_bankroll: Optional[int] = None,
    max_per_race: Optional[int] = None,
    max_daily: Optional[int] = None,
) -> dict:
    if battle_bundle is None:
        from battle_judge import get_battle_judge_bundle

        battle_bundle = get_battle_judge_bundle(bet_type)

    plan = build_bankroll_plan(
        battle_bundle,
        bet_type,
        current_bankroll=current_bankroll,
        max_per_race=max_per_race,
        max_daily=max_daily,
    )
    plan["lines"] = build_bankroll_lines(plan)
    return plan


def build_bankroll_lines(plan: Optional[dict] = None, bet_type: str = "3連単") -> list[str]:
    p = plan or get_bankroll_bundle(bet_type)
    lines = [f"【資金管理】券種={bet_type}  元手{p['initial_bankroll']:,}円", ""]
    lines.append(f"  現在資金: {p['current_bankroll']:,}円")
    lines.append(f"  本日上限: {p['max_daily']:,}円（使用{p['daily_used']:,}円）")
    lines.append(f"  推奨購入: {p['recommended_total']:,}円 / 残り{p['remaining_bankroll']:,}円")
    lines.append(
        f"  連勝{p['streaks']['win_streak']} / 連敗{p['streaks']['lose_streak']} "
        f"（倍率{p['streak_multiplier']}）"
    )
    lines.append("")

    lines.append("--- S/A/B ランク目安 ---")
    for rank in ("S", "A", "B"):
        rs = RANK_STAKES[rank]
        lines.append(f"  {rank}: {rs['per_race']}円/レース（1点{rs['per_combo']}円）")
    lines.append("")

    lines.append("--- 本日の推奨 ---")
    if not p["buy_today"]:
        lines.append("  （推奨購入なし）")
    else:
        for a in p["buy_today"][:8]:
            lines.append(
                f"  {a['venue_name']} {a['race_no']}R "
                f"{a['recommended_yen']}円 — {a['stake_reason']}"
            )
    lines.append("")

    if p.get("warnings"):
        lines.append("--- リスク警告 ---")
        for w in p["warnings"]:
            lines.append(f"  ! {w}")
        lines.append("")

    return lines
