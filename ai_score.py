"""AIスコア — 期待値の高いレース・買い目を数値化"""

from typing import Optional

import pandas as pd

from analyze import SENKO_STYLES, first_bracket, load_entries_frame
from detect_anomaly import detect_all, load_latest_odds_frame
from learning import apply_learning_adjustment, load_learned_patterns, save_learned_patterns
from race_features import build_race_metrics, venue_trends

RANK_THRESHOLDS = [(80, "S"), (65, "A"), (50, "B"), (35, "C"), (0, "D")]


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _ev_rank(score: float) -> str:
    for threshold, label in RANK_THRESHOLDS:
        if score >= threshold:
            return label
    return "D"


def _line_head_brackets(line_info: str) -> set[int]:
    """先頭ラインの先頭車番"""
    if not line_info or line_info == "不明":
        return set()
    first_group = str(line_info).split("|")[0].strip()
    parts = first_group.split("-")
    out: set[int] = set()
    for p in parts:
        if p.strip().isdigit():
            out.add(int(p.strip()))
    return out


def _race_anomaly_map(anomalies: pd.DataFrame, bet_type: str) -> dict[str, dict]:
    """race_id -> {distortion_max, value_combos, fav_overbet}"""
    if anomalies.empty:
        return {}
    sub = anomalies[
        (anomalies["bet_type"] == bet_type) & (anomalies["race_id"] != "(集計)")
    ]
    out: dict[str, dict] = {}
    for race_id, grp in sub.groupby("race_id"):
        distort = grp[grp["anomaly_type"] == "オッズ歪み"]
        out[race_id] = {
            "distortion_max": float(distort["score"].max()) if not distort.empty else 0.0,
            "distortion_count": len(distort),
            "value_combos": set(distort["combination"].astype(str)),
            "messages": distort["message"].head(3).tolist(),
        }
    return out


def _venue_lookup(venues: pd.DataFrame) -> dict[str, dict]:
    if venues.empty:
        return {}
    return {
        str(row["venue_name"]): {
            "honmei_rate": float(row.get("honmei_rate", 0)),
            "man_ticket_rate": float(row.get("man_ticket_rate", 0)),
            "avg_are_index": float(row.get("avg_are_index", 50)),
        }
        for _, row in venues.iterrows()
    }


def _score_race_row(
    row: pd.Series,
    anomaly: dict,
    venue: dict,
    entries: pd.DataFrame,
    score_weights: Optional[dict[str, float]] = None,
) -> dict:
    """加点方式でレーススコアを算出"""
    race_id = row["race_id"]
    ent = entries[entries["race_id"] == race_id]

    # --- オッズ歪み (+0〜25) ---
    dist_max = anomaly.get("distortion_max", 0)
    dist_cnt = anomaly.get("distortion_count", 0)
    pts_distortion = min(25, dist_max * 0.4 + dist_cnt * 2)

    # --- 人気集中率 (+0〜15) ---
    ninki = float(row.get("ninki_concentration") or 0)
    if 8 <= ninki <= 14:
        pts_ninki = 12
    elif 5 <= ninki < 8 or 14 < ninki <= 18:
        pts_ninki = 7
    elif ninki > 20:
        pts_ninki = 3
    else:
        pts_ninki = 5

    # --- 逃げ人数 (+0〜12) ---
    nige = int(row.get("nige_count") or 0)
    if nige == 1:
        pts_nige = 10
    elif nige == 2:
        pts_nige = 8
    elif nige == 0:
        pts_nige = 4
    else:
        pts_nige = 6

    # --- 競輪場傾向 (+0〜15) ---
    v = venue or {}
    honmei_v = v.get("honmei_rate", 30)
    man_v = v.get("man_ticket_rate", 20)
    if man_v >= 25 and honmei_v <= 35:
        pts_venue = 14
    elif man_v >= 15:
        pts_venue = 9
    else:
        pts_venue = 5

    # --- 脚質傾向 (+0〜13) ---
    senko = int(row.get("senko_count") or 0)
    pts_style = 0
    if senko == 1:
        pts_style += 8
    if not ent.empty:
        nige_br = set(ent.loc[ent["style"] == "逃", "bracket"].astype(int))
        line_heads = _line_head_brackets(str(row.get("line_info") or ""))
        if line_heads & nige_br:
            pts_style += 5
    pts_style = min(13, pts_style)

    # --- ライン (+0〜10) ---
    line_count = int(row.get("line_count") or 0)
    pts_line = 8 if 2 <= line_count <= 3 else (4 if line_count else 0)

    w = score_weights or {}
    pts_distortion *= float(w.get("distortion", 1.0))
    pts_ninki *= float(w.get("ninki", 1.0))
    pts_nige *= float(w.get("nige", 1.0))
    pts_venue *= float(w.get("venue", 1.0))
    pts_style *= float(w.get("style", 1.0))
    pts_line *= float(w.get("line", 1.0))

    raw_total = pts_distortion + pts_ninki + pts_nige + pts_venue + pts_style + pts_line
    ai_total = round(_clamp(raw_total), 1)

    are_index = float(row.get("are_index") or 0)
    fav_odds = float(row.get("fav_odds") or 99)

    # 危険度: 荒れ・集中・高配当狙い
    danger = _clamp(
        are_index * 0.45
        + max(0, ninki - 12) * 2
        + max(0, 15 - fav_odds) * 0.5
        + (10 if nige >= 3 else 0)
        + min(15, dist_cnt * 2),
    )

    # 荒れ予想
    are_forecast = _clamp(
        are_index * 0.55
        + (15 if nige >= 2 else 0)
        + (10 if ninki < 8 else 0)
        + man_v * 0.2
        - (honmei_v * 0.15 if honmei_v > 40 else 0),
    )

    # 本命信頼度
    honmei_trust = _clamp(
        ninki * 2.5
        + max(0, 25 - fav_odds) * 1.2
        + honmei_v * 0.35
        + (8 if senko == 1 else 0)
        - are_index * 0.25
        - (10 if dist_max > 15 and ninki > 15 else 0),
    )

    return {
        "ai_total_score": ai_total,
        "danger_level": round(danger, 1),
        "are_forecast": round(are_forecast, 1),
        "honmei_trust": round(honmei_trust, 1),
        "ev_rank": _ev_rank(ai_total),
        "score_breakdown": (
            f"歪み+{pts_distortion:.0f} 人気+{pts_ninki:.0f} 逃げ+{pts_nige:.0f} "
            f"場+{pts_venue:.0f} 脚質+{pts_style:.0f} ライン+{pts_line:.0f}"
        ),
        "_anomaly": anomaly,
    }


def _combo_pick_score(
    combo_row: pd.Series,
    race_row: pd.Series,
    entries: pd.DataFrame,
    anomaly_combos: set[str],
) -> float:
    """買い目1点の期待値スコア"""
    race_id = race_row["race_id"]
    odds = float(combo_row["odds"])
    dist = float(combo_row.get("distortion_ratio") or 1.0)
    ninki = int(combo_row.get("ninki_rank") or 99)
    prob_share = float(combo_row.get("prob_share") or 0)

    score = 0.0
    # 市場の過小評価（歪み）
    if str(combo_row["combination"]) in anomaly_combos:
        score += 28
    if 2.0 <= dist <= 12:
        score += min(22, (dist - 1) * 4)
    elif dist > 12:
        score += 8

    # 人気帯: 中穴ゾーンを優遇
    if 3 <= ninki <= 8:
        score += 18
    elif ninki == 1 or ninki == 2:
        score += 10 if float(race_row.get("honmei_trust") or 0) >= 55 else 4
    elif 9 <= ninki <= 15:
        score += 12
    else:
        score += 3

    # 脚質・ライン整合
    fb = first_bracket(str(combo_row["combination"]))
    if fb is not None and not entries.empty:
        ent = entries[(entries["race_id"] == race_id) & (entries["bracket"] == fb)]
        if not ent.empty:
            style = str(ent.iloc[0]["style"])
            if style in SENKO_STYLES:
                score += 10
            line_heads = _line_head_brackets(str(race_row.get("line_info") or ""))
            if fb in line_heads:
                score += 8

    # オッズ妥当帯（極端高配当は減点）
    if odds < 3:
        score += 5
    elif 3 <= odds <= 40:
        score += 12
    elif 40 < odds <= 120:
        score += 6
    else:
        score -= 5

    # 本命集中時は本命以外に歪みボーナス
    if ninki > 2 and float(race_row.get("ninki_concentration") or 0) > 12:
        score += min(10, prob_share * 200)

    return round(score, 1)


def recommend_top3_combos(
    race_id: str,
    odds_df: pd.DataFrame,
    race_row: pd.Series,
    entries: pd.DataFrame,
    anomaly_combos: set[str],
    bet_type: str = "3連単",
) -> list[dict]:
    sub = odds_df[(odds_df["race_id"] == race_id) & (odds_df["bet_type"] == bet_type)].copy()
    if sub.empty:
        return []

    n = len(sub)
    fair = 1.0 / n if n else 0
    sub["implied"] = 1.0 / sub["odds"]
    total = sub["implied"].sum()
    sub["prob_share"] = sub["implied"] / total if total else 0
    sub["distortion_ratio"] = sub["prob_share"] / fair if fair else 1
    sub["ninki_rank"] = sub["odds"].rank(method="first", ascending=True).astype(int)

    picks: list[dict] = []
    for _, crow in sub.iterrows():
        s = _combo_pick_score(crow, race_row, entries, anomaly_combos)
        picks.append(
            {
                "combination": str(crow["combination"]),
                "odds": float(crow["odds"]),
                "ninki_rank": int(crow["ninki_rank"]),
                "pick_score": s,
                "distortion_ratio": round(float(crow["distortion_ratio"]), 2),
            }
        )

    picks.sort(key=lambda x: x["pick_score"], reverse=True)
    top = picks[:3]
    for i, p in enumerate(top, 1):
        p["rank"] = i
    return top


def build_race_scores(
    bet_type: str = "3連単",
    *,
    score_weights: Optional[dict[str, float]] = None,
    patterns: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    return build_race_scores_with_options(
        bet_type,
        score_weights=score_weights,
        patterns=patterns,
    )


def build_race_scores_with_options(
    bet_type: str = "3連単",
    *,
    score_weights: Optional[dict[str, float]] = None,
    patterns: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """レース単位AIスコア一覧（本格学習の重み・パターン指定可）"""
    metrics = build_race_metrics(bet_type)
    if metrics.empty:
        return pd.DataFrame()

    anomalies = detect_all(bet_type)
    anomaly_map = _race_anomaly_map(anomalies, bet_type)
    venues = _venue_lookup(venue_trends(metrics))
    entries = load_entries_frame()
    odds_df = load_latest_odds_frame()

    if patterns is None:
        try:
            from advanced_learning import load_advanced_patterns

            patterns = load_advanced_patterns(bet_type)
            if not patterns.empty and "excluded" in patterns.columns:
                patterns = patterns[~patterns["excluded"].astype(bool)]
        except Exception:
            patterns = pd.DataFrame()
        if patterns.empty:
            patterns = load_learned_patterns(bet_type)
            if patterns.empty:
                save_learned_patterns(bet_type)
                patterns = load_learned_patterns(bet_type)
    elif patterns.empty:
        patterns = load_learned_patterns(bet_type)

    if score_weights is None:
        try:
            from advanced_learning import get_score_weights

            score_weights = get_score_weights(bet_type)
        except Exception:
            score_weights = None

    rows: list[dict] = []
    for _, row in metrics.iterrows():
        race_id = row["race_id"]
        anom = anomaly_map.get(race_id, {})
        venue = venues.get(str(row["venue_name"]), {})
        scored = _score_race_row(row, anom, venue, entries, score_weights)
        scored.pop("_anomaly", None)

        learn_pts, learn_reasons = apply_learning_adjustment(row, patterns, entries)
        if learn_pts:
            scored["ai_total_score"] = round(
                _clamp(float(scored["ai_total_score"]) + learn_pts), 1
            )
            scored["ev_rank"] = _ev_rank(scored["ai_total_score"])
        scored["learn_adjust"] = learn_pts
        scored["learn_reasons"] = " / ".join(learn_reasons) if learn_reasons else ""
        base = scored.get("score_breakdown", "")
        if learn_pts:
            sign = "+" if learn_pts > 0 else ""
            scored["score_breakdown"] = f"{base} 学習{sign}{learn_pts:.0f}"

        race_scored = {**row.to_dict(), **scored}
        top3 = recommend_top3_combos(
            race_id, odds_df, pd.Series(race_scored), entries,
            anom.get("value_combos", set()), bet_type,
        )
        for i in range(3):
            if i < len(top3):
                t = top3[i]
                race_scored[f"pick{i+1}_combo"] = t["combination"]
                race_scored[f"pick{i+1}_odds"] = t["odds"]
                race_scored[f"pick{i+1}_score"] = t["pick_score"]
                race_scored[f"pick{i+1}_ninki"] = t["ninki_rank"]
            else:
                race_scored[f"pick{i+1}_combo"] = ""
                race_scored[f"pick{i+1}_odds"] = None
                race_scored[f"pick{i+1}_score"] = None
                race_scored[f"pick{i+1}_ninki"] = None

        picks_str = " / ".join(
            f"{t['rank']}位 {t['combination']}({t['odds']}倍・{t['pick_score']}pt)"
            for t in top3
        )
        race_scored["top3_picks"] = picks_str
        rows.append(race_scored)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("ai_total_score", ascending=False)
    return df


def build_ai_score_lines(bet_type: str = "3連単") -> list[str]:
    df = build_race_scores(bet_type)
    lines = [f"【AIスコア・おすすめ】券種={bet_type}", ""]
    if df.empty:
        lines.append("データがありません。workflow を実行してください。")
        lines.append("")
        return lines

    lines.append("--- レース別スコア（期待値順） ---")
    show_cols = [
        "race_id", "venue_name", "race_no", "ai_total_score", "ev_rank",
        "danger_level", "are_forecast", "honmei_trust",
        "ninki_concentration", "are_index", "top3_picks",
    ]
    lines.append(df[show_cols].to_string(index=False))
    lines.append("")

    lines.append("--- スコア内訳（上位） ---")
    for _, row in df.head(10).iterrows():
        lines.append(
            f"  {row['race_id']} {row['venue_name']} {row['race_no']}R "
            f"総合{row['ai_total_score']} [{row['ev_rank']}] {row['score_breakdown']}"
        )
    lines.append("")
    return lines


def get_ai_score_bundle(bet_type: str = "3連単") -> dict:
    """Streamlit 用"""
    scores = build_race_scores(bet_type)
    return {
        "scores": scores,
        "lines": build_ai_score_lines(bet_type),
        "top_races": scores.head(10) if not scores.empty else pd.DataFrame(),
    }
