"""自動運用モード — データ収集・分析・レポート・学習・直前分析の一括実行"""

from __future__ import annotations

import threading
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from ai_recommend import build_daily_recommendations
from config import DAILY_FETCH_LIMIT, DATA_DIR
from db import db_session, get_connection
from fetch_daily import fetch_daily
from learning import load_learned_patterns, save_learned_patterns
from pre_race import poll_pre_race_due

OPS_LOG_DIR = DATA_DIR / "ops" / "logs"
OPS_LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SCHEDULE_HOUR = 6
DEFAULT_SCHEDULE_MINUTE = 0
SCHEDULER_CHECK_SEC = 60

OPS_TABLE = """
CREATE TABLE IF NOT EXISTS ops_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    trigger_type    TEXT NOT NULL DEFAULT 'manual',
    kaisai_date     TEXT,
    bet_type        TEXT NOT NULL DEFAULT '3連単',
    races_fetched   INTEGER NOT NULL DEFAULT 0,
    learning_count  INTEGER NOT NULL DEFAULT 0,
    targets_count   INTEGER NOT NULL DEFAULT 0,
    pre_race_count  INTEGER NOT NULL DEFAULT 0,
    report_path     TEXT,
    log_path        TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ops_runs_started ON ops_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS ops_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def migrate_ops_table(conn) -> None:
    conn.executescript(OPS_TABLE)
    defaults = {
        "auto_enabled": "1",
        "schedule_hour": str(DEFAULT_SCHEDULE_HOUR),
        "schedule_minute": str(DEFAULT_SCHEDULE_MINUTE),
        "last_scheduled_date": "",
    }
    for key, val in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO ops_config (key, value) VALUES (?, ?)",
            (key, val),
        )


def get_ops_config() -> dict[str, str]:
    conn = get_connection()
    migrate_ops_table(conn)
    rows = conn.execute("SELECT key, value FROM ops_config").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_ops_config(key: str, value: str) -> None:
    with db_session() as conn:
        migrate_ops_table(conn)
        conn.execute(
            """
            INSERT INTO ops_config (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def _today_str() -> str:
    return date.today().strftime("%Y%m%d")


def _log_path_for_run(started: datetime) -> Path:
    return OPS_LOG_DIR / f"ops_{started.strftime('%Y%m%d_%H%M%S')}.txt"


def _append_log(log: list[str], msg: str) -> None:
    log.append(msg)


def _build_ops_notes(
    rec: dict,
    notify: dict,
    *,
    fetch_errors: list[str],
    val_improvements: list[str] | None = None,
) -> list[str]:
    notes: list[str] = []
    if fetch_errors:
        notes.append(f"取得エラー {len(fetch_errors)} 件 — 実行ログを確認")
    if not rec.get("has_data"):
        notes.append("本日レースのスコアデータがありません — workflow または再実行")
    elif not rec.get("targets"):
        notes.append("本日の狙い目なし — 見送り中心の日です")
    danger = rec.get("dangerous_popular") or []
    if danger:
        notes.append(f"危険人気 {len(danger)} 件 — 購入前に必ず確認")
    surge = notify.get("odds_surge") or []
    if surge:
        notes.append(f"オッズ急変 {len(surge)} 件 — 市場監視タブを確認")
    high = notify.get("high_score") or []
    if high:
        notes.append(f"高スコア通知 {len(high)} 件")
    if val_improvements:
        notes.append(val_improvements[0])
    if not notes:
        notes.append("問題なし — 狙い目TOP3と実戦判定を確認")
    return notes


def build_today_results_summary(
    rec: dict,
    notify: dict,
    *,
    races_fetched: int = 0,
    learning_count: int = 0,
    fetch_errors: list[str] | None = None,
    val_improvements: list[str] | None = None,
) -> dict:
    fetch_errors = fetch_errors or []
    notes = _build_ops_notes(
        rec,
        notify,
        fetch_errors=fetch_errors,
        val_improvements=val_improvements,
    )
    danger = rec.get("dangerous_popular") or []
    return {
        "races_fetched": races_fetched,
        "learning_count": learning_count,
        "targets_count": len(rec.get("targets") or []),
        "danger_count": len(danger),
        "notify_count": int(notify.get("candidate_count") or 0),
        "targets_top3": (rec.get("targets") or [])[:3],
        "dangerous_popular": danger[:5],
        "notes": notes,
    }


def run_daily_auto(
    bet_type: str = "3連単",
    *,
    limit: int = DAILY_FETCH_LIMIT,
    with_result: bool = True,
    venue_code: Optional[str] = None,
    trigger: str = "manual",
) -> dict:
    """今日の日付でワンクリック自動運用"""
    return run_full_ops(
        kaisai_date=_today_str(),
        limit=limit,
        with_result=with_result,
        venue_code=venue_code,
        bet_type=bet_type,
        trigger=trigger,
    )


def run_full_ops(
    *,
    kaisai_date: Optional[str] = None,
    limit: int = DAILY_FETCH_LIMIT,
    with_result: bool = True,
    venue_code: Optional[str] = None,
    bet_type: str = "3連単",
    trigger: str = "manual",
    skip_fetch: bool = False,
) -> dict:
    """全処理: 取得 → 分析 → 学習 → レポート → AIおすすめ → 通知 → 検証"""
    from db import init_db

    init_db()
    started = datetime.now()
    started_at = started.strftime("%Y-%m-%d %H:%M:%S")
    kaisai = kaisai_date or _today_str()
    log: list[str] = [
        "=" * 50,
        "競輪観測AI 自動運用",
        f"開始: {started_at}",
        f"トリガー: {trigger}",
        f"開催日: {kaisai} / 券種: {bet_type}",
        "=" * 50,
        "",
    ]

    run_id: Optional[int] = None
    status = "ok"
    error_message = ""
    races_fetched = 0
    learning_count = 0
    targets_count = 0
    pre_race_count = 0
    notify_count = 0
    analyze_races = 0
    detect_count = 0
    report_path: Optional[Path] = None
    fetch_errors: list[str] = []
    rec: dict = {}
    notify: dict = {}
    val_improvements: list[str] = []
    today_results: dict = {}

    with db_session() as conn:
        migrate_ops_table(conn)
        cur = conn.execute(
            """
            INSERT INTO ops_runs (
                started_at, status, trigger_type, kaisai_date, bet_type
            ) VALUES (?, 'running', ?, ?, ?)
            """,
            (started_at, trigger, kaisai, bet_type),
        )
        run_id = cur.lastrowid

    try:
        if not skip_fetch:
            _append_log(log, "STEP 1/7: レース取得 (本日)...")
            results = fetch_daily(
                kaisai_date=kaisai,
                limit=limit,
                with_result=with_result,
                venue_code=venue_code,
            )
            ok = sum(1 for r in results if not r.get("error"))
            races_fetched = ok
            _append_log(log, f"  → {ok}/{len(results)} 件成功")
            for r in results:
                if r.get("error"):
                    msg = f"{r.get('race_id', '?')}: {r['error']}"
                    fetch_errors.append(msg)
                    _append_log(log, f"  ! {msg}")
        else:
            _append_log(log, "STEP 1/7: レース取得 — スキップ")

        _append_log(log, "")
        _append_log(log, "STEP 2/7: 分析実行...")
        from analyze import load_bet_frame
        from detect_anomaly import detect_all

        bet_df = load_bet_frame(bet_type=bet_type)
        analyze_races = len(bet_df)
        detect_df = detect_all(bet_type)
        detect_count = len(detect_df)
        _append_log(log, f"  → 分析 {analyze_races} レース / 異常 {detect_count} 件")

        _append_log(log, "")
        _append_log(log, "STEP 3/7: 学習データ更新...")
        learning_count = save_learned_patterns(bet_type)
        _append_log(log, f"  → 学習条件 {learning_count} 件")
        pre_results = poll_pre_race_due(within_hours=3.0)
        pre_race_count = sum(1 for r in pre_results if r.get("ok"))
        _append_log(log, f"  → 直前記録 {pre_race_count}/{len(pre_results)} 件")
        for r in pre_results:
            if not r.get("ok"):
                _append_log(
                    log,
                    f"  ! {r.get('race_id')} {r.get('phase')}: {r.get('error', 'NG')}",
                )

        _append_log(log, "")
        _append_log(log, "STEP 4/7: レポート自動保存...")
        from report import save_report

        report_path = save_report(bet_type=bet_type)
        _append_log(log, f"  → {report_path}")

        _append_log(log, "")
        _append_log(log, "STEP 5/7: AIおすすめ更新...")
        rec = build_daily_recommendations(bet_type)
        targets_count = len(rec.get("targets") or [])
        _append_log(log, f"  → 本日の狙い目 {targets_count} 件")
        if rec.get("has_data"):
            for t in (rec.get("targets") or [])[:3]:
                _append_log(
                    log,
                    f"    · {t['venue_name']} {t['race_no']}R "
                    f"スコア{t.get('pre_race_score', t['ai_total_score'])} ({t['verdict']})",
                )
        danger_n = len(rec.get("dangerous_popular") or [])
        _append_log(log, f"  → 危険人気 {danger_n} 件")

        _append_log(log, "")
        _append_log(log, "STEP 6/7: 通知候補作成...")
        from ai_score import build_race_scores
        from notifications import get_notification_bundle

        scores = build_race_scores(bet_type)
        notify = get_notification_bundle(
            bet_type,
            scores=scores,
            recommend=rec,
            persist=True,
        )
        notify_count = int(notify.get("candidate_count") or 0)
        saved_n = int(notify.get("saved_count") or 0)
        _append_log(log, f"  → 通知候補 {notify_count} 件（新規記録 {saved_n} 件）")

        _append_log(log, "")
        _append_log(log, "STEP 7/7: 検証レポート...")
        from validation_report import run_daily_validation

        val = run_daily_validation(bet_type)
        _append_log(log, f"  → {val.get('report_path')}")
        val_improvements = val.get("improvements") or []
        if val_improvements:
            _append_log(log, f"  改善: {val_improvements[0]}")

        from improvement_ai import save_improvement_report

        imp_path = save_improvement_report(bet_type)
        _append_log(log, f"  改善提案: {imp_path.name}")

        today_results = build_today_results_summary(
            rec,
            notify,
            races_fetched=races_fetched,
            learning_count=learning_count,
            fetch_errors=fetch_errors,
            val_improvements=val_improvements,
        )
        _append_log(log, "")
        _append_log(log, "=" * 50)
        _append_log(log, "今日見るべき結果")
        _append_log(log, f"  取得レース: {today_results['races_fetched']} 件")
        _append_log(log, f"  AIおすすめ: {today_results['targets_count']} 件")
        _append_log(log, f"  危険人気: {today_results['danger_count']} 件")
        _append_log(log, f"  通知候補: {today_results['notify_count']} 件")
        if today_results["targets_top3"]:
            _append_log(log, "  --- 本日の狙い目 TOP3 ---")
            for t in today_results["targets_top3"]:
                _append_log(
                    log,
                    f"    [{t.get('verdict', '')}] {t['venue_name']} {t['race_no']}R "
                    f"スコア{t.get('pre_race_score', t.get('ai_total_score', '-'))}",
                )
        if today_results["notes"]:
            _append_log(log, "  --- 注意点 ---")
            for note in today_results["notes"]:
                _append_log(log, f"    ! {note}")

        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _append_log(log, "")
        _append_log(log, f"完了: {finished_at}")

    except Exception as e:
        status = "error"
        error_message = str(e)
        _append_log(log, "")
        _append_log(log, f"エラー: {e}")
        _append_log(log, traceback.format_exc())
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log_file = _log_path_for_run(started)
    log_text = "\n".join(log)
    log_file.write_text(log_text, encoding="utf-8")

    with db_session() as conn:
        conn.execute(
            """
            UPDATE ops_runs SET
                finished_at = ?,
                status = ?,
                races_fetched = ?,
                learning_count = ?,
                targets_count = ?,
                pre_race_count = ?,
                report_path = ?,
                log_path = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                finished_at,
                status,
                races_fetched,
                learning_count,
                targets_count,
                pre_race_count,
                str(report_path) if report_path else None,
                str(log_file),
                error_message or None,
                run_id,
            ),
        )

    if trigger == "scheduled":
        set_ops_config("last_scheduled_date", date.today().isoformat())

    return {
        "ok": status == "ok",
        "status": status,
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "kaisai_date": kaisai,
        "races_fetched": races_fetched,
        "learning_count": learning_count,
        "targets_count": targets_count,
        "danger_count": today_results.get("danger_count", len(rec.get("dangerous_popular") or [])),
        "notify_count": notify_count,
        "analyze_races": analyze_races,
        "detect_count": detect_count,
        "pre_race_count": pre_race_count,
        "report_path": str(report_path) if report_path else "",
        "log_path": str(log_file),
        "log_text": log_text,
        "error_message": error_message,
        "fetch_errors": fetch_errors,
        "today_results": today_results,
        "recommend": rec,
        "notify": notify,
    }


def load_ops_runs(limit: int = 30) -> pd.DataFrame:
    conn = get_connection()
    migrate_ops_table(conn)
    df = pd.read_sql(
        """
        SELECT id, started_at, finished_at, status, trigger_type,
               kaisai_date, bet_type, races_fetched, learning_count,
               targets_count, pre_race_count, report_path, log_path, error_message
        FROM ops_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        conn,
        params=(limit,),
    )
    conn.close()
    return df


def get_ops_status(
    bet_type: str = "3連単",
    *,
    targets_count: Optional[int] = None,
    fast: bool = False,
) -> dict:
    """Streamlit 運用状況タブ用"""
    runs = load_ops_runs(limit=50)
    cfg = get_ops_config()
    last = runs.iloc[0].to_dict() if not runs.empty else {}

    learning_count = 0
    try:
        patterns = load_learned_patterns(bet_type)
        learning_count = len(patterns)
    except Exception:
        pass

    if targets_count is not None:
        targets_today = targets_count
    elif fast:
        targets_today = int(last.get("targets_count") or 0)
    else:
        targets_today = 0
        try:
            rec = build_daily_recommendations(bet_type)
            targets_today = len(rec.get("targets") or [])
        except Exception:
            pass

    errors = runs[runs["status"] == "error"].head(10) if not runs.empty else pd.DataFrame()

    hour = int(cfg.get("schedule_hour", DEFAULT_SCHEDULE_HOUR))
    minute = int(cfg.get("schedule_minute", DEFAULT_SCHEDULE_MINUTE))
    auto_enabled = cfg.get("auto_enabled", "1") == "1"

    return {
        "auto_enabled": auto_enabled,
        "schedule_hour": hour,
        "schedule_minute": minute,
        "schedule_label": f"毎日 {hour:02d}:{minute:02d}",
        "last_scheduled_date": cfg.get("last_scheduled_date", ""),
        "last_run": last,
        "last_started_at": last.get("started_at", "—"),
        "last_finished_at": last.get("finished_at", "—"),
        "last_status": last.get("status", "—"),
        "races_fetched": int(last.get("races_fetched") or 0),
        "learning_count": learning_count,
        "targets_count": targets_today,
        "last_targets_count": int(last.get("targets_count") or 0),
        "pre_race_count": int(last.get("pre_race_count") or 0),
        "runs": runs,
        "errors": errors,
        "latest_log_path": last.get("log_path", ""),
        "latest_log_text": (
            Path(last["log_path"]).read_text(encoding="utf-8")
            if last.get("log_path") and Path(last["log_path"]).exists()
            else ""
        ),
    }


def should_run_scheduled(now: Optional[datetime] = None) -> bool:
    """朝6時台に1日1回実行すべきか"""
    cfg = get_ops_config()
    if cfg.get("auto_enabled", "1") != "1":
        return False

    now = now or datetime.now()
    hour = int(cfg.get("schedule_hour", DEFAULT_SCHEDULE_HOUR))
    minute = int(cfg.get("schedule_minute", DEFAULT_SCHEDULE_MINUTE))
    last_date = cfg.get("last_scheduled_date", "")

    if last_date == now.date().isoformat():
        return False

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    window_end = target + timedelta(minutes=5)
    return target <= now < window_end


def check_and_run_scheduled(bet_type: str = "3連単") -> Optional[dict]:
    if not should_run_scheduled():
        return None
    return run_full_ops(bet_type=bet_type, trigger="scheduled")


_scheduler_lock = threading.Lock()
_scheduler_started = False


def _scheduler_loop(bet_type: str) -> None:
    while True:
        try:
            with _scheduler_lock:
                check_and_run_scheduled(bet_type=bet_type)
        except Exception:
            pass
        time.sleep(SCHEDULER_CHECK_SEC)


def start_scheduler_thread(bet_type: str = "3連単") -> None:
    """バックグラウンドで朝6時自動実行（Streamlit / daemon 用）"""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    t = threading.Thread(
        target=_scheduler_loop,
        args=(bet_type,),
        daemon=True,
        name="keirin-ops-scheduler",
    )
    t.start()


def build_ops_lines(bet_type: str = "3連単") -> list[str]:
    s = get_ops_status(bet_type, fast=True)
    lines = ["【運用状況】", ""]
    lines.append(f"  自動運用: {'ON' if s['auto_enabled'] else 'OFF'} ({s['schedule_label']})")
    lines.append(f"  最終実行: {s['last_started_at']} -> {s['last_finished_at']}")
    lines.append(f"  状態: {s['last_status']}")
    lines.append(f"  取得レース: {s['races_fetched']}")
    lines.append(f"  学習件数: {s['learning_count']}")
    lines.append(f"  本日狙い目: {s['targets_count']}")
    lines.append("  フロー: 取得 -> 分析 -> 学習 -> レポート -> AIおすすめ -> 通知")
    lines.append("")
    if not s["errors"].empty:
        lines.append("--- 直近エラー ---")
        for _, row in s["errors"].head(5).iterrows():
            lines.append(f"  {row['started_at']}: {row.get('error_message', 'error')}")
        lines.append("")
    return lines
