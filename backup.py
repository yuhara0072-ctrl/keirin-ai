"""バックアップ・復元 — DB / 設定 / レポート"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DATA_DIR, DB_PATH, PROJECT_ROOT
from db import db_session, get_connection

BACKUP_ROOT = DATA_DIR / "backups"
BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

REPORT_GLOB = ("report_*.txt", "report_latest.txt")


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup_dir(backup_id: str) -> Path:
    return BACKUP_ROOT / backup_id


def _export_settings() -> dict:
    conn = get_connection()
    settings: dict = {"exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    try:
        rows = conn.execute("SELECT key, value FROM ops_config").fetchall()
        settings["ops_config"] = {r["key"]: r["value"] for r in rows}
    except sqlite3.OperationalError:
        settings["ops_config"] = {}

    conn.close()

    req = PROJECT_ROOT / "requirements.txt"
    if req.exists():
        settings["requirements_txt"] = req.read_text(encoding="utf-8")

    return settings


def _import_settings(settings: dict) -> None:
    ops = settings.get("ops_config") or {}
    if not ops:
        return
    with db_session() as conn:
        for key, value in ops.items():
            conn.execute(
                """
                INSERT INTO ops_config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )


def _backup_sqlite_db(dest: Path) -> bool:
    if not DB_PATH.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return True


def _collect_reports(dest_dir: Path) -> list[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for pattern in REPORT_GLOB:
        for src in DATA_DIR.glob(pattern):
            if not src.is_file():
                continue
            target = dest_dir / src.name
            shutil.copy2(src, target)
            copied.append(src.name)
    return sorted(set(copied))


def create_backup(*, note: str = "") -> dict:
    """ワンクリックバックアップ"""
    backup_id = _timestamp()
    root = _backup_dir(backup_id)
    root.mkdir(parents=True, exist_ok=True)

    db_ok = _backup_sqlite_db(root / "keirin.db")

    settings = _export_settings()
    settings_path = root / "settings.json"
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    reports_dir = root / "reports"
    report_files = _collect_reports(reports_dir)

    models_dir = root / "models"
    src_models = DATA_DIR / "models"
    model_files: list[str] = []
    if src_models.exists():
        models_dir.mkdir(parents=True, exist_ok=True)
        for f in src_models.glob("*.json"):
            shutil.copy2(f, models_dir / f.name)
            model_files.append(f.name)

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    manifest = {
        "backup_id": backup_id,
        "created_at": created_at,
        "note": note,
        "db_backed_up": db_ok,
        "db_size_bytes": (root / "keirin.db").stat().st_size if db_ok else 0,
        "report_files": report_files,
        "report_count": len(report_files),
        "model_files": model_files,
        "settings_file": "settings.json",
        "paths": {
            "db": "keirin.db",
            "settings": "settings.json",
            "reports": "reports",
            "models": "models",
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "backup_id": backup_id,
        "created_at": created_at,
        "path": str(root),
        "manifest": manifest,
    }


def _load_manifest(backup_dir: Path) -> Optional[dict]:
    path = backup_dir / "manifest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_backups(limit: int = 50) -> list[dict]:
    """バックアップ一覧（新しい順）"""
    items: list[dict] = []
    if not BACKUP_ROOT.exists():
        return items

    for d in sorted(BACKUP_ROOT.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        manifest = _load_manifest(d)
        if manifest:
            items.append(
                {
                    "backup_id": manifest.get("backup_id", d.name),
                    "created_at": manifest.get("created_at", "—"),
                    "path": str(d),
                    "db_backed_up": manifest.get("db_backed_up", False),
                    "db_size_bytes": manifest.get("db_size_bytes", 0),
                    "report_count": manifest.get("report_count", 0),
                    "model_count": len(manifest.get("model_files") or []),
                    "note": manifest.get("note", ""),
                    "manifest": manifest,
                }
            )
        else:
            items.append(
                {
                    "backup_id": d.name,
                    "created_at": "—",
                    "path": str(d),
                    "db_backed_up": (d / "keirin.db").exists(),
                    "db_size_bytes": (d / "keirin.db").stat().st_size
                    if (d / "keirin.db").exists()
                    else 0,
                    "report_count": len(list((d / "reports").glob("*.txt")))
                    if (d / "reports").exists()
                    else 0,
                    "model_count": 0,
                    "note": "",
                    "manifest": {},
                }
            )
        if len(items) >= limit:
            break
    return items


def get_latest_backup() -> Optional[dict]:
    backups = list_backups(limit=1)
    return backups[0] if backups else None


def restore_backup(backup_id: str, *, restore_db: bool = True, restore_settings: bool = True, restore_reports: bool = True, restore_models: bool = True) -> dict:
    """指定バックアップから復元"""
    root = _backup_dir(backup_id)
    if not root.exists():
        return {"ok": False, "error": f"バックアップが見つかりません: {backup_id}"}

    manifest = _load_manifest(root) or {}
    log: list[str] = []

    if restore_db:
        src_db = root / "keirin.db"
        if src_db.exists():
            if DB_PATH.exists():
                safety = DATA_DIR / f"keirin_before_restore_{_timestamp()}.db"
                shutil.copy2(DB_PATH, safety)
                log.append(f"現行DBを退避: {safety.name}")
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_db, DB_PATH)
            log.append("DBを復元しました")
        else:
            log.append("DBファイルなし（スキップ）")

    if restore_settings:
        settings_path = root / "settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            _import_settings(settings)
            log.append("設定（ops_config）を復元しました")
        else:
            log.append("設定ファイルなし（スキップ）")

    if restore_reports:
        reports_src = root / "reports"
        if reports_src.exists():
            n = 0
            for f in reports_src.glob("*.txt"):
                shutil.copy2(f, DATA_DIR / f.name)
                n += 1
            log.append(f"レポート {n} 件を復元しました")
        else:
            log.append("レポートなし（スキップ）")

    if restore_models:
        models_src = root / "models"
        if models_src.exists():
            dest = DATA_DIR / "models"
            dest.mkdir(parents=True, exist_ok=True)
            n = 0
            for f in models_src.glob("*.json"):
                shutil.copy2(f, dest / f.name)
                n += 1
            log.append(f"モデル {n} 件を復元しました")

    return {
        "ok": True,
        "backup_id": backup_id,
        "log": log,
        "manifest": manifest,
    }


def delete_backup(backup_id: str) -> dict:
    root = _backup_dir(backup_id)
    if not root.exists():
        return {"ok": False, "error": "バックアップが見つかりません"}
    shutil.rmtree(root)
    return {"ok": True, "backup_id": backup_id}


def get_backup_bundle() -> dict:
    """Streamlit 用"""
    backups = list_backups()
    latest = backups[0] if backups else None
    return {
        "has_backups": bool(backups),
        "backups": backups,
        "latest": latest,
        "latest_at": latest["created_at"] if latest else "—",
        "backup_root": str(BACKUP_ROOT),
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "db_size_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
    }


def format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def build_backup_lines() -> list[str]:
    bundle = get_backup_bundle()
    lines = ["【バックアップ】", ""]
    lines.append(f"  保存先: {bundle['backup_root']}")
    lines.append(f"  最新: {bundle['latest_at']}")
    lines.append(f"  件数: {len(bundle['backups'])}")
    lines.append("")
    if bundle["backups"]:
        lines.append("--- 履歴 ---")
        for b in bundle["backups"][:10]:
            lines.append(
                f"  {b['created_at']} {b['backup_id']} "
                f"DB={format_size(b['db_size_bytes'])} レポート={b['report_count']}"
            )
        lines.append("")
    return lines
