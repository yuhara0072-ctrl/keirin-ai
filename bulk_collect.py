"""100レース収集モード — 日付範囲で複数日・複数開催を一括取得"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from config import DATA_DIR, TARGET_RACES
from db import db_session, get_connection
from fetch_daily import (
    fetch_one_race,
    list_races_for_date,
    normalize_date,
    select_races,
)
from learning import save_learned_patterns
from report import save_report

COLLECT_LOG_DIR = DATA_DIR / "collect" / "logs"
COLLECT_LOG_DIR.mkdir(parents=True, exist_ok=True)

COLLECT_TABLE = """
CREATE TABLE IF NOT EXISTS collect_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    per_day_limit   INTEGER NOT NULL,
    target_races    INTEGER NOT NULL DEFAULT 100,
    fetched_new     INTEGER NOT NULL DEFAULT 0,
    skipped_dup     INTEGER NOT NULL DEFAULT 0,
    error_count     INTEGER NOT NULL DEFAULT 0,
    days_processed  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running',
    log_path        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS collect_errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER,
    kaisai_date     TEXT NOT NULL,
    race_id         TEXT,
    venue_name      TEXT,
    race_no         INTEGER,
    error_message   TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_collect_errors_run ON collect_errors(run_id);
"""


def migrate_collect_table(conn) -> None:
    conn.executescript(COLLECT_TABLE)


def count_saved_races(*, with_result: bool = True) -> int:
    conn = get_connection()
    if with_result:
        n = conn.execute(
            """
            SELECT COUNT(*) FROM races r
            INNER JOIN results res ON r.race_id = res.race_id
            """
        ).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM races").fetchone()[0]
    conn.close()
    return int(n)


def is_race_collected(race_id: str, *, require_result: bool = True) -> bool:
    conn = get_connection()
    if require_result:
        row = conn.execute(
            """
            SELECT 1 FROM races r
            INNER JOIN results res ON r.race_id = res.race_id
            WHERE r.race_id = ?
            """,
            (race_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM races WHERE race_id = ?", (race_id,)
        ).fetchone()
    conn.close()
    return row is not None


def iter_dates(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(normalize_date(start_date), "%Y%m%d").date()
    end = datetime.strptime(normalize_date(end_date), "%Y%m%d").date()
    if start > end:
        start, end = end, start
    days: list[str] = []
    cur = start
    while cur <= end:
        days.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return days


def run_post_collect(bet_type: str = "3連単") -> dict:
    """取得後: 学習・レポート更新"""
    learning_count = save_learned_patterns(bet_type)
    report_path = save_report(bet_type=bet_type)
    return {
        "learning_count": learning_count,
        "report_path": str(report_path),
    }


def fetch_bulk(
    start_date: str,
    end_date: str,
    *,
    per_day_limit: int = 5,
    with_result: bool = True,
    venue_code: Optional[str] = None,
    target_races: int = TARGET_RACES,
    skip_existing: bool = True,
    run_post: bool = True,
    bet_type: str = "3連単",
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    """日付範囲でレースを一括取得"""
    from db import init_db

    init_db()
    started = datetime.now()
    started_at = started.strftime("%Y-%m-%d %H:%M:%S")
    dates = iter_dates(start_date, end_date)
    log: list[str] = [
        "=" * 50,
        "100レース収集モード",
        f"開始: {started_at}",
        f"期間: {normalize_date(start_date)} 〜 {normalize_date(end_date)}",
        f"1日あたり: {per_day_limit} 件 / 目標: {target_races} レース",
        "=" * 50,
        "",
    ]

    fetched_new = 0
    skipped_dup = 0
    error_count = 0
    errors: list[dict] = []
    days_processed = 0
    run_id: Optional[int] = None

    with db_session() as conn:
        migrate_collect_table(conn)
        cur = conn.execute(
            """
            INSERT INTO collect_runs (
                started_at, start_date, end_date, per_day_limit, target_races, status
            ) VALUES (?, ?, ?, ?, ?, 'running')
            """,
            (
                started_at,
                normalize_date(start_date),
                normalize_date(end_date),
                per_day_limit,
                target_races,
            ),
        )
        run_id = cur.lastrowid

    total_days = len(dates)
    for day_idx, day in enumerate(dates, 1):
        days_processed = day_idx
        log.append(f"--- {day} ({day_idx}/{total_days}) ---")

        if count_saved_races(with_result=with_result) >= target_races:
            log.append(f"  目標 {target_races} レース到達のため終了")
            break

        try:
            all_races = list_races_for_date(day)
        except Exception as e:
            msg = str(e)
            log.append(f"  一覧取得エラー: {msg}")
            errors.append(
                {"kaisai_date": day, "race_id": "", "error_message": msg}
            )
            error_count += 1
            if progress_callback:
                progress_callback(_progress_payload(day_idx, total_days, fetched_new, skipped_dup, error_count, log[-1]))
            continue

        if not all_races:
            log.append("  開催なし")
            if progress_callback:
                progress_callback(_progress_payload(day_idx, total_days, fetched_new, skipped_dup, error_count, "開催なし"))
            continue

        targets = select_races(all_races, per_day_limit, venue_code)
        day_new = 0

        for info in targets:
            if count_saved_races(with_result=with_result) >= target_races:
                break

            race_id = info["race_id"]
            if skip_existing and is_race_collected(race_id, require_result=with_result):
                skipped_dup += 1
                log.append(f"  スキップ(重複): {info['venue_name']} {info['race_no']}R")
                continue

            label = f"{info['venue_name']} {info['race_no']}R"
            if progress_callback:
                progress_callback(
                    _progress_payload(
                        day_idx,
                        total_days,
                        fetched_new,
                        skipped_dup,
                        error_count,
                        f"取得中: {day} {label}",
                    )
                )

            row = fetch_one_race(info, with_result)
            if row.get("error"):
                error_count += 1
                err = {
                    "kaisai_date": day,
                    "race_id": race_id,
                    "venue_name": info.get("venue_name"),
                    "race_no": info.get("race_no"),
                    "error_message": row["error"],
                }
                errors.append(err)
                log.append(f"  エラー {label}: {row['error']}")
            else:
                fetched_new += 1
                day_new += 1
                log.append(f"  OK {label} 出走{row['entries']} / オッズ{row['odds']}")

        log.append(f"  日次: 新規{day_new} / スキップ累計{skipped_dup}")
        if progress_callback:
            progress_callback(
                _progress_payload(day_idx, total_days, fetched_new, skipped_dup, error_count, f"{day} 完了")
            )

    post_result: dict = {}
    if run_post and fetched_new > 0:
        log.append("")
        log.append("--- 取得後処理 ---")
        try:
            post_result = run_post_collect(bet_type)
            log.append(f"  学習: {post_result['learning_count']} 件")
            log.append(f"  レポート: {post_result['report_path']}")
        except Exception as e:
            log.append(f"  取得後処理エラー: {e}")
            error_count += 1

    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.append("")
    log.append(
        f"完了: 新規{fetched_new} / 重複スキップ{skipped_dup} / エラー{error_count}"
    )
    log.append(f"保存レース数: {count_saved_races(with_result=with_result)}")

    log_file = COLLECT_LOG_DIR / f"collect_{started.strftime('%Y%m%d_%H%M%S')}.txt"
    log_text = "\n".join(log)
    log_file.write_text(log_text, encoding="utf-8")

    with db_session() as conn:
        for err in errors:
            conn.execute(
                """
                INSERT INTO collect_errors (
                    run_id, kaisai_date, race_id, venue_name, race_no, error_message
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    err["kaisai_date"],
                    err.get("race_id") or "",
                    err.get("venue_name"),
                    err.get("race_no"),
                    err["error_message"],
                ),
            )
        conn.execute(
            """
            UPDATE collect_runs SET
                finished_at = ?,
                fetched_new = ?,
                skipped_dup = ?,
                error_count = ?,
                days_processed = ?,
                status = 'ok',
                log_path = ?
            WHERE id = ?
            """,
            (
                finished_at,
                fetched_new,
                skipped_dup,
                error_count,
                days_processed,
                str(log_file),
                run_id,
            ),
        )

    from github_persist import format_sync_result, maybe_sync

    sync_result = maybe_sync("bulk_collect")
    log.append(f"GitHub永続化: {format_sync_result(sync_result)}")

    return {
        "ok": True,
        "run_id": run_id,
        "fetched_new": fetched_new,
        "skipped_dup": skipped_dup,
        "error_count": error_count,
        "days_processed": days_processed,
        "saved_races": count_saved_races(with_result=with_result),
        "remaining_to_target": max(0, target_races - count_saved_races(with_result=with_result)),
        "log_text": log_text,
        "log_path": str(log_file),
        "post_result": post_result,
        "errors": errors,
    }


def _progress_payload(
    day_idx: int,
    total_days: int,
    fetched_new: int,
    skipped_dup: int,
    error_count: int,
    message: str,
) -> dict:
    saved = count_saved_races(with_result=True)
    return {
        "day_idx": day_idx,
        "total_days": total_days,
        "progress_pct": day_idx / total_days if total_days else 0,
        "fetched_new": fetched_new,
        "skipped_dup": skipped_dup,
        "error_count": error_count,
        "saved_races": saved,
        "remaining_to_target": max(0, TARGET_RACES - saved),
        "message": message,
    }


def load_collect_runs(limit: int = 20) -> pd.DataFrame:
    conn = get_connection()
    migrate_collect_table(conn)
    df = pd.read_sql(
        """
        SELECT id, started_at, finished_at, start_date, end_date,
               per_day_limit, target_races, fetched_new, skipped_dup,
               error_count, days_processed, status, log_path
        FROM collect_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        conn,
        params=(limit,),
    )
    conn.close()
    return df


def load_collect_errors(run_id: Optional[int] = None, limit: int = 50) -> pd.DataFrame:
    conn = get_connection()
    migrate_collect_table(conn)
    if run_id:
        df = pd.read_sql(
            """
            SELECT kaisai_date, race_id, venue_name, race_no, error_message, created_at
            FROM collect_errors WHERE run_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            conn,
            params=(run_id, limit),
        )
    else:
        df = pd.read_sql(
            """
            SELECT run_id, kaisai_date, race_id, venue_name, race_no, error_message, created_at
            FROM collect_errors
            ORDER BY id DESC LIMIT ?
            """,
            conn,
            params=(limit,),
        )
    conn.close()
    return df


def get_collect_bundle(target_races: int = TARGET_RACES) -> dict:
    saved = count_saved_races(with_result=True)
    saved_all = count_saved_races(with_result=False)
    return {
        "target_races": target_races,
        "saved_races": saved,
        "saved_races_all": saved_all,
        "remaining_to_target": max(0, target_races - saved),
        "progress_pct": min(100.0, saved / target_races * 100) if target_races else 0,
        "runs": load_collect_runs(),
        "errors": load_collect_errors(),
    }


def build_collect_lines() -> list[str]:
    b = get_collect_bundle()
    lines = ["【データ収集】", ""]
    lines.append(f"  保存(結果あり): {b['saved_races']} / 目標 {b['target_races']}")
    lines.append(f"  あと {b['remaining_to_target']} レース")
    lines.append(f"  全登録: {b['saved_races_all']} レース")
    lines.append("")
    return lines
