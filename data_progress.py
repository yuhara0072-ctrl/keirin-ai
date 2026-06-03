"""データ収集進捗とAI信頼度"""

from __future__ import annotations

from config import DATA_MILESTONES, TARGET_RACES, TARGET_RACES_FULL, TARGET_RACES_MID

TRUST_INSUFFICIENT = "insufficient"
TRUST_REFERENCE = "reference"
TRUST_VERIFIABLE = "verifiable"
TRUST_BATTLE = "battle"

TRUST_LABELS = {
    TRUST_INSUFFICIENT: "データ不足",
    TRUST_REFERENCE: "参考レベル",
    TRUST_VERIFIABLE: "検証可能",
    TRUST_BATTLE: "実戦レベル",
}

TRUST_HINTS = {
    TRUST_INSUFFICIENT: (
        f"有効データが{TARGET_RACES}レース未満です。"
        "パターン学習・検証には不十分 — 100レース収集を優先してください。"
    ),
    TRUST_REFERENCE: (
        f"{TARGET_RACES}レース到達 — 傾向の参考程度。"
        "実戦判断は控えめに、検証データの追加収集を続けてください。"
    ),
    TRUST_VERIFIABLE: (
        f"{TARGET_RACES_MID}レース到達 — 検証レポートで戦略評価が可能。"
        "改善提案と組み合わせて精度を高められます。"
    ),
    TRUST_BATTLE: (
        f"{TARGET_RACES_FULL}レース到達 — 十分なデータ量。"
        "実戦判定・資金管理を本格的に活用できます。"
    ),
}


def get_ai_trust_level(valid_races: int) -> dict:
    """有効レース数からAI信頼度を判定"""
    n = max(0, int(valid_races))
    if n >= TARGET_RACES_FULL:
        level = TRUST_BATTLE
    elif n >= TARGET_RACES_MID:
        level = TRUST_VERIFIABLE
    elif n >= TARGET_RACES:
        level = TRUST_REFERENCE
    else:
        level = TRUST_INSUFFICIENT

    return {
        "level": level,
        "label": TRUST_LABELS[level],
        "hint": TRUST_HINTS[level],
        "valid_races": n,
        "next_milestone": _next_milestone(n),
    }


def _next_milestone(valid_races: int) -> int | None:
    for target in DATA_MILESTONES:
        if valid_races < target:
            return target
    return None


def get_milestone_progress(valid_races: int) -> list[dict]:
    """100 / 300 / 1000 レース目標の進捗"""
    n = max(0, int(valid_races))
    rows: list[dict] = []
    for target in DATA_MILESTONES:
        ratio = min(n / target, 1.0) if target else 0.0
        rows.append(
            {
                "target": target,
                "current": n,
                "remaining": max(0, target - n),
                "ratio": ratio,
                "pct": round(ratio * 100, 1),
                "done": n >= target,
            }
        )
    return rows


def get_data_progress_bundle(
    *,
    total_races: int = 0,
    valid_races: int = 0,
    result_races: int = 0,
) -> dict:
    """ホーム画面用 — 保存件数・進捗・信頼度"""
    trust = get_ai_trust_level(valid_races)
    milestones = get_milestone_progress(valid_races)
    return {
        "saved_total": int(total_races),
        "saved_valid": int(valid_races),
        "saved_results": int(result_races),
        "milestones": milestones,
        "trust": trust,
    }


def get_light_data_progress_bundle(
    *,
    total_races: int = 0,
    result_races: int = 0,
    valid_races: int | None = None,
) -> dict:
    """詳細バンドル未読み込み時 — DB 件数だけでホームを表示"""
    valid = int(valid_races if valid_races is not None else result_races)
    return get_data_progress_bundle(
        total_races=int(total_races),
        valid_races=valid,
        result_races=int(result_races),
    )
