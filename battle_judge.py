"""実戦判定 — AI・学習・市場・ラインを総合した買い/見送り判定"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

import pandas as pd

from ai_recommend import (
    DISCLAIMER,
    VERDICT_CHECK,
    VERDICT_SKIP,
    VERDICT_SMALL,
    _anomaly_map_for_scores,
    _filter_today,
    _is_dangerous_popular,
    _row_to_card,
    _today_date_str,
)
from pre_race import apply_pre_race_to_scores

VERDICT_BUY = "買い候補"

VERDICT_COLORS = {
    VERDICT_BUY: "#059669",
    VERDICT_SMALL: "#16a34a",
    VERDICT_CHECK: "#ca8a04",
    VERDICT_SKIP: "#64748b",
}

VERDICT_BG = {
    VERDICT_BUY: "#ecfdf5",
    VERDICT_SMALL: "#f0fdf4",
    VERDICT_CHECK: "#fefce8",
    VERDICT_SKIP: "#f8fafc",
}

DEFAULT_BASE_AMOUNT = 100

ALLOCATION_GUIDE = {
    VERDICT_BUY: {
        "per_race_yen": 300,
        "per_combo_yen": 100,
        "max_races": 2,
        "budget_pct": 40,
        "label": "本命枠",
    },
    VERDICT_SMALL: {
        "per_race_yen": 200,
        "per_combo_yen": 100,
        "max_races": 3,
        "budget_pct": 35,
        "label": "少額枠",
    },
    VERDICT_CHECK: {
        "per_race_yen": 100,
        "per_combo_yen": 100,
        "max_races": 1,
        "budget_pct": 15,
        "label": "様子見",
    },
    VERDICT_SKIP: {
        "per_race_yen": 0,
        "per_combo_yen": 0,
        "max_races": 0,
        "budget_pct": 0,
        "label": "見送り",
    },
}

DO_NOT_BUY_RULES = [
    {"id": "quality", "label": "データ品質不足", "desc": "品質スコア50未満または学習不可"},
    {"id": "danger_pop", "label": "危険人気", "desc": "人気過集中かつ総合判定が弱い"},
    {"id": "market_chaos", "label": "市場混乱", "desc": "警戒レベル75超＋急変多数"},
    {"id": "line_bad", "label": "ライン不利", "desc": "有利ラインなし＋危険ラインあり"},
    {"id": "low_pred", "label": "予測回収率低", "desc": "予測回収60%未満かつAIスコア55未満"},
    {"id": "learn_exclude", "label": "低回収学習条件", "desc": "本格学習で除外された条件に該当"},
    {"id": "pre_race_drop", "label": "直前急落", "desc": "直前補正-8以下（資金流出）"},
]


def _norm_recovery(recovery: Optional[float]) -> float:
    if recovery is None or pd.isna(recovery):
        return 50.0
    return float(max(0.0, min(100.0, (float(recovery) - 40.0) / 1.2)))


def _quality_map(quality_bundle: dict) -> dict[str, dict]:
    details = quality_bundle.get("race_details", pd.DataFrame())
    if details.empty:
        return {}
    out: dict[str, dict] = {}
    for _, row in details.iterrows():
        rid = str(row["race_id"])
        out[rid] = {
            "learnable": bool(row.get("learnable")),
            "quality_score": float(row.get("quality_score") or 0),
            "issue_text": str(row.get("issue_text") or ""),
        }
    return out


def _line_map(line_bundle: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for rep in line_bundle.get("race_reports") or []:
        rid = str(rep.get("race_id"))
        adv = rep.get("advantageous") or []
        dng = rep.get("dangerous") or []
        out[rid] = {
            "line_advantage_score": float(adv[0]["line_ai_score"]) if adv else 0.0,
            "line_danger_score": float(dng[0]["line_ai_score"]) if dng else 100.0,
            "line_advantage_label": adv[0]["line_label"] if adv else "",
            "line_danger_label": dng[0]["line_label"] if dng else "",
            "senko_line_count": int(rep.get("senko_line_count") or 0),
        }
    return out


def _market_map(market_bundle: dict) -> dict[str, dict]:
    alerts = market_bundle.get("race_alerts", pd.DataFrame())
    if alerts.empty:
        return {}
    out: dict[str, dict] = {}
    for _, row in alerts.iterrows():
        rid = str(row["race_id"])
        out[rid] = {
            "market_alert_level": float(row.get("market_alert_level") or 0),
            "sudden_count": int(row.get("sudden_count") or 0),
            "hot_sell_count": int(row.get("hot_sell_count") or 0),
            "max_change_pct": float(row.get("max_change_pct") or 0),
            "danger_favorite": bool(row.get("danger_favorite")),
        }
    return out


def _ml_map(ml_bundle: dict) -> dict[str, float]:
    pred = ml_bundle.get("predictions", pd.DataFrame())
    if pred.empty:
        return {}
    return {
        str(row["race_id"]): float(row["pred_recovery"])
        for _, row in pred.iterrows()
        if pd.notna(row.get("pred_recovery"))
    }


def _advanced_exclude_map(advanced_bundle: dict, row: pd.Series, entries: pd.DataFrame) -> list[str]:
    """本格学習の除外条件に該当するか"""
    patterns = advanced_bundle.get("patterns", pd.DataFrame())
    if patterns.empty or "excluded" not in patterns.columns:
        return []
    excluded = patterns[patterns["excluded"].astype(bool)]
    if excluded.empty:
        return []

    from learning import build_pattern_lookup

    lookup = build_pattern_lookup(excluded)
    hits: list[str] = []
    from learning import (
        _dominant_first_style,
        _line_bucket,
        _ninki_bucket,
        _race_popularity_label,
        _race_style_tag,
    )

    tags = [
        ("venue", str(row.get("venue_name") or "")),
        ("line", _line_bucket(int(row.get("line_count") or 0))),
        ("ninki", _ninki_bucket(float(row.get("ninki_concentration") or 0))),
        ("race_style", _race_style_tag(row, entries)),
        ("style", _dominant_first_style(row, entries)),
        ("popularity", _race_popularity_label(row)),
    ]
    for cat, key in tags:
        pat = lookup.get((cat, key))
        if pat:
            hits.append(str(pat.get("condition_label") or f"{cat}:{key}"))
    return hits[:3]


def _distortion_score(anomaly: dict) -> float:
    dist_cnt = int(anomaly.get("distortion_count") or 0)
    dist_max = float(anomaly.get("distortion_max") or 0)
    return min(100.0, dist_cnt * 8 + dist_max * 0.5)


def compute_composite_score(
    row: pd.Series,
    *,
    pred_recovery: Optional[float],
    quality_score: float,
    line_advantage: float,
    market_alert: float,
    distortion: float,
) -> float:
    ai = float(row.get("pre_race_score") or row.get("ai_total_score") or 0)
    learn = float(row.get("learn_adjust") or 0)
    learn_norm = max(0.0, min(100.0, 50.0 + learn * 4))
    pre_adj = float(row.get("pre_race_adjust") or 0)
    pre_norm = max(0.0, min(100.0, 50.0 + pre_adj * 2.5))
    line_norm = max(0.0, min(100.0, line_advantage))
    market_bonus = max(0.0, min(15.0, (distortion - 20) * 0.3))
    market_penalty = max(0.0, min(20.0, (market_alert - 50) * 0.3))

    composite = (
        ai * 0.25
        + _norm_recovery(pred_recovery) * 0.20
        + learn_norm * 0.15
        + min(100.0, distortion) * 0.12
        + line_norm * 0.13
        + pre_norm * 0.10
        + market_bonus
        - market_penalty
    )
    composite *= quality_score / 100.0 if quality_score > 0 else 0.8
    return round(max(0.0, min(100.0, composite)), 1)


def check_do_not_buy(
    row: pd.Series,
    *,
    composite: float,
    quality: dict,
    line: dict,
    market: dict,
    pred_recovery: Optional[float],
    anomaly: dict,
) -> tuple[list[str], list[str]]:
    """買ってはいけない条件 → (rule_ids, reasons)"""
    rule_ids: list[str] = []
    reasons: list[str] = []

    q_score = float(quality.get("quality_score") or 80)
    issue = str(quality.get("issue_text") or "")
    learnable = quality.get("learnable", True)
    if q_score < 50:
        rule_ids.append("quality")
        reasons.append(issue or "データ品質が低い")
    elif not learnable:
        # 結果未確定（当日レース）はオッズ・出走表があれば許容
        if "結果なし" in issue and q_score >= 60:
            pass
        elif q_score < 65 or "オッズなし" in issue or "entries" in issue:
            rule_ids.append("quality")
            reasons.append(issue or "データ品質が低い")

    ai = float(row.get("pre_race_score") or row.get("ai_total_score") or 0)
    if _is_dangerous_popular(row, anomaly) and composite < 58:
        rule_ids.append("danger_pop")
        reasons.append("危険人気かつ総合判定が弱い")

    alert = float(market.get("market_alert_level") or 0)
    sudden = int(market.get("sudden_count") or 0)
    if alert >= 75 and sudden >= 3 and ai < 60:
        rule_ids.append("market_chaos")
        reasons.append(f"市場警戒{alert:.0f}・急変{sudden}件")

    adv = float(line.get("line_advantage_score") or 0)
    dng = float(line.get("line_danger_score") or 100)
    senko = int(line.get("senko_line_count") or 0)
    if adv < 30 and dng < 32 and senko == 0:
        rule_ids.append("line_bad")
        reasons.append("有利ラインなし・危険ラインのみ")

    pred = pred_recovery if pred_recovery is not None else 75.0
    if pred < 60 and ai < 55:
        rule_ids.append("low_pred")
        reasons.append(f"予測回収{pred:.0f}%・AI{ai:.0f}点")

    pre_adj = float(row.get("pre_race_adjust") or 0)
    if pre_adj <= -8:
        rule_ids.append("pre_race_drop")
        reasons.append(f"直前補正{pre_adj:.0f}（資金流出）")

    return rule_ids, reasons


def judge_battle_verdict(
    composite: float,
    row: pd.Series,
    blockers: list[str],
) -> tuple[str, str, int]:
    """判定・理由・推奨金額"""
    if blockers:
        return VERDICT_SKIP, " / ".join(blockers[:3]), 0

    ai = float(row.get("pre_race_score") or row.get("ai_total_score") or 0)
    danger = float(row.get("danger_level") or 0)

    if composite >= 68 and ai >= 65 and danger < 60:
        alloc = ALLOCATION_GUIDE[VERDICT_BUY]
        return (
            VERDICT_BUY,
            f"総合{composite:.0f}点・AI{ai:.0f}点で条件良好",
            int(alloc["per_race_yen"]),
        )
    if composite >= 52 and ai >= 50 and danger < 70:
        alloc = ALLOCATION_GUIDE[VERDICT_SMALL]
        return (
            VERDICT_SMALL,
            f"総合{composite:.0f}点・少額分散を推奨",
            int(alloc["per_race_yen"]),
        )
    if composite >= 42 and ai >= 45:
        alloc = ALLOCATION_GUIDE[VERDICT_CHECK]
        return (
            VERDICT_CHECK,
            f"総合{composite:.0f}点・最終確認後に検討",
            int(alloc["per_race_yen"]),
        )
    return VERDICT_SKIP, f"総合{composite:.0f}点・期待値不足", 0


def _build_reasons(
    row: pd.Series,
    *,
    composite: float,
    pred_recovery: Optional[float],
    quality: dict,
    line: dict,
    market: dict,
    learn_excludes: list[str],
    anomaly: dict,
) -> list[str]:
    reasons: list[str] = []
    ai = float(row.get("pre_race_score") or row.get("ai_total_score") or 0)
    reasons.append(f"AIスコア{ai:.0f} / 総合{composite:.0f}点")
    if pred_recovery is not None:
        reasons.append(f"予測回収率{pred_recovery:.0f}%")
    learn = float(row.get("learn_adjust") or 0)
    if learn:
        sign = "+" if learn > 0 else ""
        reasons.append(f"学習補正{sign}{learn:.0f}pt")
    pre = float(row.get("pre_race_adjust") or 0)
    if pre:
        sign = "+" if pre > 0 else ""
        reasons.append(f"直前補正{sign}{pre:.0f}pt")

    adv = line.get("line_advantage_label")
    if adv:
        reasons.append(f"有利ライン: {adv}({line.get('line_advantage_score', 0):.0f}点)")
    alert = market.get("market_alert_level")
    if alert is not None and float(alert) >= 45:
        reasons.append(f"市場警戒{float(alert):.0f}")
    if int(anomaly.get("distortion_count") or 0):
        reasons.append(f"オッズ歪み{anomaly['distortion_count']}件")

    q = quality.get("quality_score")
    if q is not None:
        reasons.append(f"データ品質{float(q):.0f}点")
    if learn_excludes:
        reasons.append("除外条件該当: " + learn_excludes[0])
    return reasons[:8]


def _row_to_battle_card(
    row: pd.Series,
    anomaly: dict,
    *,
    quality_map: dict,
    line_map: dict,
    market_map: dict,
    ml_map: dict,
    advanced_bundle: dict,
    entries: pd.DataFrame,
) -> dict:
    rid = str(row["race_id"])
    quality = quality_map.get(rid, {"learnable": True, "quality_score": 80, "issue_text": ""})
    line = line_map.get(rid, {})
    market = market_map.get(rid, {})
    pred = ml_map.get(rid)
    distortion = _distortion_score(anomaly)

    learn_excludes = _advanced_exclude_map(advanced_bundle, row, entries)
    composite = compute_composite_score(
        row,
        pred_recovery=pred,
        quality_score=float(quality.get("quality_score") or 80),
        line_advantage=float(line.get("line_advantage_score") or 50),
        market_alert=float(market.get("market_alert_level") or 0),
        distortion=distortion,
    )
    if learn_excludes:
        composite = round(max(0.0, composite - min(12.0, len(learn_excludes) * 4)), 1)

    _, blockers = check_do_not_buy(
        row,
        composite=composite,
        quality=quality,
        line=line,
        market=market,
        pred_recovery=pred,
        anomaly=anomaly,
    )
    verdict, hint, amount = judge_battle_verdict(composite, row, blockers)
    reasons = _build_reasons(
        row,
        composite=composite,
        pred_recovery=pred,
        quality=quality,
        line=line,
        market=market,
        learn_excludes=learn_excludes,
        anomaly=anomaly,
    )

    base_card = _row_to_card(row, anomaly)
    danger = _is_dangerous_popular(row, anomaly)

    return {
        **base_card,
        "battle_verdict": verdict,
        "battle_hint": hint,
        "recommended_yen": amount,
        "composite_score": composite,
        "pred_recovery": pred,
        "quality_score": quality.get("quality_score"),
        "quality_learnable": quality.get("learnable"),
        "line_advantage_score": line.get("line_advantage_score"),
        "market_alert_level": market.get("market_alert_level"),
        "blockers": blockers,
        "battle_reasons": reasons,
        "danger_popular": danger,
        "do_not_buy": bool(blockers),
    }


def build_battle_judgments(
    bet_type: str = "3連単",
    *,
    scores: Optional[pd.DataFrame] = None,
    market: Optional[dict] = None,
    line: Optional[dict] = None,
    pre_race: Optional[dict] = None,
    ml: Optional[dict] = None,
    quality: Optional[dict] = None,
    advanced: Optional[dict] = None,
    base_amount: int = DEFAULT_BASE_AMOUNT,
) -> dict:
    """実戦判定バンドル"""
    from ai_score import build_race_scores
    from analyze import load_entries_frame

    if scores is None:
        scores = build_race_scores(bet_type)
    if scores.empty:
        return {
            "has_data": False,
            "today": date.today().strftime("%Y%m%d"),
            "disclaimer": DISCLAIMER,
            "buy_candidates": [],
            "small_candidates": [],
            "check_candidates": [],
            "skip_candidates": [],
            "dangerous_popular": [],
            "do_not_buy_rules": DO_NOT_BUY_RULES,
            "allocation_guide": ALLOCATION_GUIDE,
            "base_amount": base_amount,
            "all_cards": [],
        }

    if pre_race is None:
        from pre_race import get_pre_race_bundle

        pre_race = get_pre_race_bundle(bet_type)
    scores = apply_pre_race_to_scores(scores, bet_type, pre_race)

    if market is None:
        from market_monitor import get_market_monitor_bundle

        market = get_market_monitor_bundle(bet_type)
    if line is None:
        from line_analysis import get_line_analysis_bundle

        line = get_line_analysis_bundle()
    if ml is None:
        from ml_model import get_ml_bundle

        ml = get_ml_bundle(bet_type, scores=scores, retrain=False)
    if quality is None:
        from data_quality import get_quality_bundle

        quality = get_quality_bundle(bet_type)
    if advanced is None:
        from advanced_learning import get_advanced_learning_bundle

        advanced = get_advanced_learning_bundle(bet_type, retrain=False)

    anomaly_map = _anomaly_map_for_scores(bet_type)
    quality_map = _quality_map(quality)
    line_map = _line_map(line)
    market_map = _market_map(market)
    ml_map = _ml_map(ml)
    entries = load_entries_frame()

    today = _today_date_str(scores)
    today_scores = _filter_today(scores, today)

    cards: list[dict] = []
    for _, row in today_scores.iterrows():
        anom = anomaly_map.get(row["race_id"], {})
        cards.append(
            _row_to_battle_card(
                row,
                anom,
                quality_map=quality_map,
                line_map=line_map,
                market_map=market_map,
                ml_map=ml_map,
                advanced_bundle=advanced,
                entries=entries,
            )
        )

    buy = [c for c in cards if c["battle_verdict"] == VERDICT_BUY]
    small = [c for c in cards if c["battle_verdict"] == VERDICT_SMALL]
    check = [c for c in cards if c["battle_verdict"] == VERDICT_CHECK]
    skip = [c for c in cards if c["battle_verdict"] == VERDICT_SKIP]
    danger = [c for c in cards if c["danger_popular"]]

    for group in (buy, small, check, skip, danger):
        group.sort(key=lambda x: x.get("composite_score", 0), reverse=True)

    total_buy_yen = sum(c["recommended_yen"] for c in buy + small + check)

    return {
        "has_data": True,
        "today": today,
        "bet_type": bet_type,
        "disclaimer": DISCLAIMER,
        "race_count": len(cards),
        "buy_candidates": buy,
        "small_candidates": small,
        "check_candidates": check,
        "skip_candidates": skip,
        "dangerous_popular": danger,
        "do_not_buy_rules": DO_NOT_BUY_RULES,
        "allocation_guide": ALLOCATION_GUIDE,
        "base_amount": base_amount,
        "total_recommended_yen": total_buy_yen,
        "all_cards": sorted(cards, key=lambda x: x.get("composite_score", 0), reverse=True),
    }


def get_battle_judge_bundle(
    bet_type: str = "3連単",
    *,
    scores: Optional[pd.DataFrame] = None,
    market: Optional[dict] = None,
    line: Optional[dict] = None,
    pre_race: Optional[dict] = None,
    ml: Optional[dict] = None,
    quality: Optional[dict] = None,
    advanced: Optional[dict] = None,
    base_amount: int = DEFAULT_BASE_AMOUNT,
) -> dict:
    bundle = build_battle_judgments(
        bet_type,
        scores=scores,
        market=market,
        line=line,
        pre_race=pre_race,
        ml=ml,
        quality=quality,
        advanced=advanced,
        base_amount=base_amount,
    )
    bundle["lines"] = build_battle_judge_lines(bundle)
    return bundle


def build_battle_judge_lines(bundle: Optional[dict] = None, bet_type: str = "3連単") -> list[str]:
    b = bundle or build_battle_judgments(bet_type)
    lines = [f"【実戦判定】券種={b.get('bet_type', bet_type)}  対象日={b.get('today', '')}", b.get("disclaimer", ""), ""]
    if not b.get("has_data"):
        lines.append("データがありません。")
        return lines

    lines.append(f"  対象レース: {b['race_count']}")
    lines.append(f"  推奨合計: {b.get('total_recommended_yen', 0)}円")
    lines.append("")

    for title, key in [
        ("買い候補", "buy_candidates"),
        ("少額候補", "small_candidates"),
        ("要確認", "check_candidates"),
        ("見送り", "skip_candidates"),
    ]:
        lines.append(f"--- {title} ({len(b[key])}件) ---")
        for c in b[key][:5]:
            lines.append(
                f"  {c['venue_name']} {c['race_no']}R "
                f"総合{c['composite_score']:.0f} "
                f"推奨{c['recommended_yen']}円 — {c['battle_hint']}"
            )
        lines.append("")

    lines.append(f"--- 危険人気 ({len(b['dangerous_popular'])}件) ---")
    for c in b["dangerous_popular"][:5]:
        lines.append(f"  {c['venue_name']} {c['race_no']}R — {c.get('danger_reason', '')}")
    lines.append("")

    lines.append("--- 資金配分目安 ---")
    for verdict, guide in ALLOCATION_GUIDE.items():
        if verdict == VERDICT_SKIP:
            continue
        lines.append(
            f"  {verdict}: {guide['per_race_yen']}円/レース "
            f"(最大{guide['max_races']}R・予算{guide['budget_pct']}%)"
        )
    lines.append("")
    return lines
