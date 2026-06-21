"""修正後効果測定 — 詳細読込・今日タブ・MISSING_FETCH・API/sleep・予想一致"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "scripts" / "benchmark_results"
BET = "3連単"
BEFORE_COMMIT = "27da620"
BEFORE_JSON = OUT_DIR / "detail_load_before.json"
BEFORE_TAB_JSON = OUT_DIR / "baseline.json"


def _reset_diag() -> None:
    from load_diagnostics import LoadDiagnostics

    d = LoadDiagnostics
    d.api_calls = 0
    d.github_api_calls = 0
    d.keirin_api_calls = 0
    d.other_api_calls = 0
    d.sleep_count = 0
    d.sleep_total_sec = 0.0
    d.missing_fetch_count = 0
    d.missing_fetch_by_kind = {}
    d.missing_fetch_races = set()
    d.loop_counts = {}
    d.api_errors = 0
    d.slow_api_count = 0


def _diag_snapshot() -> dict:
    from load_diagnostics import LoadDiagnostics

    d = LoadDiagnostics
    return {
        "missing_fetch": d.missing_fetch_count,
        "missing_fetch_by_kind": dict(d.missing_fetch_by_kind),
        "missing_fetch_races": sorted(d.missing_fetch_races),
        "api_calls": d.api_calls,
        "keirin_api_calls": d.keirin_api_calls,
        "github_api_calls": d.github_api_calls,
        "sleep_count": d.sleep_count,
        "sleep_sec": round(d.sleep_total_sec, 3),
    }


def _install_keirin_hooks() -> dict:
    import race_features as rf

    stats = {"keirin_api": 0, "sleep_count": 0, "sleep_sec": 0.0}
    _orig_fetch = rf.fetch_line_forecast
    _orig_sleep = time.sleep

    def patched_fetch(race_id: str):
        stats["keirin_api"] += 1
        return _orig_fetch(race_id)

    def patched_sleep(sec: float):
        stats["sleep_count"] += 1
        stats["sleep_sec"] += float(sec)
        return _orig_sleep(sec)

    rf.fetch_line_forecast = patched_fetch  # type: ignore[method-assign]
    time.sleep = patched_sleep  # type: ignore[assignment]
    return stats


def _measure_detail_load() -> dict:
    from bundle_cache import build_full_app_bundles
    from load_diagnostics import install_load_diagnostics
    from race_features import clear_race_metrics_cache

    install_load_diagnostics()
    _reset_diag()
    clear_race_metrics_cache()
    hook = _install_keirin_hooks()

    t0 = time.perf_counter()
    build_full_app_bundles(BET)
    wall = time.perf_counter() - t0
    diag = _diag_snapshot()

    return {
        "detail_load_sec": round(wall, 3),
        "keirin_api_hook": hook["keirin_api"],
        "sleep_hook": hook["sleep_count"],
        "sleep_sec_hook": round(hook["sleep_sec"], 3),
        **diag,
    }


def _measure_today_tab() -> dict:
    from ai_recommend import get_ai_recommend_bundle
    from ai_score import get_ai_score_bundle
    from load_diagnostics import install_load_diagnostics
    from race_features import clear_race_metrics_cache

    install_load_diagnostics()
    _reset_diag()
    clear_race_metrics_cache()
    hook = _install_keirin_hooks()

    t0 = time.perf_counter()
    scores = get_ai_score_bundle(BET, fetch_missing_lines=False)["scores"]
    get_ai_recommend_bundle(BET, scores=scores)
    wall = time.perf_counter() - t0
    diag = _diag_snapshot()

    return {
        "today_tab_sec": round(wall, 3),
        "keirin_api_hook": hook["keirin_api"],
        "sleep_hook": hook["sleep_count"],
        "sleep_sec_hook": round(hook["sleep_sec"], 3),
        **diag,
    }


def _load_before_baseline() -> dict:
    if not BEFORE_JSON.exists():
        return {}
    return json.loads(BEFORE_JSON.read_text(encoding="utf-8"))


def _load_before_tab_rec() -> float | None:
    if not BEFORE_TAB_JSON.exists():
        return None
    rows = json.loads(BEFORE_TAB_JSON.read_text(encoding="utf-8"))
    for r in rows:
        if r.get("key") == "rec":
            return float(r["seconds"])
    return None


def _compare_predictions(after_path: Path, before_path: Path) -> dict:
    if not before_path.exists() or not after_path.exists():
        return {"status": "skip", "reason": "snapshot missing"}

    a = json.loads(after_path.read_text(encoding="utf-8"))
    b = json.loads(before_path.read_text(encoding="utf-8"))

    def norm_scores(d: dict) -> list[dict]:
        return sorted(d.get("scores") or [], key=lambda x: x.get("race_id", ""))

    sa, sb = norm_scores(a), norm_scores(b)
    score_match = sa == sb
    targets_match = (a.get("targets") or []) == (b.get("targets") or [])
    global_match = (a.get("global_picks") or []) == (b.get("global_picks") or [])

    diff_races = []
    sb_map = {r["race_id"]: r for r in sb if "race_id" in r}
    for row in sa:
        rid = row.get("race_id")
        old = sb_map.get(rid)
        if old is None:
            diff_races.append({"race_id": rid, "reason": "new"})
            continue
        for k in ("ai_total_score", "ev_rank", "line_info", "learn_adjust"):
            if row.get(k) != old.get(k):
                diff_races.append(
                    {"race_id": rid, "field": k, "before": old.get(k), "after": row.get(k)}
                )

    return {
        "status": "ok",
        "scores_identical": score_match,
        "targets_identical": targets_match,
        "global_picks_identical": global_match,
        "all_identical": score_match and targets_match and global_match,
        "diff_count": len(diff_races),
        "diffs": diff_races[:20],
        "today_before": b.get("today"),
        "today_after": a.get("today"),
    }


def _ensure_before_worktree() -> Path | None:
    wt = ROOT.parent / "keirin_ai_before_measure"
    if wt.exists() and (wt / "main.py").exists():
        return wt
    try:
        subprocess.run(
            ["git", "worktree", "add", str(wt), BEFORE_COMMIT],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return wt
    except subprocess.CalledProcessError as exc:
        print("worktree failed:", exc.stderr or exc.stdout, file=sys.stderr)
        return None


def _capture_before_predictions(out_path: Path) -> bool:
    import subprocess as sp

    proc = sp.run(
        [sys.executable, str(ROOT / "scripts" / "capture_predictions_before.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return False
    return out_path.exists()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    after_pred = OUT_DIR / "predictions_after.json"
    before_pred = OUT_DIR / "predictions_before.json"
    effect_json = OUT_DIR / "fix_effect_measurement.json"

    # 予想（修正後）
    subprocess.run(
        [sys.executable, "scripts/capture_predictions.py", str(after_pred)],
        cwd=ROOT,
        check=True,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "KEIRIN_LOAD_DIAG": "0"},
    )

    # 予想（修正前コミット）
    _capture_before_predictions(before_pred)

    pred_cmp = _compare_predictions(after_pred, before_pred)

    detail = _measure_detail_load()
    today = _measure_today_tab()

    before = _load_before_baseline()
    before_rec = _load_before_tab_rec()

    result = {
        "scenario": "warm_db_local",
        "before_baseline_file": str(BEFORE_JSON),
        "before_commit": BEFORE_COMMIT,
        "detail_load": {
            "before_sec": before.get("load_app_bundles_sec"),
            "after_sec": detail["detail_load_sec"],
            "before_api": before.get("load_app_bundles_api"),
            "after_api": detail["api_calls"],
            "before_keirin_api_hook": before.get("load_app_bundles_api"),
            "after_keirin_api_hook": detail["keirin_api_hook"],
            "before_sleep": before.get("load_app_bundles_sleep_sec"),
            "after_sleep_sec": detail["sleep_sec"],
            "before_missing_fetch": "あり（line_info=不明で再API）",
            "after_missing_fetch": detail["missing_fetch"],
            "after_missing_fetch_detail": detail,
        },
        "today_tab": {
            "before_sec": before_rec,
            "after_sec": today["today_tab_sec"],
            "after_api": today["api_calls"],
            "after_sleep_count": today["sleep_count"],
            "after_missing_fetch": today["missing_fetch"],
            "after_detail": today,
        },
        "predictions": pred_cmp,
    }

    effect_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nWROTE {effect_json}")


if __name__ == "__main__":
    main()
