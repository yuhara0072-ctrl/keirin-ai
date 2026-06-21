"""予想結果スナップショット（修正前後比較用）"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BET = "3連単"


def capture() -> dict:
    from ai_recommend import build_daily_recommendations
    from ai_score import get_ai_score_bundle

    score_bundle = get_ai_score_bundle(BET, fetch_missing_lines=False)
    scores = score_bundle["scores"]
    rec = build_daily_recommendations(BET, scores=scores)

    score_cols = [
        "race_id",
        "race_date",
        "venue_name",
        "race_no",
        "ai_total_score",
        "ev_rank",
        "line_info",
        "nige_count",
        "ninki_concentration",
        "are_index",
        "learn_adjust",
    ]
    present = [c for c in score_cols if c in scores.columns]
    score_rows = scores[present].fillna("").astype(str).to_dict(orient="records")

    targets = []
    for t in rec.get("targets") or []:
        if isinstance(t, dict):
            targets.append(
                {k: t.get(k) for k in ("race_id", "verdict", "ai_score", "ev_rank", "pick")}
            )

    return {
        "bet_type": BET,
        "today": rec.get("today"),
        "scores": score_rows,
        "targets": targets,
        "global_picks": rec.get("global_picks") or [],
        "stance": rec.get("stance"),
    }


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    data = capture()
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"WROTE {out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
