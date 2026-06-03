"""ホーム画面 — 毎日予想・学習・資金・月目標の統合"""

from __future__ import annotations

from datetime import date
from typing import Optional

from monthly_goal import get_monthly_goal_bundle

APP_PILLARS = [
    {
        "title": "1. 毎日予想",
        "items": [
            "今日の狙い目 TOP3",
            "見送りレース",
            "危険人気",
            "AIスコア・信頼度",
        ],
    },
    {
        "title": "2. 学習",
        "items": [
            "予想結果の記録",
            "的中/不的中・回収率",
            "AIスコア別成績",
            "強い条件 / 弱い条件 → 次回スコアへ反映",
        ],
    },
    {
        "title": "3. 資金管理",
        "items": [
            "現在資金・1日/1レース上限",
            "連敗時減額・危険レース0円",
            "推奨購入額",
        ],
    },
    {
        "title": "4. 月目標",
        "items": [
            "月目標金額・現在収支",
            "目標まで残り・1日あたり必要額",
            "今日は攻める / 守る",
        ],
    },
]


def build_daily_prediction_summary(
    *,
    recommend: dict,
    battle: dict,
    trust: dict,
) -> dict:
    targets = list(recommend.get("targets") or [])[:3]
    skip_cards = list(battle.get("skip_candidates") or [])
    danger = list(recommend.get("dangerous_popular") or recommend.get("danger") or [])[:5]
    buy_n = len(battle.get("buy_candidates") or [])

    top_scores = []
    for card in targets:
        top_scores.append(
            {
                "venue": card.get("venue_name", ""),
                "race_no": card.get("race_no"),
                "ai_score": card.get("ai_total_score") or card.get("pre_race_score"),
                "ev_rank": card.get("ev_rank"),
            }
        )

    return {
        "has_data": bool(recommend.get("has_data") or battle.get("has_data")),
        "targets": targets,
        "target_count": len(recommend.get("targets") or []),
        "skip_count": len(skip_cards),
        "skip_preview": skip_cards[:3],
        "danger_count": len(danger),
        "danger_preview": danger[:3],
        "buy_count": buy_n,
        "trust": trust,
    }


def build_learning_snapshot(
    *,
    learning: dict,
    validation: dict,
) -> dict:
    month = validation.get("month", {}).get("summary", {})
    high = learning.get("high_recovery_top10")
    low = learning.get("low_recovery_top10")
    strong = []
    weak = []
    if high is not None and not high.empty:
        for _, row in high.head(3).iterrows():
            strong.append(
                f"{row.get('condition_label', '')} 回収{row.get('recovery_rate', 0)}%"
            )
    if low is not None and not low.empty:
        for _, row in low.head(3).iterrows():
            weak.append(
                f"{row.get('condition_label', '')} 回収{row.get('recovery_rate', 0)}%"
            )

    return {
        "has_data": learning.get("has_data", False),
        "learning_count": learning.get("learning_count", 0),
        "month_settled": month.get("settled", 0),
        "month_recovery": month.get("recovery_rate"),
        "month_hit_rate": month.get("hit_rate"),
        "month_profit": month.get("total_profit", 0),
        "strong_conditions": strong,
        "weak_conditions": weak,
    }


def build_today_todos(
    *,
    status: dict,
    recommend: dict,
    battle: dict,
    market: dict,
    pnl: dict,
    validation: dict,
    quality: dict,
    ops: dict,
    monthly: dict,
    bankroll: dict,
    prediction: dict,
) -> list[dict]:
    todos: list[dict] = []
    today = date.today().strftime("%Y%m%d")

    stance = monthly.get("stance", "")
    if stance == "守る":
        todos.append(
            {
                "text": f"今日は【{stance}】— {monthly.get('stance_reason', '')}",
                "done": False,
                "tab": "ホーム",
            }
        )
    elif stance == "攻める":
        todos.append(
            {
                "text": f"今日は【{stance}】— 好条件レースを優先",
                "done": False,
                "tab": "ホーム",
            }
        )

    if not monthly.get("achieved") and monthly.get("remaining", 0) > 0:
        todos.append(
            {
                "text": (
                    f"月目標まであと {monthly['remaining']:,}円 "
                    f"（1日あたり約 {monthly['daily_required']:,}円）"
                ),
                "done": False,
                "tab": "検証レポート",
            }
        )

    rec_total = int(bankroll.get("recommended_total") or 0)
    if rec_total > 0:
        todos.append(
            {
                "text": f"推奨購入額 {rec_total:,}円 以内で購入（💰 資金管理）",
                "done": False,
                "tab": "資金管理",
            }
        )

    if status["races"] == 0:
        todos.append(
            {
                "text": "サイドバーから workflow を実行してデータを取得",
                "done": False,
                "tab": "設定",
            }
        )
    elif not recommend.get("has_data"):
        todos.append(
            {
                "text": "本日分のレースデータを workflow で更新",
                "done": False,
                "tab": "設定",
            }
        )

    if prediction.get("target_count", 0) > 0:
        todos.append(
            {
                "text": f"狙い目 TOP{min(3, prediction['target_count'])} を確認（⭐ 今日のAIおすすめ）",
                "done": False,
                "tab": "今日のAIおすすめ",
            }
        )

    if prediction.get("danger_count", 0) > 0:
        todos.append(
            {
                "text": f"危険人気 {prediction['danger_count']} 件を確認",
                "done": False,
                "tab": "今日のAIおすすめ",
            }
        )

    if prediction.get("skip_count", 0) > 0:
        todos.append(
            {
                "text": f"見送り {prediction['skip_count']} レースを確認（🎯 実戦判定）",
                "done": False,
                "tab": "実戦判定",
            }
        )

    buy_n = len(battle.get("buy_candidates") or [])
    if buy_n > 0:
        todos.append(
            {
                "text": f"実戦判定で買い候補 {buy_n} 件を確認",
                "done": False,
                "tab": "実戦判定",
            }
        )

    if market.get("needs_poll_hint"):
        todos.append(
            {
                "text": "市場監視でオッズ再取得（急変検知のため2回以上）",
                "done": False,
                "tab": "市場監視",
            }
        )

    pending = pnl.get("summary_actual", {}).get("pending", 0)
    if pending > 0:
        todos.append(
            {
                "text": f"収支検証で未確定 {pending} 件を結果反映",
                "done": False,
                "tab": "収支検証",
            }
        )

    val_today = validation.get("today", {}).get("summary", {})
    if val_today.get("settled", 0) == 0 and status["results"] > 0:
        todos.append(
            {
                "text": "検証レポートを更新して成績を確認",
                "done": False,
                "tab": "検証レポート",
            }
        )

    remaining = max(0, 100 - quality.get("valid_races", 0))
    if remaining > 0 and quality.get("valid_races", 0) < 100:
        todos.append(
            {
                "text": f"有効データをあと {remaining} レース集める（学習精度向上）",
                "done": False,
                "tab": "学習状況",
            }
        )

    last_ops = (ops.get("last_started_at") or "")[:10].replace("-", "")
    if last_ops != today and ops.get("auto_enabled"):
        todos.append(
            {
                "text": "ホームの「今日の自動実行」で一括処理",
                "done": False,
                "tab": "ホーム",
            }
        )

    if not todos:
        todos.append(
            {
                "text": "今日のAIおすすめと実戦判定を確認",
                "done": True,
                "tab": "今日のAIおすすめ",
            }
        )
    return todos


def get_home_dashboard_bundle(
    *,
    bet_type: str,
    status: dict,
    recommend: dict,
    battle: dict,
    market: dict,
    pnl: dict,
    validation: dict,
    quality: dict,
    ops: dict,
    bankroll: dict,
    learning: dict,
    data_progress: dict,
) -> dict:
    trust = data_progress.get("trust") or {}
    prediction = build_daily_prediction_summary(
        recommend=recommend,
        battle=battle,
        trust=trust,
    )
    monthly = get_monthly_goal_bundle(
        bet_type,
        bankroll=bankroll,
        trust_level=trust.get("level", "insufficient"),
    )
    learning_snap = build_learning_snapshot(learning=learning, validation=validation)
    todos = build_today_todos(
        status=status,
        recommend=recommend,
        battle=battle,
        market=market,
        pnl=pnl,
        validation=validation,
        quality=quality,
        ops=ops,
        monthly=monthly,
        bankroll=bankroll,
        prediction=prediction,
    )
    return {
        "pillars": APP_PILLARS,
        "prediction": prediction,
        "monthly": monthly,
        "learning_snap": learning_snap,
        "bankroll": bankroll,
        "todos": todos,
    }
