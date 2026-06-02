"""GitHub / リポジトリ JSON 永続化（Render 無料プラン向け）

SQLite は Render 上では再起動で消えるため、レースデータと学習データを
JSON として GitHub に保存し、起動時に SQLite（実行用キャッシュ）へ復元する。
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd
import requests

from config import (
    DB_PATH,
    GITHUB_PERSIST_BRANCH,
    GITHUB_REPO,
    GITHUB_REQUEST_TIMEOUT,
    GITHUB_TOKEN,
    PERSIST_DIR,
)

logger = logging.getLogger("github_persist")

PERSIST_VERSION = 1
MAX_CHUNK_BYTES = 900_000
TABLES_CORE = ("races", "entries", "odds", "results")
TABLES_ALL = (*TABLES_CORE, "learned_patterns")
ODDS_CHUNK_PREFIX = "odds_"
CORE_FILE_ORDER = (
    "meta.json",
    "races.json",
    "entries.json",
    "results.json",
    "learned_patterns.json",
)


def is_github_enabled() -> bool:
    return bool(GITHUB_TOKEN.strip() and GITHUB_REPO.strip())


def is_persist_enabled() -> bool:
    return is_github_enabled() or PERSIST_DIR.exists()


def _api_headers() -> dict[str, str]:
    token = GITHUB_TOKEN.strip()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _persist_path(name: str) -> Path:
    return PERSIST_DIR / name


def _read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _flush_db() -> None:
    """export 前に SQLite の変更を確実に反映"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def _rows_to_records(rows) -> list[dict]:
    return [dict(row) for row in rows]


def persist_races() -> list[dict]:
    """DB から races テーブルを読み出し（GitHub 保存用）"""
    from db import get_connection, table_exists

    _flush_db()
    conn = get_connection()
    try:
        if not table_exists(conn, "races"):
            return []
        rows = conn.execute("SELECT * FROM races ORDER BY race_id").fetchall()
        return _rows_to_records(rows)
    finally:
        conn.close()


def persist_results() -> list[dict]:
    """DB から results テーブルを読み出し（GitHub 保存用）"""
    from db import get_connection, table_exists

    _flush_db()
    conn = get_connection()
    try:
        if not table_exists(conn, "results"):
            return []
        rows = conn.execute("SELECT * FROM results ORDER BY race_id").fetchall()
        return _rows_to_records(rows)
    finally:
        conn.close()


def _persist_entries() -> list[dict]:
    from db import get_connection, table_exists

    conn = get_connection()
    try:
        if not table_exists(conn, "entries"):
            return []
        rows = conn.execute("SELECT * FROM entries ORDER BY race_id, bracket").fetchall()
        return _rows_to_records(rows)
    finally:
        conn.close()


def _persist_learned_patterns() -> list[dict]:
    from db import get_connection, table_exists

    conn = get_connection()
    try:
        if not table_exists(conn, "learned_patterns"):
            return []
        rows = conn.execute(
            "SELECT * FROM learned_patterns ORDER BY bet_type, category, condition_key"
        ).fetchall()
        return _rows_to_records(rows)
    finally:
        conn.close()


def _table_records(conn, table: str) -> list[dict]:
    if table not in TABLES_ALL:
        return []
    df = pd.read_sql(f"SELECT * FROM {table}", conn)
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False))


def _export_odds_chunks(conn) -> dict[str, list[dict]]:
    df = pd.read_sql("SELECT * FROM odds ORDER BY id", conn)
    if df.empty:
        return {}
    records = json.loads(df.to_json(orient="records", force_ascii=False))
    chunks: dict[str, list[dict]] = {}
    batch: list[dict] = []
    batch_name = f"{ODDS_CHUNK_PREFIX}0.json"
    size = 2
    idx = 0
    for row in records:
        row_text = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        if batch and size + len(row_text) > MAX_CHUNK_BYTES:
            chunks[batch_name] = batch
            idx += 1
            batch_name = f"{ODDS_CHUNK_PREFIX}{idx}.json"
            batch = []
            size = 2
        batch.append(row)
        size += len(row_text) + 1
    if batch:
        chunks[batch_name] = batch
    return chunks


def _db_race_count() -> int:
    from db import get_connection, safe_table_count, table_exists

    conn = get_connection()
    try:
        if not table_exists(conn, "races"):
            return 0
        return safe_table_count(conn, "races")
    finally:
        conn.close()


def _db_result_count() -> int:
    from db import get_connection, safe_table_count, table_exists

    conn = get_connection()
    try:
        if not table_exists(conn, "results"):
            return 0
        return safe_table_count(conn, "results")
    finally:
        conn.close()


def _emit_log(log_fn: Optional[Callable[[str], None]], message: str) -> None:
    logger.info(message)
    if log_fn:
        log_fn(message)


def build_snapshot_files(
    races: list[dict],
    results: list[dict],
) -> dict[str, Any]:
    entries = _persist_entries()
    patterns = _persist_learned_patterns()
    meta = {
        "version": PERSIST_VERSION,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "db_path": str(DB_PATH),
        "race_count": len(races),
        "result_count": len(results),
        "learning_count": len(patterns),
    }
    files: dict[str, Any] = {
        "meta.json": meta,
        "races.json": races,
        "entries.json": entries,
        "results.json": results,
        "learned_patterns.json": patterns,
    }
    from db import get_connection, table_exists

    conn = get_connection()
    try:
        if table_exists(conn, "odds"):
            files.update(_export_odds_chunks(conn))
    finally:
        conn.close()

    db_count = _db_race_count()
    if db_count > 0 and len(races) == 0:
        raise RuntimeError(
            f"export mismatch: DB has {db_count} races at {DB_PATH} but persist_races() returned 0"
        )
    return files


def export_snapshot() -> dict[str, Any]:
    races = persist_races()
    results = persist_results()
    return build_snapshot_files(races, results)


def _import_table(conn, table: str, records: list[dict]) -> None:
    if table not in TABLES_ALL:
        return
    conn.execute(f"DELETE FROM {table}")
    if not records:
        return
    df = pd.DataFrame(records)
    df.to_sql(table, conn, if_exists="append", index=False)


def import_snapshot(files: dict[str, Any]) -> dict:
    from db import get_connection, migrate_db
    from learning import migrate_learning_table

    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in ("odds", "entries", "results", "learned_patterns", "races"):
            if table == "odds":
                conn.execute("DELETE FROM odds")
            else:
                conn.execute(f"DELETE FROM {table}")

        _import_table(conn, "races", files.get("races.json") or [])
        _import_table(conn, "entries", files.get("entries.json") or [])

        odds_rows: list[dict] = []
        for name, payload in files.items():
            if name.startswith(ODDS_CHUNK_PREFIX) and name.endswith(".json"):
                odds_rows.extend(payload or [])
        _import_table(conn, "odds", odds_rows)
        _import_table(conn, "results", files.get("results.json") or [])
        _import_table(conn, "learned_patterns", files.get("learned_patterns.json") or [])

        migrate_db(conn)
        migrate_learning_table(conn)
        conn.commit()

        meta = files.get("meta.json") or {}
        return {
            "race_count": meta.get("race_count", len(files.get("races.json") or [])),
            "result_count": meta.get("result_count", len(files.get("results.json") or [])),
            "learning_count": meta.get(
                "learning_count", len(files.get("learned_patterns.json") or [])
            ),
        }
    finally:
        conn.close()


def save_local_snapshot(files: Optional[dict[str, Any]] = None) -> Path:
    payload = files or export_snapshot()
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in payload.items():
        _write_json_file(_persist_path(name), content)
    return PERSIST_DIR


def load_local_snapshot() -> Optional[dict[str, Any]]:
    meta_path = _persist_path("meta.json")
    if not meta_path.exists():
        return None
    files: dict[str, Any] = {"meta.json": _read_json_file(meta_path)}
    for path in sorted(PERSIST_DIR.glob("*.json")):
        if path.name == "meta.json":
            continue
        files[path.name] = _read_json_file(path)
    races = files.get("races.json") or []
    if not races:
        return None
    return files


def _github_get_file(name: str) -> tuple[Optional[str], Optional[str]]:
    url = f"https://api.github.com/repos/{GITHUB_REPO.strip()}/contents/{PERSIST_DIR.name}/{name}"
    resp = requests.get(
        url,
        headers=_api_headers(),
        params={"ref": GITHUB_PERSIST_BRANCH},
        timeout=GITHUB_REQUEST_TIMEOUT,
    )
    if resp.status_code == 404:
        return None, None
    resp.raise_for_status()
    body = resp.json()
    content = base64.b64decode(body["content"]).decode("utf-8")
    return content, body.get("sha")


def _github_put_file(name: str, content: str, message: str) -> None:
    _, sha = _github_get_file(name)
    url = f"https://api.github.com/repos/{GITHUB_REPO.strip()}/contents/{PERSIST_DIR.name}/{name}"
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_PERSIST_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    resp = requests.put(
        url,
        headers=_api_headers(),
        json=payload,
        timeout=GITHUB_REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        detail = resp.text[:500]
        raise RuntimeError(f"GitHub PUT {name} failed ({resp.status_code}): {detail}")


def _ordered_file_names(files: dict[str, Any]) -> list[str]:
    ordered: list[str] = []
    for name in CORE_FILE_ORDER:
        if name in files:
            ordered.append(name)
    for name in sorted(files.keys()):
        if name not in ordered:
            ordered.append(name)
    return ordered


def download_github_snapshot() -> Optional[dict[str, Any]]:
    if not is_github_enabled():
        return None
    meta_raw, _ = _github_get_file("meta.json")
    if not meta_raw:
        return None
    files: dict[str, Any] = {"meta.json": json.loads(meta_raw)}
    list_url = f"https://api.github.com/repos/{GITHUB_REPO.strip()}/contents/{PERSIST_DIR.name}"
    resp = requests.get(
        list_url,
        headers=_api_headers(),
        params={"ref": GITHUB_PERSIST_BRANCH},
        timeout=GITHUB_REQUEST_TIMEOUT,
    )
    if resp.status_code == 404:
        return files if files.get("meta.json") else None
    resp.raise_for_status()
    for item in resp.json():
        if item.get("type") != "file":
            continue
        name = item.get("name") or ""
        if not name.endswith(".json") or name == "meta.json":
            continue
        raw, _ = _github_get_file(name)
        if raw:
            files[name] = json.loads(raw)
    return files


def sync_to_github(
    reason: str = "update",
    *,
    log_fn: Optional[Callable[[str], None]] = None,
) -> dict:
    push_log: list[str] = []
    db_races_before = _db_race_count()
    db_results_before = _db_result_count()
    _emit_log(
        log_fn,
        f"DB件数: races={db_races_before} results={db_results_before} path={DB_PATH}",
    )

    _emit_log(log_fn, "persist_races() 呼び出し")
    races = persist_races()
    _emit_log(log_fn, f"persist_races() 完了: export={len(races)} 件")

    _emit_log(log_fn, "persist_results() 呼び出し")
    results = persist_results()
    _emit_log(log_fn, f"persist_results() 完了: export={len(results)} 件")

    files = build_snapshot_files(races, results)
    exported_races = len(races)
    exported_results = len(results)
    _emit_log(
        log_fn,
        f"export件数: races={exported_races} results={exported_results} "
        f"entries={len(files.get('entries.json') or [])} "
        f"learning={len(files.get('learned_patterns.json') or [])}",
    )

    save_local_snapshot(files)
    result = {
        "ok": True,
        "local": True,
        "github": False,
        "db_race_count": db_races_before,
        "db_result_count": db_results_before,
        "race_count": exported_races,
        "result_count": exported_results,
        "learning_count": len(files.get("learned_patterns.json") or []),
        "db_path": str(DB_PATH),
        "github_repo": GITHUB_REPO,
        "github_enabled": is_github_enabled(),
        "message": "",
        "errors": [],
        "push_log": push_log,
    }

    if not is_github_enabled():
        result["message"] = "local only (GITHUB_TOKEN / GITHUB_REPO 未設定)"
        _emit_log(log_fn, result["message"])
        return result

    if db_races_before > 0 and exported_races == 0:
        result["ok"] = False
        result["message"] = (
            f"GitHub同期を中止: DB={db_races_before}件 なのに export=0件 ({DB_PATH})"
        )
        _emit_log(log_fn, result["message"])
        return result

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_msg = f"chore: persist keirin data ({reason}) at {stamp}"
    pushed: list[str] = []
    _emit_log(
        log_fn,
        f"GitHub push 開始: repo={GITHUB_REPO} branch={GITHUB_PERSIST_BRANCH}",
    )
    try:
        for name in _ordered_file_names(files):
            text = json.dumps(files[name], ensure_ascii=False, separators=(",", ":"))
            size = len(text.encode("utf-8"))
            _emit_log(log_fn, f"GitHub PUT {name} ({size} bytes)...")
            _github_put_file(name, text, commit_msg)
            pushed.append(name)
            push_log.append(f"{name}: OK ({size} bytes)")
            _emit_log(log_fn, f"GitHub PUT {name} → OK")
        result["github"] = True
        result["message"] = (
            f"github synced ({exported_races} races, {exported_results} results)"
        )
        _emit_log(log_fn, f"GitHub push 完了: {result['message']}")
    except Exception as exc:
        result["ok"] = False
        result["errors"] = pushed
        result["message"] = str(exc)
        push_log.append(f"ERROR: {exc}")
        _emit_log(log_fn, f"GitHub push 失敗: {exc}")
        if pushed:
            _emit_log(log_fn, f"push済みファイル: {', '.join(pushed)}")
    result["push_log"] = push_log
    return result


def workflow_persist_and_sync(reason: str = "workflow") -> tuple[dict, list[str]]:
    """workflow 終了時: persist_races / persist_results を明示実行して GitHub 同期"""
    lines: list[str] = []

    def log_fn(msg: str) -> None:
        lines.append(f"  {msg}")

    lines.append("STEP 5/5: GitHub永続化")
    lines.append(f"  GITHUB_REPO={GITHUB_REPO or '(未設定)'}")
    lines.append(f"  GITHUB_TOKEN={'設定済' if GITHUB_TOKEN else '未設定'}")
    lines.append(f"  GITHUB_BRANCH={GITHUB_PERSIST_BRANCH}")

    try:
        result = sync_to_github(reason, log_fn=log_fn)
    except Exception as exc:
        result = {
            "ok": False,
            "github": False,
            "message": str(exc),
            "db_path": str(DB_PATH),
            "push_log": [],
        }
        lines.append(f"  永続化エラー: {exc}")

    lines.append(f"  結果: {format_sync_result(result)}")
    for entry in result.get("push_log") or []:
        lines.append(f"  push: {entry}")
    return result, lines


def restore_if_needed() -> Optional[dict]:
    from db import get_connection, safe_table_count, table_exists

    conn = get_connection()
    try:
        if table_exists(conn, "races") and safe_table_count(conn, "races") > 0:
            return None
    finally:
        conn.close()

    snapshot = None
    source = ""
    if is_github_enabled():
        try:
            snapshot = download_github_snapshot()
            source = "github"
        except Exception:
            snapshot = None
    if snapshot is None:
        snapshot = load_local_snapshot()
        source = "local" if snapshot else ""

    if not snapshot:
        return None

    races = snapshot.get("races.json") or []
    if not races:
        return None

    stats = import_snapshot(snapshot)
    stats["source"] = source
    return stats


def maybe_sync(reason: str) -> dict:
    if not is_persist_enabled():
        return {"ok": True, "skipped": True}
    try:
        return sync_to_github(reason)
    except Exception as exc:
        return {"ok": False, "message": str(exc), "db_path": str(DB_PATH)}


def format_sync_result(result: dict) -> str:
    if result.get("skipped"):
        return "永続化スキップ"
    parts = [
        f"ok={result.get('ok')}",
        f"github={result.get('github')}",
        f"export_races={result.get('race_count', 0)}",
        f"export_results={result.get('result_count', 0)}",
        f"db_races={result.get('db_race_count', 0)}",
        f"db_results={result.get('db_result_count', 0)}",
        f"path={result.get('db_path', DB_PATH)}",
    ]
    if result.get("message"):
        parts.append(str(result["message"]))
    if result.get("errors"):
        parts.append(f"pushed={','.join(result['errors'])}")
    return " / ".join(parts)
