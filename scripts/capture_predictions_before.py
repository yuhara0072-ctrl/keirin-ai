"""修正前コミットの worktree から同一 DB で予想を取得"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WT = ROOT.parent / "keirin_ai_before_measure"
BEFORE_COMMIT = "27da620"
OUT = ROOT / "scripts" / "benchmark_results" / "predictions_before.json"
DB = ROOT / "data" / "keirin.db"


def main() -> int:
    if not WT.exists():
        subprocess.run(
            ["git", "worktree", "add", str(WT), BEFORE_COMMIT],
            cwd=ROOT,
            check=True,
        )

    runner = WT / "_capture_run.py"
    runner.write_text(
        """
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from ai_recommend import build_daily_recommendations
from ai_score import get_ai_score_bundle

BET = "3連単"
sb = get_ai_score_bundle(BET, fetch_missing_lines=False)
scores = sb["scores"]
rec = build_daily_recommendations(BET, scores=scores)
cols = [
    c
    for c in [
        "race_id", "race_date", "venue_name", "race_no",
        "ai_total_score", "ev_rank", "line_info", "learn_adjust", "learn_adjust",
    ]
    if c in scores.columns
]
data = {
    "bet_type": BET,
    "today": rec.get("today"),
    "scores": scores[cols].fillna("").astype(str).to_dict(orient="records"),
    "targets": rec.get("targets") or [],
    "global_picks": rec.get("global_picks") or [],
}
Path(sys.argv[1]).write_text(
    json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
    encoding="utf-8",
)
print("ok")
""",
        encoding="utf-8",
    )

    import os

    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "KEIRIN_LOAD_DIAG": "0",
        "DATABASE_PATH": str(DB),
    }
    proc = subprocess.run(
        [sys.executable, str(runner), str(OUT)],
        cwd=WT,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return proc.returncode
    print(proc.stdout.strip())
    print(f"WROTE {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
