"""AI予測強化 — レポート用テキスト生成"""

from typing import Optional

import pandas as pd

from race_features import (
    build_race_metrics,
    overall_rates,
    recovery_by_feature,
    venue_trends,
)


def build_ai_insights_lines(bet_type: str = "3連単") -> list[str]:
    metrics = build_race_metrics(bet_type)
    lines = [
        f"【AI予測強化指標】券種={bet_type}",
        "",
    ]
    if metrics.empty:
        lines.append("データがありません。workflow を実行してください。")
        lines.append("")
        return lines

    rates = overall_rates(metrics)
    lines.append("--- 全体サマリー ---")
    lines.append(f"  対象レース: {rates.get('races', 0)}")
    lines.append(f"  平均人気集中率: {rates.get('avg_ninki_concentration', 0)}%")
    lines.append(f"  平均荒れ指数: {rates.get('avg_are_index', 0)}")
    lines.append(f"  本命決着率: {rates.get('honmei_settle_rate', 0)}%")
    lines.append(f"  万車券率: {rates.get('man_ticket_rate', 0)}%")
    lines.append(f"  平均逃げ人数: {rates.get('avg_nige_count', 0)}名")
    lines.append("")

    vt = venue_trends(metrics)
    lines.append("--- 競輪場別傾向 ---")
    lines.append(vt.to_string(index=False) if not vt.empty else "  （データなし）")
    lines.append("")

    lines.append("--- ライン本数別 回収率 ---")
    rl = recovery_by_feature(bet_type, "line_count", metrics)
    lines.append(rl.to_string(index=False) if not rl.empty else "  （データなし）")
    lines.append("")

    lines.append("--- 逃げ人数別 回収率 ---")
    rn = recovery_by_feature(bet_type, "nige_count", metrics)
    lines.append(rn.to_string(index=False) if not rn.empty else "  （データなし）")
    lines.append("")

    lines.append("--- 人気集中率帯別 回収率 ---")
    rc = recovery_by_feature(bet_type, "ninki_concentration", metrics)
    lines.append(rc.to_string(index=False) if not rc.empty else "  （データなし）")
    lines.append("")

    lines.append("--- 荒れ指数帯別 回収率 ---")
    ra = recovery_by_feature(bet_type, "are_index", metrics)
    lines.append(ra.to_string(index=False) if not ra.empty else "  （データなし）")
    lines.append("")

    lines.append("--- レース別指標（直近） ---")
    show_cols = [
        "race_id",
        "venue_name",
        "race_no",
        "line_info",
        "nige_count",
        "ninki_concentration",
        "are_index",
        "honmei_settle",
        "man_ticket",
        "trifecta_pay",
    ]
    lines.append(metrics[show_cols].tail(20).to_string(index=False))
    lines.append("")
    return lines


def get_ai_insights_bundle(bet_type: str = "3連単") -> dict:
    """Streamlit 用"""
    metrics = build_race_metrics(bet_type)
    return {
        "metrics": metrics,
        "venue_trends": venue_trends(metrics),
        "overall": overall_rates(metrics),
        "recovery_line": recovery_by_feature(bet_type, "line_count", metrics),
        "recovery_nige": recovery_by_feature(bet_type, "nige_count", metrics),
        "recovery_ninki": recovery_by_feature(bet_type, "ninki_concentration", metrics),
        "recovery_are": recovery_by_feature(bet_type, "are_index", metrics),
        "lines": build_ai_insights_lines(bet_type),
    }
