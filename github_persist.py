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
    if "id" in df.columns:
        df = df.drop(columns=["id"])
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

        from db import safe_table_count, table_exists

        return {
            "race_count": safe_table_count(conn, "races")
            if table_exists(conn, "races")
            else 0,
            "result_count": safe_table_count(conn, "results")
            if table_exists(conn, "results")
            else 0,
            "learning_count": safe_table_count(conn, "learned_patterns")
            if table_exists(conn, "learned_patterns")
            else 0,
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


def _guard_against_empty_races_overwrite(name: str, content: str) -> None:
    """GitHub 上の既存 races.json を空配列で上書きしない（データ消失防止）"""
    if name != "races.json":
        return
    try:
        new_rows = json.loads(content)
    except json.JSONDecodeError:
        return
    if new_rows:
        return
    existing_raw, _ = _github_get_file(name)
    if not existing_raw:
        return
    try:
        old_rows = json.loads(existing_raw)
    except json.JSONDecodeError:
        return
    if old_rows:
        raise RuntimeError(
            f"データ保護: 空の races.json で既存 {len(old_rows)} 件を上書きしません"
        )


def _github_put_file(name: str, content: str, message: str) -> dict[str, Any]:
    """GitHub Contents API で1ファイル更新。status_code 等を返す"""
    if is_github_enabled() and name == "races.json":
        try:
            _guard_against_empty_races_overwrite(name, content)
        except RuntimeError as exc:
            return {
                "ok": False,
                "file": name,
                "status_code": None,
                "error_message": str(exc),
            }
    url = f"https://api.github.com/repos/{GITHUB_REPO.strip()}/contents/{PERSIST_DIR.name}/{name}"
    try:
        _, sha = _github_get_file(name)
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
        status_code = resp.status_code
        if status_code >= 400:
            return {
                "ok": False,
                "file": name,
                "status_code": status_code,
                "error_message": resp.text[:500],
            }
        return {
            "ok": True,
            "file": name,
            "status_code": status_code,
            "error_message": "",
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "file": name,
            "status_code": 0,
            "error_message": str(exc),
        }


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
    races: Optional[list[dict]] = None,
    results: Optional[list[dict]] = None,
) -> dict:
    push_log: list[str] = []
    db_races_before = _db_race_count()
    db_results_before = _db_result_count()
    _emit_log(
        log_fn,
        f"DB件数: races={db_races_before} results={db_results_before} path={DB_PATH}",
    )

    if races is None:
        _emit_log(log_fn, "persist_races() 呼び出し")
        races = persist_races()
        _emit_log(log_fn, f"persist_races() 完了: export={len(races)} 件")
    if results is None:
        _emit_log(log_fn, "persist_results() 呼び出し")
        results = persist_results()
        _emit_log(log_fn, f"persist_results() 完了: export={len(results)} 件")

    exported_races = len(races)
    exported_results = len(results)
    files = build_snapshot_files(races, results)
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
        "races_json_put": {
            "ok": False,
            "file": "races.json",
            "status_code": None,
            "error_message": "",
            "skipped": True,
        },
    }

    if not is_github_enabled():
        result["ok"] = exported_races > 0
        result["message"] = "local only (GITHUB_TOKEN / GITHUB_REPO 未設定)"
        result["races_json_put"]["error_message"] = result["message"]
        result["races_json_put"]["skipped"] = True
        _emit_log(log_fn, result["message"])
        return result

    if db_races_before > 0 and exported_races == 0:
        result["ok"] = False
        result["message"] = (
            f"GitHub同期を中止: DB={db_races_before}件 なのに export=0件 ({DB_PATH})"
        )
        result["races_json_put"]["error_message"] = result["message"]
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
            put_result = _github_put_file(name, text, commit_msg)
            if name == "races.json":
                result["races_json_put"] = put_result
                status = put_result.get("status_code")
                _emit_log(
                    log_fn,
                    f"GitHub PUT races.json 結果={'OK' if put_result.get('ok') else '失敗'} "
                    f"HTTP={status if status is not None else 'N/A'}",
                )
                if put_result.get("error_message"):
                    _emit_log(log_fn, f"GitHub PUT races.json エラー: {put_result['error_message']}")
            if not put_result.get("ok"):
                raise RuntimeError(
                    f"GitHub PUT {name} failed (HTTP {put_result.get('status_code')}): "
                    f"{put_result.get('error_message')}"
                )
            pushed.append(name)
            push_log.append(
                f"{name}: OK HTTP {put_result.get('status_code')} ({size} bytes)"
            )
            _emit_log(log_fn, f"GitHub PUT {name} → OK HTTP {put_result.get('status_code')}")
        result["github"] = True
        result["message"] = (
            f"github synced ({exported_races} races, {exported_results} results)"
        )
        _emit_log(log_fn, f"GitHub push 完了: {result['message']}")
    except Exception as exc:
        result["ok"] = False
        result["errors"] = pushed
        result["message"] = str(exc)
        if not result["races_json_put"].get("error_message"):
            result["races_json_put"]["error_message"] = str(exc)
        push_log.append(f"ERROR: {exc}")
        _emit_log(log_fn, f"GitHub push 失敗: {exc}")
        if pushed:
            _emit_log(log_fn, f"push済みファイル: {', '.join(pushed)}")
    result["push_log"] = push_log
    return result


def format_workflow_persist_detail(result: dict) -> list[str]:
    """workflow 終了時に必ず出す github_persist 詳細ログ"""
    put = result.get("races_json_put") or {}
    status_code = put.get("status_code")
    if put.get("skipped"):
        put_label = "スキップ"
    elif put.get("ok"):
        put_label = "OK"
    else:
        put_label = "失敗"

    error_msg = put.get("error_message") or result.get("message") or "なし"
    if error_msg == "なし" and result.get("ok") and result.get("github"):
        error_msg = "なし"

    return [
        "--- github_persist 詳細 ---",
        f"DB内 race件数: {result.get('db_race_count', 0)}",
        f"export対象 race件数: {result.get('race_count', 0)}",
        f"GITHUB_REPO: {result.get('github_repo') or GITHUB_REPO or '(未設定)'}",
        f"GitHub PUT races.json 結果: {put_label}",
        f"HTTPステータス: {status_code if status_code is not None else 'N/A'}",
        f"エラーメッセージ: {error_msg}",
    ]


def _put_result_label(put: dict) -> str:
    if put.get("skipped"):
        return "スキップ"
    if put.get("ok"):
        return "OK"
    return "失敗"


def execute_workflow_persist_with_print(reason: str = "workflow") -> tuple[dict, list[str]]:
    """workflow ボタン直後: persist + GitHub sync。Render Logs 向けに print 出力"""
    ui_lines: list[str] = []

    def log_print(msg: str) -> None:
        print(f"[github_persist] {msg}", flush=True)
        logger.info(msg)
        ui_lines.append(msg)

    log_print("=== workflow persist start ===")
    log_print(f"永続化ブランチ: {GITHUB_PERSIST_BRANCH} (Render deploy は main のみ)")

    db_races = _db_race_count()
    log_print(f"DB race件数: {db_races}")

    log_print("persist_races() 呼び出し")
    races = persist_races()
    export_races = len(races)
    log_print(f"export race件数: {export_races}")

    log_print("persist_results() 呼び出し")
    results = persist_results()
    log_print(f"export result件数: {len(results)}")

    log_print(f"GITHUB_REPO: {GITHUB_REPO or '(未設定)'}")

    try:
        sync_result = sync_to_github(
            reason,
            log_fn=log_print,
            races=races,
            results=results,
        )
    except Exception as exc:
        log_print(f"GitHub sync exception: {exc}")
        sync_result = {
            "ok": False,
            "github": False,
            "message": str(exc),
            "db_path": str(DB_PATH),
            "db_race_count": db_races,
            "race_count": export_races,
            "github_repo": GITHUB_REPO,
            "push_log": [],
            "races_json_put": {
                "ok": False,
                "file": "races.json",
                "status_code": None,
                "error_message": str(exc),
                "skipped": False,
            },
        }

    put = sync_result.get("races_json_put") or {}
    status_code = put.get("status_code")
    put_label = _put_result_label(put)

    log_print(f"DB race件数: {sync_result.get('db_race_count', db_races)}")
    log_print(f"export race件数: {sync_result.get('race_count', export_races)}")
    log_print(f"GITHUB_REPO: {sync_result.get('github_repo') or GITHUB_REPO or '(未設定)'}")
    log_print(f"GitHub PUT結果: {put_label}")
    log_print(f"HTTPステータス: {status_code if status_code is not None else 'N/A'}")
    err = put.get("error_message") or sync_result.get("message") or ""
    if err:
        log_print(f"エラーメッセージ: {err}")

    log_print("=== workflow persist end ===")
    print(
        f"[persist] done ok={sync_result.get('ok')} "
        f"races={sync_result.get('race_count', export_races)} "
        f"github={sync_result.get('github')}",
        flush=True,
    )
    ui_lines.extend(format_workflow_persist_detail(sync_result))
    return sync_result, ui_lines


def workflow_persist_and_sync(reason: str = "workflow") -> tuple[dict, list[str]]:
    """後方互換 — workflow ボタンからは execute_workflow_persist_with_print を使用"""
    return execute_workflow_persist_with_print(reason)


def _load_best_snapshot() -> tuple[Optional[dict], str]:
    snapshot = None
    source = ""
    if is_github_enabled():
        try:
            snapshot = download_github_snapshot()
            source = "github"
        except Exception as exc:
            print(f"[persist] restore github download error: {exc}", flush=True)
            snapshot = None
    local = load_local_snapshot()
    if local:
        local_races = len(local.get("races.json") or [])
        gh_races = len((snapshot or {}).get("races.json") or [])
        if snapshot is None or local_races >= gh_races:
            snapshot = local
            source = "local" if source != "github" else "local+github"
    return snapshot, source


def ensure_data_restored() -> dict:
    """DB が空のとき persist/GitHub から復元（Render 再起動・再ログイン後）"""
    from db import get_connection, init_db, safe_table_count, table_exists

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    init_db()

    conn = get_connection()
    try:
        db_races = safe_table_count(conn, "races") if table_exists(conn, "races") else 0
        db_results = safe_table_count(conn, "results") if table_exists(conn, "results") else 0
        db_learning = (
            safe_table_count(conn, "learned_patterns")
            if table_exists(conn, "learned_patterns")
            else 0
        )
    finally:
        conn.close()

    if db_races > 0:
        return {
            "ok": True,
            "skipped": True,
            "reason": "db_not_empty",
            "race_count": db_races,
            "result_count": db_results,
            "learning_count": db_learning,
        }

    snapshot, source = _load_best_snapshot()
    if not snapshot:
        return {
            "ok": False,
            "skipped": True,
            "reason": "no_snapshot",
            "race_count": 0,
            "result_count": 0,
            "learning_count": 0,
        }

    races = snapshot.get("races.json") or []
    if not races:
        return {
            "ok": False,
            "skipped": True,
            "reason": "empty_races_json",
            "race_count": 0,
            "result_count": 0,
            "learning_count": 0,
        }

    try:
        stats = import_snapshot(snapshot)
        stats["source"] = source
        stats["ok"] = True
        stats["skipped"] = False
        print(
            f"[persist] restore ok: races={stats.get('race_count')} "
            f"results={stats.get('result_count')} "
            f"learning={stats.get('learning_count')} source={source}",
            flush=True,
        )
        return stats
    except Exception as exc:
        print(f"[persist] restore import error: {exc}", flush=True)
        return {
            "ok": False,
            "skipped": False,
            "error": str(exc),
            "source": source,
            "race_count": 0,
            "result_count": 0,
            "learning_count": 0,
        }


def restore_if_needed() -> Optional[dict]:
    return ensure_data_restored()


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
