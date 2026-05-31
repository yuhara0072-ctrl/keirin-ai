"""完成チェック — アプリ全体の一括ヘルスチェック"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from config import DATA_DIR, DB_PATH, TARGET_RACES
from db import db_session, get_connection, init_db

CHECK_DIR = DATA_DIR / "system_check"
CHECK_DIR.mkdir(parents=True, exist_ok=True)

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_ERROR = "error"

STATUS_LABEL = {
    STATUS_OK: "正常",
    STATUS_WARN: "注意",
    STATUS_ERROR: "エラー",
}

CORE_TABLES = (
    "races",
    "entries",
    "odds",
    "results",
    "bet_records",
    "ops_runs",
)


def _item(
    name: str,
    category: str,
    status: str,
    message: str,
    *,
    detail: str = "",
    fix: str = "",
) -> dict:
    return {
        "項目": name,
        "category": category,
        "status": status,
        "状態": STATUS_LABEL.get(status, status),
        "メッセージ": message,
        "詳細": detail,
        "修正候補": fix,
    }


def _worst_status(items: list[dict]) -> str:
    if any(i["status"] == STATUS_ERROR for i in items):
        return STATUS_ERROR
    if any(i["status"] == STATUS_WARN for i in items):
        return STATUS_WARN
    return STATUS_OK


def check_db_connection() -> dict:
    try:
        if not DB_PATH.exists():
            return _item(
                "DB接続",
                "db",
                STATUS_ERROR,
                "DBファイルが存在しません",
                fix="python main.py init を実行",
            )
        init_db()
        with db_session() as conn:
            conn.execute("SELECT 1").fetchone()
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        missing = [t for t in CORE_TABLES if t not in tables]
        if missing:
            return _item(
                "DB接続",
                "db",
                STATUS_WARN,
                f"接続OK / 不足テーブル {len(missing)} 件",
                detail=", ".join(missing),
                fix="python main.py init を再実行",
            )
        size_kb = round(DB_PATH.stat().st_size / 1024, 1)
        return _item(
            "DB接続",
            "db",
            STATUS_OK,
            f"接続正常 / {size_kb} KB",
            detail=str(DB_PATH),
        )
    except Exception as e:
        return _item(
            "DB接続",
            "db",
            STATUS_ERROR,
            "接続に失敗",
            detail=str(e),
            fix="DBファイルの権限・破損を確認し python main.py init",
        )


def check_data_counts(bet_type: str = "3連単", *, quality: Optional[dict] = None) -> dict:
    try:
        conn = get_connection()
        races = conn.execute("SELECT COUNT(*) FROM races").fetchone()[0]
        results = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        odds = conn.execute("SELECT COUNT(*) FROM odds").fetchone()[0]
        entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        conn.close()

        if quality is None:
            from data_quality import get_quality_bundle

            quality = get_quality_bundle(bet_type, refresh=False)

        valid = quality.get("valid_races", 0)
        detail = f"レース{races} / 結果{results} / オッズ{odds} / 出走{entries} / 有効{valid}"

        if races == 0:
            return _item(
                "データ件数",
                "data",
                STATUS_ERROR,
                "レースデータがありません",
                detail=detail,
                fix="サイドバー workflow または python main.py daily を実行",
            )
        if valid < 10:
            return _item(
                "データ件数",
                "data",
                STATUS_WARN,
                f"有効データ {valid} 件 — 学習・検証には不足",
                detail=detail,
                fix=f"python main.py collect で100レース目標（残り{max(0, TARGET_RACES - valid)}）",
            )
        if valid < TARGET_RACES:
            return _item(
                "データ件数",
                "data",
                STATUS_WARN,
                f"有効 {valid}/{TARGET_RACES} 件 — 本格運用前に追加収集推奨",
                detail=detail,
                fix="学習状況タブ → 100レース収集",
            )
        return _item(
            "データ件数",
            "data",
            STATUS_OK,
            f"有効 {valid} 件 / 総レース {races} 件",
            detail=detail,
        )
    except Exception as e:
        return _item(
            "データ件数",
            "data",
            STATUS_ERROR,
            "件数取得に失敗",
            detail=str(e),
        )


def check_fetch(*, deep: bool = False) -> dict:
    try:
        conn = get_connection()
        latest = conn.execute(
            "SELECT MAX(race_date) FROM races"
        ).fetchone()[0]
        recent = conn.execute(
            "SELECT COUNT(*) FROM races WHERE race_date >= date('now', '-7 day')"
        ).fetchone()[0]
        conn.close()

        today = date.today().strftime("%Y%m%d")
        detail = f"最新日付 {latest or '—'} / 直近7日 {recent} 件"

        if not latest:
            status = STATUS_ERROR
            message = "取得履歴なし"
            fix = "python main.py daily --date YYYYMMDD を実行"
        elif latest < today and recent == 0:
            status = STATUS_WARN
            message = "直近7日のデータなし"
            fix = "本日分を workflow または python main.py ops --run-now で取得"
        else:
            status = STATUS_OK
            message = f"DBに取得済み / 最新 {latest}"
            fix = ""

        if deep:
            try:
                from fetch_daily import list_races_for_date

                api_races = list_races_for_date(today)
                detail += f" / API本日 {len(api_races)} レース"
                if api_races and latest and latest < today:
                    status = STATUS_WARN
                    message = "APIは応答OK / 本日分がDB未取込"
                    fix = "workflow 実行で本日データを取り込む"
                elif api_races:
                    if status == STATUS_OK:
                        message = f"取得機能OK / API本日 {len(api_races)} レース"
            except Exception as api_err:
                if status == STATUS_OK:
                    status = STATUS_WARN
                    message = "DB取得OK / API疎通未確認"
                detail += f" / API: {api_err}"
                fix = fix or "ネットワーク・keirin.jp 接続を確認"

        return _item(
            "取得機能",
            "fetch",
            status,
            message,
            detail=detail,
            fix=fix,
        )
    except Exception as e:
        return _item(
            "取得機能",
            "fetch",
            STATUS_ERROR,
            "チェック失敗",
            detail=str(e),
        )


def check_analysis(bet_type: str = "3連単") -> dict:
    try:
        from analyze import analyze_by_venue, load_bet_frame

        df = load_bet_frame(bet_type=bet_type)
        if df.empty:
            return _item(
                "分析機能",
                "analyze",
                STATUS_WARN,
                "分析対象データなし",
                fix="結果付きレースを workflow で取得",
            )
        venue_df = analyze_by_venue(bet_type)
        n = len(df)
        v = len(venue_df)
        return _item(
            "分析機能",
            "analyze",
            STATUS_OK,
            f"分析OK / {n} レース / 競輪場 {v} 区分",
            detail=f"券種={bet_type}",
        )
    except Exception as e:
        return _item(
            "分析機能",
            "analyze",
            STATUS_ERROR,
            "分析処理でエラー",
            detail=str(e),
            fix="python main.py analyze で詳細確認",
        )


def check_ai_score(
    bet_type: str = "3連単",
    *,
    score_bundle: Optional[dict] = None,
) -> dict:
    try:
        if score_bundle is None:
            from ai_score import get_ai_score_bundle

            score_bundle = get_ai_score_bundle(bet_type)
        scores = score_bundle.get("scores", pd.DataFrame())
        if scores.empty:
            return _item(
                "AIスコア",
                "ai_score",
                STATUS_WARN,
                "スコア対象レースなし",
                fix="workflow 実行後 python main.py score",
            )
        n = len(scores)
        high = int((scores["ai_total_score"].fillna(0) >= 80).sum()) if "ai_total_score" in scores.columns else 0
        return _item(
            "AIスコア",
            "ai_score",
            STATUS_OK,
            f"スコア生成OK / {n} レース（80+={high}）",
            detail=f"券種={bet_type}",
        )
    except Exception as e:
        return _item(
            "AIスコア",
            "ai_score",
            STATUS_ERROR,
            "スコア生成失敗",
            detail=str(e),
            fix="python main.py score でエラー内容を確認",
        )


def check_learning(
    bet_type: str = "3連単",
    *,
    learning_bundle: Optional[dict] = None,
) -> dict:
    try:
        if learning_bundle is None:
            from learning import get_learning_bundle

            learning_bundle = get_learning_bundle(bet_type, refresh=False)
        count = learning_bundle.get("learning_count", 0)
        races = learning_bundle.get("result_races", 0)
        if count == 0:
            return _item(
                "学習機能",
                "learning",
                STATUS_WARN,
                "学習パターン未生成",
                detail=f"結果付き {races} レース",
                fix="python main.py learn を実行",
            )
        return _item(
            "学習機能",
            "learning",
            STATUS_OK,
            f"パターン {count} 件 / 結果 {races} レース",
        )
    except Exception as e:
        return _item(
            "学習機能",
            "learning",
            STATUS_ERROR,
            "学習チェック失敗",
            detail=str(e),
            fix="python main.py learn",
        )


def check_report(bet_type: str = "3連単") -> dict:
    try:
        from report import build_analyze_lines, build_detect_lines

        analyze_lines = build_analyze_lines(bet_type)
        detect_lines = build_detect_lines(bet_type)
        latest = DATA_DIR / "report_latest.txt"
        validation_latest = DATA_DIR / "validation" / "validation_latest.txt"

        issues: list[str] = []
        if "分析対象がありません" in "\n".join(analyze_lines):
            issues.append("分析レポート空")
        if not latest.exists():
            issues.append("report_latest.txt 未生成")
        if not validation_latest.exists():
            issues.append("検証レポート未生成")

        detail_parts = []
        if latest.exists():
            mtime = datetime.fromtimestamp(latest.stat().st_mtime)
            detail_parts.append(f"report {mtime.strftime('%Y-%m-%d %H:%M')}")
        if validation_latest.exists():
            vm = datetime.fromtimestamp(validation_latest.stat().st_mtime)
            detail_parts.append(f"validation {vm.strftime('%Y-%m-%d %H:%M')}")
        detail = " / ".join(detail_parts) or "レポートファイルなし"

        if issues:
            return _item(
                "レポート生成",
                "report",
                STATUS_WARN,
                " · ".join(issues),
                detail=detail,
                fix="python main.py workflow または python main.py validate --save",
            )
        return _item(
            "レポート生成",
            "report",
            STATUS_OK,
            "分析・検知・保存ファイルOK",
            detail=detail + f" / 検知行 {len(detect_lines)}",
        )
    except Exception as e:
        return _item(
            "レポート生成",
            "report",
            STATUS_ERROR,
            "レポート生成失敗",
            detail=str(e),
            fix="python main.py report",
        )


def check_backup(*, backup_bundle: Optional[dict] = None) -> dict:
    try:
        if backup_bundle is None:
            from backup import get_backup_bundle

            backup_bundle = get_backup_bundle()
        backups = backup_bundle.get("backups") or []
        db_size = backup_bundle.get("db_size_bytes", 0)
        if not backups:
            return _item(
                "バックアップ",
                "backup",
                STATUS_WARN,
                "バックアップ未作成",
                detail=f"DB {round(db_size / 1024, 1)} KB",
                fix="バックアップタブまたは python main.py backup を実行",
            )
        latest_at = backups[0].get("created_at", "")
        try:
            latest_dt = datetime.strptime(latest_at[:19], "%Y-%m-%d %H:%M:%S")
            age_days = (datetime.now() - latest_dt).days
        except ValueError:
            age_days = 999
        if age_days > 7:
            return _item(
                "バックアップ",
                "backup",
                STATUS_WARN,
                f"最新が {age_days} 日前",
                detail=f"履歴 {len(backups)} 件",
                fix="python main.py backup で定期バックアップ",
            )
        return _item(
            "バックアップ",
            "backup",
            STATUS_OK,
            f"最新 {latest_at[:16]} / 履歴 {len(backups)} 件",
        )
    except Exception as e:
        return _item(
            "バックアップ",
            "backup",
            STATUS_ERROR,
            "バックアップ確認失敗",
            detail=str(e),
        )


def collect_ops_errors(limit: int = 10) -> pd.DataFrame:
    try:
        from ops import load_ops_runs

        runs = load_ops_runs(limit=50)
        if runs.empty:
            return pd.DataFrame()
        err = runs[runs["status"] == "error"].head(limit)
        if err.empty:
            return pd.DataFrame()
        cols = [c for c in ("started_at", "trigger_type", "error_message", "log_path") if c in err.columns]
        return err[cols].copy()
    except Exception:
        return pd.DataFrame()


def _build_missing_data(
    quality: Optional[dict],
    data_check: dict,
) -> list[str]:
    missing: list[str] = []
    if data_check["status"] == STATUS_ERROR:
        missing.append("レース・オッズ・結果データ")
    if quality:
        if quality.get("no_odds_count", 0) > 0:
            missing.append(f"オッズ欠損 {quality['no_odds_count']} レース")
        if quality.get("no_result_count", 0) > 0:
            missing.append(f"結果欠損 {quality['no_result_count']} レース")
        if quality.get("missing_count", 0) > 0:
            missing.append(f"出走表欠損 {quality['missing_count']} レース")
        if quality.get("duplicate_count", 0) > 0:
            missing.append(f"重複 {quality['duplicate_count']} 件")
    return missing


def _build_next_tasks(checks: list[dict], missing: list[str]) -> list[str]:
    tasks: list[str] = []
    for c in checks:
        if c["status"] in (STATUS_ERROR, STATUS_WARN) and c.get("修正候補"):
            tasks.append(f"{c['項目']}: {c['修正候補']}")
    if missing and not tasks:
        tasks.append("データ品質タブで欠損レースを確認・再取得")
    if not tasks:
        tasks.append("現状問題なし — 実戦判定・資金管理の通常運用を継続")
    return tasks[:8]


def _build_fix_suggestions(
    checks: list[dict],
    quality: Optional[dict],
) -> list[str]:
    fixes: list[str] = []
    for c in checks:
        fix = c.get("修正候補", "")
        if fix and c["status"] != STATUS_OK:
            fixes.append(f"[{c['項目']}] {fix}")
    if quality is not None and not quality.get("fix_candidates", pd.DataFrame()).empty:
        fc = quality["fix_candidates"].head(5)
        for _, row in fc.iterrows():
            label = row.get("修正候補") or row.get("問題", "")
            if label:
                fixes.append(f"[品質] {label}")
    seen: set[str] = set()
    unique: list[str] = []
    for f in fixes:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique[:10]


def run_system_checks(
    bet_type: str = "3連単",
    *,
    deep: bool = False,
    quality: Optional[dict] = None,
    score_bundle: Optional[dict] = None,
    learning_bundle: Optional[dict] = None,
    backup_bundle: Optional[dict] = None,
) -> dict:
    if quality is None:
        from data_quality import get_quality_bundle

        quality = get_quality_bundle(bet_type, refresh=False)

    checks = [
        check_db_connection(),
        check_data_counts(bet_type, quality=quality),
        check_fetch(deep=deep),
        check_analysis(bet_type),
        check_ai_score(bet_type, score_bundle=score_bundle),
        check_learning(bet_type, learning_bundle=learning_bundle),
        check_report(bet_type),
        check_backup(backup_bundle=backup_bundle),
    ]

    check_errors = [
        {
            "種別": "チェック",
            "日時": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "内容": f"{c['項目']}: {c['メッセージ']}",
            "詳細": c.get("詳細", ""),
        }
        for c in checks
        if c["status"] == STATUS_ERROR
    ]

    ops_errors = collect_ops_errors()
    ops_rows: list[dict] = []
    if not ops_errors.empty:
        for _, row in ops_errors.iterrows():
            ops_rows.append(
                {
                    "種別": "自動運用",
                    "日時": str(row.get("started_at", ""))[:19],
                    "内容": str(row.get("error_message", ""))[:200],
                    "詳細": str(row.get("log_path", "")),
                }
            )

    error_list = check_errors + ops_rows
    missing = _build_missing_data(quality, checks[1])
    next_tasks = _build_next_tasks(checks, missing)
    fix_suggestions = _build_fix_suggestions(checks, quality)

    summary = {
        STATUS_OK: sum(1 for c in checks if c["status"] == STATUS_OK),
        STATUS_WARN: sum(1 for c in checks if c["status"] == STATUS_WARN),
        STATUS_ERROR: sum(1 for c in checks if c["status"] == STATUS_ERROR),
    }
    overall = _worst_status(checks)

    return {
        "bet_type": bet_type,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overall_status": overall,
        "overall_label": STATUS_LABEL[overall],
        "summary": summary,
        "checks": checks,
        "checks_df": pd.DataFrame(checks),
        "missing_data": missing,
        "next_tasks": next_tasks,
        "fix_suggestions": fix_suggestions,
        "errors": error_list,
        "errors_df": pd.DataFrame(error_list) if error_list else pd.DataFrame(),
        "ops_errors": ops_errors,
        "quality": quality,
    }


def get_system_check_bundle(
    bet_type: str = "3連単",
    *,
    deep: bool = False,
    quality: Optional[dict] = None,
    score_bundle: Optional[dict] = None,
    learning_bundle: Optional[dict] = None,
    backup_bundle: Optional[dict] = None,
) -> dict:
    bundle = run_system_checks(
        bet_type,
        deep=deep,
        quality=quality,
        score_bundle=score_bundle,
        learning_bundle=learning_bundle,
        backup_bundle=backup_bundle,
    )
    bundle["lines"] = build_system_check_lines(bundle)
    return bundle


def save_system_check_report(bet_type: str = "3連単", bundle: Optional[dict] = None) -> Path:
    b = bundle or run_system_checks(bet_type)
    text = "\n".join(build_system_check_lines(b))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = CHECK_DIR / f"system_check_{ts}.txt"
    path.write_text(text, encoding="utf-8")
    (CHECK_DIR / "system_check_latest.txt").write_text(text, encoding="utf-8")
    return path


def build_system_check_lines(bundle: Optional[dict] = None, bet_type: str = "3連単") -> list[str]:
    b = bundle or run_system_checks(bet_type)
    lines = [
        f"【完成チェック】券種={b['bet_type']}  実行={b['checked_at']}",
        f"  総合: {b['overall_label']} "
        f"(正常{b['summary'][STATUS_OK]} / 注意{b['summary'][STATUS_WARN]} / エラー{b['summary'][STATUS_ERROR]})",
        "",
    ]

    lines.append("--- チェック結果 ---")
    for c in b.get("checks") or []:
        lines.append(f"  [{c['状態']}] {c['項目']}: {c['メッセージ']}")
        if c.get("詳細"):
            lines.append(f"      {c['詳細']}")
    lines.append("")

    if b.get("missing_data"):
        lines.append("--- 不足データ ---")
        for m in b["missing_data"]:
            lines.append(f"  - {m}")
        lines.append("")

    if b.get("next_tasks"):
        lines.append("--- 次にやるべき作業 ---")
        for t in b["next_tasks"]:
            lines.append(f"  > {t}")
        lines.append("")

    if b.get("fix_suggestions"):
        lines.append("--- 修正候補 ---")
        for f in b["fix_suggestions"]:
            lines.append(f"  * {f}")
        lines.append("")

    if b.get("errors"):
        lines.append("--- エラー一覧 ---")
        for e in b["errors"]:
            lines.append(f"  ! [{e.get('種別', '')}] {e.get('内容', '')}")
        lines.append("")

    return lines
