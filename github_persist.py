"""GitHub / リポジトリ JSON 永続化（Render 無料プラン向け）

SQLite は Render 上では再起動で消えるため、レースデータと学習データを
JSON として GitHub に保存し、起動時に SQLite（実行用キャッシュ）へ復元する。
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

from config import GITHUB_PERSIST_BRANCH, GITHUB_REPO, GITHUB_TOKEN, PERSIST_DIR, REQUEST_TIMEOUT

PERSIST_VERSION = 1
MAX_CHUNK_BYTES = 900_000
TABLES_CORE = ("races", "entries", "odds", "results")
TABLES_ALL = (*TABLES_CORE, "learned_patterns")
ODDS_CHUNK_PREFIX = "odds_"


def is_github_enabled() -> bool:
    return bool(GITHUB_TOKEN.strip() and GITHUB_REPO.strip())


def is_persist_enabled() -> bool:
    return is_github_enabled() or PERSIST_DIR.exists()


def _api_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN.strip()}",
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


def export_snapshot() -> dict[str, Any]:
    from db import get_connection, table_exists

    conn = get_connection()
    try:
        meta = {
            "version": PERSIST_VERSION,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "race_count": 0,
            "result_count": 0,
            "learning_count": 0,
        }
        files: dict[str, Any] = {"meta.json": meta}

        if table_exists(conn, "races"):
            races = _table_records(conn, "races")
            files["races.json"] = races
            meta["race_count"] = len(races)
        else:
            files["races.json"] = []

        if table_exists(conn, "entries"):
            files["entries.json"] = _table_records(conn, "entries")
        else:
            files["entries.json"] = []

        if table_exists(conn, "results"):
            results = _table_records(conn, "results")
            files["results.json"] = results
            meta["result_count"] = len(results)
        else:
            files["results.json"] = []

        if table_exists(conn, "odds"):
            files.update(_export_odds_chunks(conn))
        if table_exists(conn, "learned_patterns"):
            patterns = _table_records(conn, "learned_patterns")
            files["learned_patterns.json"] = patterns
            meta["learning_count"] = len(patterns)
        else:
            files["learned_patterns.json"] = []

        files["meta.json"] = meta
        return files
    finally:
        conn.close()


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
    patterns = files.get("learned_patterns.json") or []
    if not races and not patterns:
        return None
    return files


def _github_get_file(name: str) -> tuple[Optional[str], Optional[str]]:
    url = f"https://api.github.com/repos/{GITHUB_REPO.strip()}/contents/{PERSIST_DIR.name}/{name}"
    resp = requests.get(
        url,
        headers=_api_headers(),
        params={"ref": GITHUB_PERSIST_BRANCH},
        timeout=REQUEST_TIMEOUT,
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
    resp = requests.put(url, headers=_api_headers(), json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()


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
        timeout=REQUEST_TIMEOUT,
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


def sync_to_github(reason: str = "update") -> dict:
    files = export_snapshot()
    save_local_snapshot(files)
    result = {
        "ok": True,
        "local": True,
        "github": False,
        "race_count": files["meta.json"]["race_count"],
        "learning_count": files["meta.json"]["learning_count"],
        "message": "",
    }
    if not is_github_enabled():
        result["message"] = "local only (GITHUB_TOKEN / GITHUB_REPO 未設定)"
        return result

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_msg = f"chore: persist keirin data ({reason}) at {stamp}"
    try:
        for name, content in files.items():
            text = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            _github_put_file(name, text, commit_msg)
        result["github"] = True
        result["message"] = f"github synced ({result['race_count']} races)"
    except Exception as exc:
        result["ok"] = False
        result["message"] = str(exc)
    return result


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
    patterns = snapshot.get("learned_patterns.json") or []
    if not races and not patterns:
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
        return {"ok": False, "message": str(exc)}
