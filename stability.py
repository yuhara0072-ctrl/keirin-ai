"""安定化検証 — workflow / 永続化 / 再起動 / ログイン / 復元の一連チェック"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DB_PATH, PERSIST_DIR


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class CycleResult:
    cycle: int
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)


def _table_count(table: str) -> int:
    from db import get_connection, safe_table_count, table_exists

    conn = get_connection()
    try:
        if not table_exists(conn, table):
            return 0
        return safe_table_count(conn, table)
    finally:
        conn.close()


def get_data_counts() -> dict[str, int]:
    return {
        "races": _table_count("races"),
        "results": _table_count("results"),
        "learning": _table_count("learned_patterns"),
    }


def seed_stability_test_data(*, race_id: str = "209901011201") -> dict[str, int]:
    """ネットワーク不要の最小データ（workflow 相当）"""
    from db import init_db

    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM odds")
        conn.execute("DELETE FROM entries")
        conn.execute("DELETE FROM results")
        conn.execute("DELETE FROM learned_patterns")
        conn.execute("DELETE FROM races")

        conn.execute(
            """
            INSERT INTO races (race_id, race_date, venue_code, venue_name, race_no)
            VALUES (?, '2099-01-01', '99', '安定化テスト', 1)
            """,
            (race_id,),
        )
        conn.execute(
            """
            INSERT INTO results (race_id, finish_order, trifecta_pay, exacta_pay)
            VALUES (?, '1-2-3', 1000, 500)
            """,
            (race_id,),
        )
        conn.execute(
            """
            INSERT INTO learned_patterns (
                bet_type, category, condition_key, condition_label,
                races, recovery_rate, hit_rate, score_adjust, updated_at
            ) VALUES ('3連単', 'venue', 'test99', '安定化テスト場', 1, 120.0, 50.0, 5.0, datetime('now'))
            """
        )
        conn.commit()
    finally:
        conn.close()
    return get_data_counts()


def simulate_render_restart() -> None:
    """Render 再起動相当 — SQLite のみ削除（persist/ は残す）"""
    if DB_PATH.exists():
        DB_PATH.unlink()
    wal = Path(str(DB_PATH) + "-wal")
    shm = Path(str(DB_PATH) + "-shm")
    wal.unlink(missing_ok=True)
    shm.unlink(missing_ok=True)


def check_workflow() -> StepResult:
    try:
        counts_before = seed_stability_test_data()
        if counts_before["races"] < 1:
            return StepResult("workflow実行", False, "race 投入失敗")
        return StepResult(
            "workflow実行",
            True,
            f"races={counts_before['races']} results={counts_before['results']}",
        )
    except Exception as exc:
        return StepResult("workflow実行", False, str(exc))


def check_github_save() -> StepResult:
    try:
        from github_persist import execute_workflow_persist_with_print, load_local_snapshot

        sync_result, _ = execute_workflow_persist_with_print("stability")
        local = load_local_snapshot()
        local_races = len((local or {}).get("races.json") or [])
        github_ok = sync_result.get("github") or not sync_result.get("github_enabled")
        local_ok = local_races > 0 and sync_result.get("race_count", 0) > 0

        if not local_ok:
            return StepResult(
                "GitHub保存",
                False,
                f"local保存失敗 export={sync_result.get('race_count')} local={local_races}",
            )
        if not github_ok:
            return StepResult(
                "GitHub保存",
                True,
                f"local OK ({local_races} races) / GitHubスキップ: {sync_result.get('message', '')}",
            )
        put = sync_result.get("races_json_put") or {}
        return StepResult(
            "GitHub保存",
            True,
            f"github={sync_result.get('github')} HTTP={put.get('status_code')} races={local_races}",
        )
    except Exception as exc:
        return StepResult("GitHub保存", False, str(exc))


def check_restart_and_restore() -> StepResult:
    try:
        before = get_data_counts()
        if before["races"] < 1:
            return StepResult("Render再起動", False, "保存前に race がありません")

        simulate_render_restart()
        if DB_PATH.exists():
            return StepResult("Render再起動", False, "DBファイルが残っています")

        from github_persist import ensure_data_restored

        restore = ensure_data_restored()
        after = get_data_counts()
        ok = after["races"] >= before["races"] and after["results"] >= before["results"]
        return StepResult(
            "Render再起動",
            ok,
            f"restore={restore} after races={after['races']} results={after['results']} "
            f"learning={after['learning']}",
        )
    except Exception as exc:
        return StepResult("Render再起動", False, str(exc))


def check_relogin() -> StepResult:
    """再ログイン相当 — DB 空の状態から bootstrap"""
    try:
        from db import bootstrap_database

        simulate_render_restart()
        outcome = bootstrap_database()
        after = get_data_counts()
        ok = outcome.get("race_count", 0) > 0 and after["races"] > 0
        return StepResult(
            "再ログイン",
            ok,
            f"bootstrap ok={outcome.get('ok')} races={after['races']} "
            f"restore={outcome.get('restore')}",
        )
    except Exception as exc:
        return StepResult("再ログイン", False, str(exc))


def check_restore_integrity() -> StepResult:
    try:
        counts = get_data_counts()
        snap = None
        from github_persist import load_local_snapshot

        snap = load_local_snapshot()
        meta = (snap or {}).get("meta.json") or {}
        meta_races = int(meta.get("race_count") or 0)
        meta_results = int(meta.get("result_count") or 0)
        meta_learning = int(meta.get("learning_count") or 0)

        ok = (
            counts["races"] > 0
            and counts["races"] >= meta_races
            and counts["results"] >= meta_results
            and counts["learning"] >= meta_learning
        )
        detail = (
            f"DB races={counts['races']} results={counts['results']} "
            f"learning={counts['learning']} / meta races={meta_races} "
            f"results={meta_results} learning={meta_learning}"
        )
        return StepResult("race/result/learning復元", ok, detail)
    except Exception as exc:
        return StepResult("race/result/learning復元", False, str(exc))


def run_stability_cycle(cycle: int) -> CycleResult:
    result = CycleResult(cycle=cycle)
    result.steps.append(check_workflow())
    if not result.steps[-1].ok:
        return result
    result.steps.append(check_github_save())
    if not result.steps[-1].ok:
        return result
    result.steps.append(check_restart_and_restore())
    result.steps.append(check_relogin())
    result.steps.append(check_restore_integrity())
    return result


def run_stability_suite(*, cycles: int = 3) -> tuple[bool, list[CycleResult]]:
    results: list[CycleResult] = []
    all_ok = True
    for i in range(1, cycles + 1):
        cr = run_stability_cycle(i)
        results.append(cr)
        if not cr.ok:
            all_ok = False
    return all_ok, results


def format_report(results: list[CycleResult]) -> str:
    lines = [f"安定化チェック {datetime.now().isoformat(timespec='seconds')}", ""]
    for cr in results:
        status = "PASS" if cr.ok else "FAIL"
        lines.append(f"--- サイクル {cr.cycle}: {status} ---")
        for step in cr.steps:
            mark = "OK" if step.ok else "NG"
            lines.append(f"  [{mark}] {step.name}: {step.detail}")
        lines.append("")
    return "\n".join(lines)
