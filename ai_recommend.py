"""AIおすすめ — 毎日の判断補助（自動購入ではありません）"""

from datetime import date
from typing import Optional

import pandas as pd

from ai_score import build_race_scores
from detect_anomaly import detect_all
from pre_race import apply_pre_race_to_scores, get_pre_race_bundle

DISCLAIMER = (
    "※ 本表示は投資判断の補助です。自動購入・必中保証ではありません。"
    "最終判断はご自身で行ってください。"
)

VERDICT_SKIP = "見送り"
VERDICT_SMALL = "少額"
VERDICT_CHECK = "要確認"

VERDICT_COLORS = {
    VERDICT_SMALL: "#16a34a",
    VERDICT_CHECK: "#ca8a04",
    VERDICT_SKIP: "#dc2626",
}

VERDICT_BG = {
    VERDICT_SMALL: "#f0fdf4",
    VERDICT_CHECK: "#fefce8",
    VERDICT_SKIP: "#fef2f2",
}


def _today_date_str(scores: pd.DataFrame) -> str:
    if scores.empty or "race_date" not in scores.columns:
        return date.today().strftime("%Y%m%d")
    return str(scores["race_date"].max())


def _filter_today(scores: pd.DataFrame, today: str) -> pd.DataFrame:
    if scores.empty:
        return scores
    today_rows = scores[scores["race_date"].astype(str) == today]
    return today_rows if not today_rows.empty else scores


def judge_verdict(
    ai_score: float,
    danger: float,
    are_forecast: float,
    ninki_concentration: float,
) -> tuple[str, str]:
    """買うなら少額 / 見送り / 要確認"""
    if ai_score < 45 or danger >= 75:
        return VERDICT_SKIP, "スコア不足または危険度が高すぎます"
    if danger >= 65 and ai_score < 58:
        return VERDICT_SKIP, "リスクが期待値を上回っています"
    if are_forecast >= 78 and ninki_concentration < 8:
        return VERDICT_SKIP, "荒れ予想が強く本命も不安定です"
    if ai_score >= 65 and danger < 55 and are_forecast < 70:
        return VERDICT_SMALL, "条件が揃っています（少額・分散を推奨）"
    if ai_score >= 58 and danger < 62:
        return VERDICT_CHECK, "有望だが波乱要素あり。オッズ最終確認を"
    if ai_score >= 50:
        return VERDICT_CHECK, "期待値は中程度。無理な追いは避けてください"
    return VERDICT_SKIP, "データ上の期待値が低いです"


def build_score_reasons(
    row: pd.Series,
    anomaly: Optional[dict] = None,
) -> list[str]:
    """AIスコアの理由（箇条書き）"""
    reasons: list[str] = []
    anom = anomaly or {}

    dist_cnt = int(anom.get("distortion_count") or 0)
    if dist_cnt > 0:
        reasons.append(f"オッズ歪みを{dist_cnt}件検知（市場の偏りあり）")
    elif float(row.get("ai_total_score") or 0) >= 55:
        reasons.append("オッズ歪みは弱いが他条件でスコア確保")

    ninki = float(row.get("ninki_concentration") or 0)
    if 8 <= ninki <= 14:
        reasons.append(f"人気集中率{ninki}%は中穴狙い向きの帯")
    elif ninki > 18:
        reasons.append(f"人気集中率{ninki}%は本命への過集中（警戒）")
    elif ninki < 6:
        reasons.append(f"人気集中率{ninki}%は票が分散（荒れやすい）")

    nige = int(row.get("nige_count") or 0)
    if nige == 1:
        reasons.append("逃げ1名で展開が読みやすい")
    elif nige >= 2:
        reasons.append(f"逃げ{nige}名で先行争い・荒れ要素あり")
    elif nige == 0:
        reasons.append("逃げ不在で捲り・差し主体の可能性")

    are_idx = float(row.get("are_index") or 0)
    are_fc = float(row.get("are_forecast") or 0)
    if are_fc >= 70:
        reasons.append(f"荒れ予想{are_fc:.0f}・荒れ指数{are_idx:.0f}（波乱注意）")
    elif are_fc < 45:
        reasons.append(f"荒れ予想{are_fc:.0f}で比較的堅い展開")

    honmei_tr = float(row.get("honmei_trust") or 0)
    if honmei_tr >= 60:
        reasons.append(f"本命信頼度{honmei_tr:.0f}（本命決着も視野）")
    elif honmei_tr < 40:
        reasons.append(f"本命信頼度{honmei_tr:.0f}（本命薄め）")

    line = str(row.get("line_info") or "")
    if line and line != "不明":
        reasons.append(f"ライン: {line}")

    breakdown = str(row.get("score_breakdown") or "")
    if breakdown:
        reasons.append(f"内訳: {breakdown}")

    for msg in (anom.get("messages") or [])[:2]:
        reasons.append(f"検知: {msg}")

    return reasons[:8]


def _is_dangerous_popular(row: pd.Series, anomaly: Optional[dict] = None) -> bool:
    """危険な人気レース判定"""
    ninki = float(row.get("ninki_concentration") or 0)
    danger = float(row.get("danger_level") or 0)
    are_fc = float(row.get("are_forecast") or 0)
    fav_odds = float(row.get("fav_odds") or 99)

    if ninki >= 16 and danger >= 50:
        return True
    if ninki >= 14 and are_fc >= 65:
        return True
    if fav_odds < 8 and are_fc >= 60:
        return True
    anom = anomaly or {}
    for msg in anom.get("messages") or []:
        if "1番人気" in msg or "資金集中" in msg or "過集中" in msg:
            return True
    return False


def _danger_reason(row: pd.Series, anomaly: Optional[dict] = None) -> str:
    parts = []
    ninki = float(row.get("ninki_concentration") or 0)
    if ninki >= 14:
        parts.append(f"人気集中{ninki}%")
    if float(row.get("are_forecast") or 0) >= 65:
        parts.append("荒れ予想高")
    if float(row.get("fav_odds") or 99) < 10:
        parts.append(f"本命{row.get('fav_odds')}倍と人気過多のリスク")
    anom = anomaly or {}
    if anom.get("messages"):
        parts.append(anom["messages"][0][:40])
    return " / ".join(parts) if parts else "人気・波乱リスクが高い組み合わせ"


def _row_to_card(row: pd.Series, anomaly: Optional[dict] = None) -> dict:
    score_for_verdict = float(
        row.get("pre_race_score") or row.get("ai_total_score") or 0
    )
    verdict, hint = judge_verdict(
        score_for_verdict,
        float(row.get("danger_level") or 0),
        float(row.get("are_forecast") or 0),
        float(row.get("ninki_concentration") or 0),
    )
    picks = []
    for i in (1, 2, 3):
        combo = row.get(f"pick{i}_combo")
        if combo:
            picks.append(
                {
                    "rank": i,
                    "combination": combo,
                    "odds": row.get(f"pick{i}_odds"),
                    "ninki": row.get(f"pick{i}_ninki"),
                    "score": row.get(f"pick{i}_score"),
                }
            )
    reasons = build_score_reasons(row, anomaly)
    pre_adj = float(row.get("pre_race_adjust") or 0)
    if pre_adj:
        sign = "+" if pre_adj > 0 else ""
        reasons.insert(
            0,
            f"直前補正{sign}{pre_adj:.0f} → 補正後スコア{row.get('pre_race_score', score_for_verdict)}",
        )
    pre_reasons = str(row.get("pre_race_reasons") or "")
    if pre_reasons:
        reasons.insert(1 if pre_adj else 0, f"直前: {pre_reasons}")

    return {
        "race_id": row["race_id"],
        "race_date": row.get("race_date"),
        "venue_name": row["venue_name"],
        "race_no": row["race_no"],
        "ai_total_score": row["ai_total_score"],
        "pre_race_adjust": pre_adj,
        "pre_race_score": row.get("pre_race_score", row["ai_total_score"]),
        "pre_race_reasons": pre_reasons,
        "ev_rank": row["ev_rank"],
        "danger_level": row["danger_level"],
        "are_forecast": row["are_forecast"],
        "honmei_trust": row["honmei_trust"],
        "ninki_concentration": row.get("ninki_concentration"),
        "line_info": row.get("line_info"),
        "verdict": verdict,
        "verdict_hint": hint,
        "reasons": reasons,
        "picks": picks,
        "danger_popular": _is_dangerous_popular(row, anomaly),
        "danger_reason": _danger_reason(row, anomaly) if _is_dangerous_popular(row, anomaly) else "",
    }


def _global_top_picks(scores: pd.DataFrame, limit: int = 3) -> list[dict]:
    """全レース横断の期待値買い目TOP3"""
    rows: list[dict] = []
    for _, r in scores.iterrows():
        for i in (1, 2, 3):
            combo = r.get(f"pick{i}_combo")
            sc = r.get(f"pick{i}_score")
            if combo and sc is not None:
                rows.append(
                    {
                        "combination": combo,
                        "odds": r.get(f"pick{i}_odds"),
                        "ninki_rank": r.get(f"pick{i}_ninki"),
                        "pick_score": float(sc),
                        "race_id": r["race_id"],
                        "venue_name": r["venue_name"],
                        "race_no": r["race_no"],
                        "race_label": f"{r['venue_name']} {r['race_no']}R",
                        "ai_total_score": r["ai_total_score"],
                        "verdict": r.get("verdict"),
                    }
                )
    rows.sort(key=lambda x: x["pick_score"], reverse=True)
    seen: set[str] = set()
    out: list[dict] = []
    for item in rows:
        key = f"{item['race_id']}:{item['combination']}"
        if key in seen:
            continue
        seen.add(key)
        item["global_rank"] = len(out) + 1
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _anomaly_map_for_scores(bet_type: str) -> dict[str, dict]:
    from ai_score import _race_anomaly_map

    return _race_anomaly_map(detect_all(bet_type), bet_type)


def build_daily_recommendations(
    bet_type: str = "3連単",
    scores: Optional[pd.DataFrame] = None,
) -> dict:
    """毎日の判断補助バンドル"""
    if scores is None:
        scores = build_race_scores(bet_type)
    if scores.empty:
        return {
            "has_data": False,
            "today": date.today().strftime("%Y%m%d"),
            "disclaimer": DISCLAIMER,
            "targets": [],
            "skip_races": [],
            "dangerous_popular": [],
            "global_picks": [],
            "all_cards": [],
        }

    pre_bundle = get_pre_race_bundle(bet_type)
    scores = apply_pre_race_to_scores(scores, bet_type, pre_bundle)

    anomaly_map = _anomaly_map_for_scores(bet_type)
    today = _today_date_str(scores)
    today_scores = _filter_today(scores, today)

    cards: list[dict] = []
    for _, row in today_scores.iterrows():
        anom = anomaly_map.get(row["race_id"], {})
        card = _row_to_card(row, anom)
        cards.append(card)

    # verdict を scores に反映（global picks 用）
    verdict_map = {c["race_id"]: c["verdict"] for c in cards}
    today_scores = today_scores.copy()
    today_scores["verdict"] = today_scores["race_id"].map(verdict_map)

    targets = [
        c for c in cards
        if c["verdict"] != VERDICT_SKIP
    ]
    targets.sort(
        key=lambda x: float(x.get("pre_race_score") or x["ai_total_score"]),
        reverse=True,
    )
    targets = targets[:3]

    skip_races = [c for c in cards if c["verdict"] == VERDICT_SKIP]
    skip_races.sort(key=lambda x: x["ai_total_score"])

    dangerous = [c for c in cards if c["danger_popular"]]
    dangerous.sort(key=lambda x: (x["ninki_concentration"] or 0), reverse=True)

    global_picks = _global_top_picks(today_scores, limit=3)

    return {
        "has_data": True,
        "today": today,
        "disclaimer": DISCLAIMER,
        "bet_type": bet_type,
        "race_count": len(cards),
        "targets": targets,
        "skip_races": skip_races,
        "dangerous_popular": dangerous,
        "global_picks": global_picks,
        "all_cards": sorted(
            cards,
            key=lambda x: float(x.get("pre_race_score") or x["ai_total_score"]),
            reverse=True,
        ),
        "pre_race_bundle": pre_bundle,
    }


def build_recommend_lines(bet_type: str = "3連単") -> list[str]:
    rec = build_daily_recommendations(bet_type)
    lines = [
        f"【AIおすすめ・毎日の判断補助】券種={bet_type}  対象日={rec['today']}",
        rec["disclaimer"],
        "",
    ]
    if not rec["has_data"]:
        lines.append("データがありません。")
        lines.append("")
        return lines

    lines.append(f"--- 今日の狙い目 TOP3 ({len(rec['targets'])}件) ---")
    for c in rec["targets"]:
        lines.append(
            f"  [{c['verdict']}] {c['venue_name']} {c['race_no']}R "
            f"スコア{c['ai_total_score']} ({c['ev_rank']}) — {c['verdict_hint']}"
        )
        for p in c["picks"][:3]:
            lines.append(f"    買い目: {p['combination']} ({p['odds']}倍)")
    lines.append("")

    lines.append(f"--- 見送りレース ({len(rec['skip_races'])}件) ---")
    for c in rec["skip_races"]:
        lines.append(
            f"  {c['venue_name']} {c['race_no']}R スコア{c['ai_total_score']} — {c['verdict_hint']}"
        )
    lines.append("")

    lines.append(f"--- 危険な人気レース ({len(rec['dangerous_popular'])}件) ---")
    for c in rec["dangerous_popular"]:
        lines.append(
            f"  {c['venue_name']} {c['race_no']}R — {c.get('danger_reason', '')}"
        )
    lines.append("")

    lines.append("--- 期待値買い目 TOP3（全レース横断） ---")
    for p in rec["global_picks"]:
        lines.append(
            f"  {p['global_rank']}位 {p['combination']} @ {p['race_label']} "
            f"({p['odds']}倍・スコア{p['pick_score']})"
        )
    lines.append("")
    return lines


def get_ai_recommend_bundle(
    bet_type: str = "3連単",
    scores: Optional[pd.DataFrame] = None,
) -> dict:
    rec = build_daily_recommendations(bet_type, scores=scores)
    rec["lines"] = build_recommend_lines(bet_type) if scores is None else _lines_from_rec(rec)
    return rec


def _lines_from_rec(rec: dict) -> list[str]:
    """既に build 済みの rec からテキスト行を生成（再計算なし）"""
    bet_type = rec.get("bet_type", "3連単")
    lines = [
        f"【AIおすすめ・毎日の判断補助】券種={bet_type}  対象日={rec['today']}",
        rec["disclaimer"],
        "",
    ]
    if not rec["has_data"]:
        lines.append("データがありません。")
        return lines
    for title, key in [
        ("今日の狙い目 TOP3", "targets"),
        ("見送りレース", "skip_races"),
        ("危険な人気レース", "dangerous_popular"),
    ]:
        lines.append(f"--- {title} ---")
        for c in rec[key]:
            if key == "dangerous_popular":
                lines.append(f"  {c['venue_name']} {c['race_no']}R — {c.get('danger_reason', '')}")
            else:
                lines.append(
                    f"  [{c.get('verdict', '')}] {c['venue_name']} {c['race_no']}R "
                    f"スコア{c['ai_total_score']}"
                )
        lines.append("")
    lines.append("--- 期待値買い目 TOP3 ---")
    for p in rec["global_picks"]:
        lines.append(f"  {p['global_rank']}位 {p['combination']} @ {p['race_label']}")
    lines.append("")
    return lines
