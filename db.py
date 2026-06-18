"""競輪観測AI — SQLite"""

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS races (
    race_id     TEXT PRIMARY KEY,
    race_date   TEXT NOT NULL,
    venue_code  TEXT NOT NULL,
    venue_name  TEXT,
    race_no     INTEGER NOT NULL,
    grade       TEXT,
    distance    INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id     TEXT NOT NULL REFERENCES races(race_id),
    bracket     INTEGER NOT NULL,
    racer_id    TEXT,
    racer_name  TEXT NOT NULL,
    region      TEXT,
    racer_grade TEXT,
    style       TEXT,
    UNIQUE (race_id, bracket)
);

CREATE TABLE IF NOT EXISTS odds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id     TEXT NOT NULL REFERENCES races(race_id),
    bet_type    TEXT NOT NULL,
    combination TEXT NOT NULL,
    odds        REAL NOT NULL,
    captured_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (race_id, bet_type, combination, captured_at)
);

CREATE TABLE IF NOT EXISTS results (
    race_id      TEXT PRIMARY KEY REFERENCES races(race_id),
    finish_order TEXT NOT NULL,
    trifecta_pay INTEGER,
    exacta_pay   INTEGER,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_entries_race ON entries(race_id);
CREATE INDEX IF NOT EXISTS idx_odds_race ON odds(race_id);
CREATE INDEX IF NOT EXISTS idx_odds_captured ON odds(captured_at);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def safe_table_count(conn: sqlite3.Connection, table: str) -> int:
    allowed = {"races", "results", "odds", "entries", "learned_patterns"}
    if table not in allowed or not table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def get_db_counts_fast() -> dict:
    """件数のみ（bootstrap / GitHub 復元なし — UI 起動用）"""
    try:
        conn = get_connection()
        try:
            if not table_exists(conn, "races"):
                return {
                    "races": 0,
                    "results": 0,
                    "odds": 0,
                    "learning": 0,
                    "ready": False,
                }
            return {
                "races": safe_table_count(conn, "races"),
                "results": safe_table_count(conn, "results"),
                "odds": safe_table_count(conn, "odds"),
                "learning": safe_table_count(conn, "learned_patterns"),
                "ready": True,
            }
        finally:
            conn.close()
    except Exception:
        return {"races": 0, "results": 0, "odds": 0, "learning": 0, "ready": False}


def get_db_status() -> dict:
    """テーブル未作成時も落ちない件数サマリー（workflow 等 — bootstrap あり）"""
    try:
        ensure_db()
        return get_db_counts_fast()
    except Exception:
        return {"races": 0, "results": 0, "odds": 0, "learning": 0, "ready": False}


def bootstrap_database() -> dict:
    """DB 作成 + GitHub/ローカル復元。例外は握りつぶし結果 dict で返す"""
    outcome: dict = {
        "ok": True,
        "restore": None,
        "race_count": 0,
        "result_count": 0,
        "learning_count": 0,
        "error": None,
    }
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        init_db()
    except Exception as exc:
        outcome["ok"] = False
        outcome["error"] = f"init_db: {exc}"
        return outcome

    try:
        from github_persist import ensure_data_restored

        restore_out = ensure_data_restored()
        outcome["restore"] = restore_out
        if restore_out.get("error"):
            outcome["ok"] = False
            outcome["error"] = f"restore: {restore_out['error']}"
    except Exception as exc:
        outcome["ok"] = False
        outcome["error"] = f"restore: {exc}"

    try:
        conn = get_connection()
        try:
            outcome["race_count"] = safe_table_count(conn, "races")
            outcome["result_count"] = safe_table_count(conn, "results")
            outcome["learning_count"] = safe_table_count(conn, "learned_patterns")
        finally:
            conn.close()
    except Exception as exc:
        if outcome["error"] is None:
            outcome["error"] = f"count: {exc}"

    return outcome


def ensure_db() -> None:
    """アプリ起動時に DB と全テーブルを idempotent に作成"""
    bootstrap_database()


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate_db(conn: sqlite3.Connection) -> None:
    """既存DBへカラム追加"""
    if not table_exists(conn, "races"):
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(races)")}
    if "race_start" not in cols:
        conn.execute("ALTER TABLE races ADD COLUMN race_start TEXT")
    if "time_slot" not in cols:
        conn.execute("ALTER TABLE races ADD COLUMN time_slot TEXT")
    if "line_info" not in cols:
        conn.execute("ALTER TABLE races ADD COLUMN line_info TEXT")
    if "line_count" not in cols:
        conn.execute("ALTER TABLE races ADD COLUMN line_count INTEGER")


def init_db() -> None:
    with db_session() as conn:
        conn.executescript(SCHEMA)
        migrate_db(conn)
        from learning import migrate_learning_table

        migrate_learning_table(conn)
        from pre_race import migrate_pre_race_table

        migrate_pre_race_table(conn)
        from notifications import migrate_notifications_table

        migrate_notifications_table(conn)
        from bet_tracker import migrate_bet_table

        migrate_bet_table(conn)
        from bulk_collect import migrate_collect_table

        migrate_collect_table(conn)
        from advanced_learning import migrate_advanced_learning_table

        migrate_advanced_learning_table(conn)
        from bankroll import migrate_bankroll_table

        migrate_bankroll_table(conn)
        from validation_report import migrate_validation_table

        migrate_validation_table(conn)
        from improvement_ai import migrate_improvement_table

        migrate_improvement_table(conn)
        from ops import migrate_ops_table

        migrate_ops_table(conn)


if __name__ == "__main__":
    init_db()
    print(f"DB ready: {DB_PATH}")
