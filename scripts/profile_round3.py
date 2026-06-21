"""Round3: learning fetch_missing=False / line 再取得抑制 / キャッシュ優先の計測"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BET = "3連単"
OUT = ROOT / "scripts" / "benchmark_results" / "round3_after.json"


def _db_line_stats() -> dict:
    from config import DB_PATH
    from race_features import line_info_needs_api_fetch

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT race_id, line_info FROM races").fetchall()
    conn.close()
    api_missing = sum(1 for _, li in rows if line_info_needs_api_fetch(li))
    unknown = sum(1 for _, li in rows if li == "不明")
    return {
        "race_count": len(rows),
        "api_missing_line_info": api_missing,
        "unknown_line_info": unknown,
    }


def _install_hooks() -> dict:
    import race_features as rf

    stats = {"api_calls": 0, "sleep_count": 0, "sleep_sec": 0.0}
    _orig_fetch = rf.fetch_line_forecast
    _orig_sleep = time.sleep

    def patched_fetch(race_id: str):
        stats["api_calls"] += 1
        return _orig_fetch(race_id)

    def patched_sleep(sec: float):
        stats["sleep_count"] += 1
        stats["sleep_sec"] += float(sec)
        return _orig_sleep(sec)

    rf.fetch_line_forecast = patched_fetch  # type: ignore[method-assign]
    time.sleep = patched_sleep  # type: ignore[assignment]
    return stats


def _run(label: str, fn, stats: dict) -> dict:
    stats.clear()
    stats.update({"api_calls": 0, "sleep_count": 0, "sleep_sec": 0.0})
    t0 = time.perf_counter()
    fn()
    wall = time.perf_counter() - t0
    return {
        "label": label,
        "wall_sec": round(wall, 3),
        "api_calls": stats["api_calls"],
        "sleep_count": stats["sleep_count"],
        "sleep_sec": round(stats["sleep_sec"], 3),
    }


def main() -> None:
    import race_features as rf
    from bundle_cache import build_full_app_bundles
    from learning import compute_learning_patterns, save_learned_patterns
    from line_analysis import get_line_analysis_bundle

    db = _db_line_stats()
    print("DB", json.dumps(db, ensure_ascii=False))

    stats = _install_hooks()
    results: dict = {"db": db, "runs": []}

    rf.clear_race_metrics_cache()
    results["runs"].append(
        _run(
            "build_race_metrics(fetch_missing=True)",
            lambda: rf.build_race_metrics(BET, fetch_missing=True),
            stats,
        )
    )

    rf.clear_race_metrics_cache()
    results["runs"].append(
        _run(
            "build_race_metrics(fetch_missing=False)",
            lambda: rf.build_race_metrics(BET, fetch_missing=False),
            stats,
        )
    )

    rf.clear_race_metrics_cache()
    results["runs"].append(
        _run(
            "compute_learning_patterns",
            lambda: compute_learning_patterns(BET),
            stats,
        )
    )

    rf.clear_race_metrics_cache()
    results["runs"].append(
        _run(
            "save_learned_patterns",
            lambda: save_learned_patterns(BET),
            stats,
        )
    )

    results["runs"].append(
        _run(
            "get_line_analysis_bundle(fetch_missing=True)",
            lambda: get_line_analysis_bundle(fetch_missing=True),
            stats,
        )
    )

    results["runs"].append(
        _run(
            "get_line_analysis_bundle(fetch_missing=False)",
            lambda: get_line_analysis_bundle(fetch_missing=False),
            stats,
        )
    )

    rf.clear_race_metrics_cache()
    results["runs"].append(
        _run(
            "build_full_app_bundles (cold cache)",
            lambda: build_full_app_bundles(BET),
            stats,
        )
    )

    results["runs"].append(
        _run(
            "build_full_app_bundles (warm cache)",
            lambda: build_full_app_bundles(BET),
            stats,
        )
    )

    for row in results["runs"]:
        print(json.dumps(row, ensure_ascii=False))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT)


if __name__ == "__main__":
    main()
