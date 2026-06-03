"""月間目標 — 収支目標・進捗・攻め/守り判定"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Optional

from bankroll import get_bankroll_config, set_bankroll_config
from bet_tracker import load_bet_records
from validation_report import period_range

DEFAULT_MONTHLY_TARGET = 10_000
CONFIG_KEY_MONTHLY_TARGET = "monthly_target_profit"

STANCE_ATTACK = "攻める"
STANCE_DEFEND = "守る"
STANCE_NEUTRAL = "標準"


def _int_cfg(cfg: dict[str, str], key: str, default: int) -> int:
    try:
        return int(float(str(cfg.get(key, default)).replace(",", "")))
    except (TypeError, ValueError):
        return default


def get_monthly_target() -> int:
    cfg = get_bankroll_config()
    return _int_cfg(cfg, CONFIG_KEY_MONTHLY_TARGET, DEFAULT_MONTHLY_TARGET)


def set_monthly_target(amount: int) -> None:
    set_bankroll_config(CONFIG_KEY_MONTHLY_TARGET, str(max(0, int(amount))))


def month_profit_summary(bet_type: str = "3連単", ref: Optional[date] = None) -> dict:
    """今月の確定収支（実購入のみ）"""
    d = ref or date.today()
    start, end = period_range("month", d)
    df = load_bet_records(bet_type, is_virtual=0, status="settled")
    if df.empty:
        return {
            "total_bet": 0,
            "total_payout": 0,
            "total_profit": 0,
            "recovery_rate": 0.0,
            "hit_rate": 0.0,
            "settled": 0,
        }

    sub = df.copy()
    sub["_date"] = sub["race_date"].astype(str).str.replace("-", "", regex=False)
    sub = sub[(sub["_date"] >= start) & (sub["_date"] <= end)]
    if sub.empty:
        return {
            "total_bet": 0,
            "total_payout": 0,
            "total_profit": 0,
            "recovery_rate": 0.0,
            "hit_rate": 0.0,
            "settled": 0,
        }

    total_bet = int(sub["bet_amount"].sum())
    total_payout = int(sub["payout"].sum())
    total_profit = int(sub["profit"].sum())
    hits = int(sub["hit"].sum())
    n = len(sub)
    return {
        "total_bet": total_bet,
        "total_payout": total_payout,
        "total_profit": total_profit,
        "recovery_rate": round(total_payout / total_bet * 100, 1) if total_bet else 0.0,
        "hit_rate": round(hits / n * 100, 1) if n else 0.0,
        "settled": n,
    }


def _days_left_in_month(ref: date) -> int:
    last_day = calendar.monthrange(ref.year, ref.month)[1]
    return max(1, last_day - ref.day + 1)


def decide_daily_stance(
    *,
    remaining: int,
    target: int,
    current_profit: int,
    days_left: int,
    day_of_month: int,
    lose_streak: int,
    win_streak: int,
    trust_level: str,
    current_bankroll: int,
    initial_bankroll: int,
) -> tuple[str, str]:
    """今日は攻める / 守る / 標準"""
    if target <= 0:
        return STANCE_NEUTRAL, "月目標が未設定です"

    if remaining <= 0:
        return STANCE_ATTACK, "月目標を達成済み — 利益確保を優先しつつ好条件のみ"

    total_days = day_of_month + days_left - 1
    expected_pace = target * day_of_month / max(total_days, 1)
    behind = current_profit < expected_pace * 0.6
    ahead = current_profit >= expected_pace * 1.15

    if lose_streak >= 3 or current_bankroll < initial_bankroll * 0.5:
        return STANCE_DEFEND, f"{lose_streak}連敗または資金減 — 1レース上限・危険レース0円を厳守"
    if trust_level == "insufficient":
        return STANCE_DEFEND, "AI信頼度が不足 — データ収集と検証を優先"
    if behind and remaining > target * 0.5:
        return STANCE_DEFEND, "月目標に対して遅れ — 無理な追いは避け推奨額内で"
    if ahead or win_streak >= 3:
        return STANCE_ATTACK, "ペース良好 — 高スコア・買い判定のみ積極的に"
    if lose_streak >= 2:
        return STANCE_DEFEND, "連敗中 — 推奨額は自動減額済み。見送りを増やす"
    return STANCE_NEUTRAL, "標準運用 — 推奨購入額と実戦判定に従う"


def get_monthly_goal_bundle(
    bet_type: str = "3連単",
    *,
    bankroll: Optional[dict] = None,
    trust_level: str = "insufficient",
    ref: Optional[date] = None,
) -> dict:
    d = ref or date.today()
    target = get_monthly_target()
    month_stats = month_profit_summary(bet_type, d)
    current_profit = int(month_stats["total_profit"])
    remaining = target - current_profit
    days_left = _days_left_in_month(d)
    daily_required = max(0, int(remaining / days_left)) if remaining > 0 else 0

    streaks = (bankroll or {}).get("streaks") or {}
    lose_streak = int(streaks.get("lose_streak") or 0)
    win_streak = int(streaks.get("win_streak") or 0)
    current_bankroll = int((bankroll or {}).get("current_bankroll") or 0)
    initial_bankroll = int((bankroll or {}).get("initial_bankroll") or current_bankroll)

    stance, stance_reason = decide_daily_stance(
        remaining=remaining,
        target=target,
        current_profit=current_profit,
        days_left=days_left,
        day_of_month=d.day,
        lose_streak=lose_streak,
        win_streak=win_streak,
        trust_level=trust_level,
        current_bankroll=current_bankroll,
        initial_bankroll=initial_bankroll,
    )

    progress = min(1.0, current_profit / target) if target > 0 else 0.0
    if current_profit < 0:
        progress = 0.0

    month_label = d.strftime("%Y年%m月")
    return {
        "month_label": month_label,
        "target_profit": target,
        "current_profit": current_profit,
        "remaining": remaining,
        "achieved": remaining <= 0,
        "days_left": days_left,
        "daily_required": daily_required,
        "progress_ratio": progress,
        "progress_pct": round(progress * 100, 1),
        "stance": stance,
        "stance_reason": stance_reason,
        "month_stats": month_stats,
    }
