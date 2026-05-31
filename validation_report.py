"""検証レポート自動化 — AI・実戦判定・資金管理の成績検証"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from config import DATA_DIR
from db import db_session, get_connection

VALIDATION_DIR = DATA_DIR / "validation"
VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

__all__ = [
    "build_validation_lines",
    "build_validation_report",
    "empty_validation_report",
    "get_validation_bundle",
    "migrate_validation_table",
    "run_daily_validation",
    "safe_validation_period",
    "save_validation_report",
]

AMOUNT_BUCKETS = [
    (300, "300円以上"),
    (200, "200〜299円"),
    (100, "100〜199円"),
    (1, "1〜99円"),
    (0, "0円"),
]

VALIDATION_TABLE = """
CREATE TABLE IF NOT EXISTS validation_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    bet_type        TEXT NOT NULL,
    period_today    TEXT,
    report_path     TEXT,
    actual_recovery REAL,
    virtual_recovery REAL,
    status          TEXT NOT NULL DEFAULT 'running',
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def migrate_validation_table(conn) -> None:
    conn.executescript(VALIDATION_TABLE)


def _amount_bucket(amount: int) -> str:
    for threshold, label in AMOUNT_BUCKETS:
        if amount >= threshold:
            return label
    return "0円"


def _today_str() -> str:
    return date.today().strftime("%Y%m%d")


def period_range(period: str, ref: Optional[date] = None) -> tuple[str, str]:
    """today / week / month → YYYYMMDD 範囲"""
    d = ref or date.today()
    if period == "today":
        s = d.strftime("%Y%m%d")
        return s, s
    if period == "week":
        start = d - timedelta(days=6)
        return start.strftime("%Y%m%d"), d.strftime("%Y%m%d")
    if period == "month":
        start = d.replace(day=1)
        return start.strftime("%Y%m%d"), d.strftime("%Y%m%d")
    return "00000000", "99999999"


def filter_by_period(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if df.empty:
        return df
    sub = df.copy()
    sub["_date"] = sub["race_date"].astype(str).str.replace("-", "", regex=False)
    mask = sub["_date"].str.len() >= 8
    sub = sub[mask]
    sub = sub[(sub["_date"] >= start) & (sub["_date"] <= end)]
    return sub.drop(columns=["_date"], errors="ignore")


def group_stats(df: pd.DataFrame, col: str, label_col: str) -> pd.DataFrame:
    settled = df[df["status"] == "settled"].copy()
    if settled.empty:
        return pd.DataFrame()
    if col == "ai_score":
        from bet_tracker import _score_band

        settled[label_col] = settled["ai_score"].fillna(0).astype(float).map(_score_band)
    elif col == "bet_amount":
        settled[label_col] = settled["bet_amount"].fillna(0).astype(int).map(_amount_bucket)
    else:
        settled[label_col] = settled[col].fillna("不明").astype(str)

    agg = (
        settled.groupby(label_col, dropna=False)
        .agg(
            件数=("id", "count"),
            的中=("hit", "sum"),
            購入額=("bet_amount", "sum"),
            払戻=("payout", "sum"),
            収支=("profit", "sum"),
        )
        .reset_index()
    )
    agg["的中率"] = (agg["的中"] / agg["件数"] * 100).round(1)
    agg["回収率"] = (agg["払戻"] / agg["購入額"].replace(0, pd.NA) * 100).round(1)
    return agg.rename(columns={label_col: "区分"})


def period_report(df: pd.DataFrame, label: str) -> dict:
    from bet_tracker import _summarize

    summary = _summarize(df)
    settled = df[df["status"] == "settled"] if not df.empty else pd.DataFrame()
    return {
        "label": label,
        "summary": summary,
        "by_ai_score": group_stats(df, "ai_score", "score_band"),
        "by_verdict": group_stats(df, "verdict", "verdict"),
        "by_rank": group_stats(df, "ev_rank", "ev_rank"),
        "by_amount": group_stats(df, "bet_amount", "amount_band"),
        "settled_count": len(settled),
    }


def empty_period_summary() -> dict:
    return {
        "count": 0,
        "total_bet": 0,
        "total_payout": 0,
        "total_profit": 0,
        "recovery_rate": 0.0,
        "hit_rate": 0.0,
        "settled": 0,
        "pending": 0,
    }


def empty_period_report(label: str = "") -> dict:
    return {
        "label": label,
        "summary": empty_period_summary(),
        "by_ai_score": pd.DataFrame(),
        "by_verdict": pd.DataFrame(),
        "by_rank": pd.DataFrame(),
        "by_amount": pd.DataFrame(),
        "settled_count": 0,
    }


def empty_validation_report(bet_type: str = "3連単") -> dict:
    """検証データなし / Cloud初回用の空レポート"""
    return {
        "bet_type": bet_type,
        "ref_date": date.today().strftime("%Y%m%d"),
        "has_data": False,
        "today": empty_period_report("今日"),
        "today_virtual": empty_period_report("今日(仮想)"),
        "week": empty_period_report("今週"),
        "month": empty_period_report("今月"),
        "strong_conditions": pd.DataFrame(),
        "weak_conditions": pd.DataFrame(),
        "improvements": [],
        "lines": [],
        "history": pd.DataFrame(),
        "summary_all_actual": empty_period_summary(),
        "summary_all_virtual": empty_period_summary(),
        "streaks": {},
        "quality_valid_pct": 0.0,
    }


def safe_validation_period(report: dict, key: str) -> dict:
    """月次などキー欠落時も空期間（0件）を返す"""
    period = report.get(key) if report else None
    if isinstance(period, dict) and isinstance(period.get("summary"), dict):
        return period
    return empty_period_report(key)


def sync_battle_virtual_bets(
    battle_bundle: dict,
    bankroll_plan: dict,
    bet_type: str = "3連単",
) -> int:
    """買わなかった候補の仮想購入を同期（実戦判定・資金管理連動）"""
    from bet_tracker import add_bet_record, load_bet_records

    actual = load_bet_records(bet_type, is_virtual=0)
    bought = set(
        zip(actual["race_id"].astype(str), actual["combination"].astype(str))
        if not actual.empty
        else []
    )
    alloc_map = {
        str(a["race_id"]): int(a.get("recommended_yen") or 100)
        for a in bankroll_plan.get("allocations") or []
    }

    added = 0
    for card in battle_bundle.get("all_cards") or []:
        verdict = str(card.get("battle_verdict") or card.get("verdict") or "")
        if verdict in ("見送り", ""):
            continue
        rid = str(card["race_id"])
        amount = alloc_map.get(rid, 100)
        if amount <= 0:
            continue
        picks = card.get("picks") or []
        if not picks:
            continue
        pick = picks[0]
        combo = str(pick.get("combination") or "")
        if not combo or (rid, combo) in bought:
            continue
        res = add_bet_record(
            race_id=rid,
            bet_type=bet_type,
            combination=combo,
            bet_amount=amount,
            is_virtual=1,
            race_date=str(card.get("race_date") or ""),
            venue_name=str(card.get("venue_name") or ""),
            race_no=int(card.get("race_no") or 0),
            ai_score=float(card.get("pre_race_score") or card.get("ai_total_score") or 0),
            ev_rank=str(card.get("ev_rank") or ""),
            pick_rank=1,
            verdict=verdict,
            odds=float(pick["odds"]) if pick.get("odds") else None,
            note=f"仮想:{verdict}",
        )
        if res.get("ok"):
            added += 1
    return added


def _market_condition_stats(bet_type: str = "3連単") -> tuple[pd.DataFrame, pd.DataFrame]:
    """DB全体の条件別回収（AI強弱）"""
    from analyze import analyze_by_venue
    from learning import load_learned_patterns

    strong_rows: list[dict] = []
    weak_rows: list[dict] = []

    venue_df = analyze_by_venue(bet_type)
    if not venue_df.empty:
        for row in venue_df.itertuples():
            item = {
                "条件": f"競輪場:{row.venue_name}",
                "レース数": row.races,
                "回収率": row.recovery_rate,
                "的中率": row.hit_rate,
            }
            if row.recovery_rate >= 100 and row.races >= 2:
                strong_rows.append(item)
            elif row.recovery_rate <= 75 and row.races >= 2:
                weak_rows.append(item)

    patterns = load_learned_patterns(bet_type)
    if not patterns.empty:
        for _, row in patterns.iterrows():
            item = {
                "条件": row["condition_label"],
                "レース数": int(row["races"]),
                "回収率": float(row["recovery_rate"]),
                "的中率": float(row["hit_rate"]),
            }
            if row["recovery_rate"] >= 100:
                strong_rows.append(item)
            elif row["recovery_rate"] <= 75:
                weak_rows.append(item)

    strong = pd.DataFrame(strong_rows)
    weak = pd.DataFrame(weak_rows)
    if not strong.empty:
        strong = strong.sort_values("回収率", ascending=False).head(10)
    if not weak.empty:
        weak = weak.sort_values("回収率").head(10)
    return strong, weak


def build_improvements(
    *,
    today_actual: dict,
    today_virtual: dict,
    week_actual: dict,
    month_actual: dict,
    by_score_actual: pd.DataFrame,
    by_verdict_actual: pd.DataFrame,
    by_amount_actual: pd.DataFrame,
    quality_valid_pct: float = 100.0,
    lose_streak: int = 0,
) -> list[str]:
    """改善ポイントを自動生成"""
    tips: list[str] = []

    ta = today_actual.get("summary", {})
    tv = today_virtual.get("summary", {})
    wa = week_actual.get("summary", {})
    ma = month_actual.get("summary", {})

    if ta.get("settled", 0) >= 3 and ta.get("recovery_rate", 0) < 75:
        tips.append("今日の実購入回収率が低い — 買い候補の閾値を上げることを検討")

    if tv.get("settled", 0) >= 3 and tv.get("recovery_rate", 0) > ta.get("recovery_rate", 0) + 15:
        tips.append("買わなかった候補の仮想成績の方が良い — 実戦判定の見送り基準を見直し")

    if wa.get("settled", 0) >= 5 and wa.get("recovery_rate", 0) < 80:
        tips.append("今週の回収率が80%未満 — 1日上限・1レース上限の引き下げを推奨")

    if ma.get("total_profit", 0) < 0 and ma.get("settled", 0) >= 10:
        tips.append("今月マイナス収支 — 資金管理の連敗減額ルールを維持してください")

    if not by_score_actual.empty:
        low = by_score_actual[by_score_actual["回収率"].fillna(0) < 70]
        if not low.empty:
            tips.append(
                f"AIスコア帯「{low.iloc[0]['区分']}」の回収率が低い — この帯の購入を控えめに"
            )
        high = by_score_actual[by_score_actual["回収率"].fillna(0) >= 110]
        if not high.empty:
            tips.append(
                f"AIスコア帯「{high.iloc[0]['区分']}」は好調 — 優先候補として維持"
            )

    if not by_verdict_actual.empty:
        skip_like = by_verdict_actual[by_verdict_actual["区分"].astype(str).str.contains("見送")]
        if not skip_like.empty and skip_like.iloc[0].get("回収率", 100) < 60:
            tips.append("見送り判定は機能しています — 現状維持でOK")

    if not by_amount_actual.empty:
        heavy = by_amount_actual[by_amount_actual["区分"].astype(str).str.contains("300")]
        if not heavy.empty and heavy.iloc[0].get("回収率", 100) < 80:
            tips.append("300円以上の購入回収率が低い — Sランク以外は200円以下に")

    if quality_valid_pct < 70:
        tips.append("有効データ比率が低い — データ収集・品質チェックを実行")

    if lose_streak >= 3:
        tips.append(f"{lose_streak}連敗中 — 自動減額モードを継続（無理な追い禁止）")

    if not tips:
        tips.append("大きな問題は検出されていません — 現行ルールで継続検証")

    return tips[:8]


def build_validation_report(
    bet_type: str = "3連単",
    *,
    battle_bundle: Optional[dict] = None,
    bankroll_plan: Optional[dict] = None,
    sync_virtual: bool = True,
    ref_date: Optional[date] = None,
) -> dict:
    """検証レポート本体"""
    if battle_bundle is None:
        from battle_judge import get_battle_judge_bundle

        battle_bundle = get_battle_judge_bundle(bet_type)
    if bankroll_plan is None:
        from bankroll import get_bankroll_bundle

        bankroll_plan = get_bankroll_bundle(bet_type, battle_bundle=battle_bundle)

    if sync_virtual:
        sync_battle_virtual_bets(battle_bundle, bankroll_plan, bet_type)

    from bet_tracker import load_bet_records, settle_pending_bets

    settle_pending_bets(bet_type)
    actual = load_bet_records(bet_type, is_virtual=0)
    virtual = load_bet_records(bet_type, is_virtual=1)

    today_s, today_e = period_range("today", ref_date)
    week_s, week_e = period_range("week", ref_date)
    month_s, month_e = period_range("month", ref_date)

    today_a = period_report(filter_by_period(actual, today_s, today_e), "今日")
    today_v = period_report(filter_by_period(virtual, today_s, today_e), "今日(仮想)")
    week_a = period_report(filter_by_period(actual, week_s, week_e), "今週")
    month_a = period_report(filter_by_period(actual, month_s, month_e), "今月")

    strong, weak = _market_condition_stats(bet_type)

    from bankroll import compute_streaks
    from data_quality import get_quality_bundle

    qb = get_quality_bundle(bet_type)
    valid_pct = 0.0
    if qb.get("total_races"):
        valid_pct = round(qb["valid_races"] / qb["total_races"] * 100, 1)

    streaks = compute_streaks(bet_type)
    improvements = build_improvements(
        today_actual=today_a,
        today_virtual=today_v,
        week_actual=week_a,
        month_actual=month_a,
        by_score_actual=today_a["by_ai_score"],
        by_verdict_actual=today_a["by_verdict"],
        by_amount_actual=today_a["by_amount"],
        quality_valid_pct=valid_pct,
        lose_streak=streaks.get("lose_streak", 0),
    )

    return {
        "bet_type": bet_type,
        "ref_date": (ref_date or date.today()).strftime("%Y%m%d"),
        "has_data": not actual.empty or not virtual.empty,
        "today": today_a,
        "today_virtual": today_v,
        "week": week_a,
        "month": month_a,
        "strong_conditions": strong,
        "weak_conditions": weak,
        "improvements": improvements,
        "summary_all_actual": _summarize(actual),
        "summary_all_virtual": _summarize(virtual),
        "streaks": streaks,
        "quality_valid_pct": valid_pct,
    }


def save_validation_report(
    bet_type: str = "3連単",
    *,
    battle_bundle: Optional[dict] = None,
    bankroll_plan: Optional[dict] = None,
    output: Optional[str] = None,
) -> Path:
    """検証レポートをファイル保存"""
    report = build_validation_report(
        bet_type,
        battle_bundle=battle_bundle,
        bankroll_plan=bankroll_plan,
        sync_virtual=True,
    )
    lines = build_validation_lines(report)
    text = "\n".join(lines)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(output) if output else VALIDATION_DIR / f"validation_{ts}.txt"
    path.write_text(text, encoding="utf-8")
    latest = VALIDATION_DIR / "validation_latest.txt"
    latest.write_text(text, encoding="utf-8")

    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_session() as conn:
        migrate_validation_table(conn)
        conn.execute(
            """
            INSERT INTO validation_runs (
                started_at, finished_at, bet_type, period_today,
                report_path, actual_recovery, virtual_recovery, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ok')
            """,
            (
                started,
                started,
                bet_type,
                report["ref_date"],
                str(path),
                report["today"]["summary"].get("recovery_rate"),
                report["today_virtual"]["summary"].get("recovery_rate"),
            ),
        )

    return path


def run_daily_validation(bet_type: str = "3連単") -> dict:
    """日次検証（ops から呼び出し）"""
    path = save_validation_report(bet_type)
    report = build_validation_report(bet_type, sync_virtual=False)
    return {
        "ok": True,
        "report_path": str(path),
        "latest_path": str(VALIDATION_DIR / "validation_latest.txt"),
        "today_recovery": report["today"]["summary"].get("recovery_rate"),
        "improvements": report["improvements"],
    }


def get_validation_bundle(
    bet_type: str = "3連単",
    *,
    battle_bundle: Optional[dict] = None,
    bankroll_plan: Optional[dict] = None,
    sync_virtual: bool = True,
) -> dict:
    try:
        report = build_validation_report(
            bet_type,
            battle_bundle=battle_bundle,
            bankroll_plan=bankroll_plan,
            sync_virtual=sync_virtual,
        )
        report["lines"] = build_validation_lines(report)
        conn = get_connection()
        migrate_validation_table(conn)
        runs = pd.read_sql(
            """
            SELECT started_at, bet_type, actual_recovery, virtual_recovery, report_path, status
            FROM validation_runs ORDER BY id DESC LIMIT 10
            """,
            conn,
        )
        conn.close()
        report["history"] = runs
        return report
    except Exception:
        report = empty_validation_report(bet_type)
        report["lines"] = []
        return report


def _summary_line(s: dict, label: str) -> str:
    if s.get("settled", 0) == 0:
        return f"  {label}: 確定データなし"
    return (
        f"  {label}: 収支{s['total_profit']:,}円 "
        f"回収{s['recovery_rate']}% 的中{s['hit_rate']}% ({s['settled']}件)"
    )


def build_validation_lines(report: Optional[dict] = None, bet_type: str = "3連単") -> list[str]:
    try:
        r = report or build_validation_report(bet_type)
    except Exception:
        r = empty_validation_report(bet_type)
    lines = [
        f"【検証レポート】券種={bet_type}  基準日={r.get('ref_date', '')}",
        "",
        "=== 日次 ===",
        _summary_line(safe_validation_period(r, "today")["summary"], "実購入"),
        _summary_line(safe_validation_period(r, "today_virtual")["summary"], "仮想(未購入)"),
        "",
        "=== 週次 ===",
        _summary_line(safe_validation_period(r, "week")["summary"], "実購入"),
        "",
        "=== 月次 ===",
        _summary_line(safe_validation_period(r, "month")["summary"], "実購入"),
        "",
    ]

    for title, key in [
        ("AIスコア別回収率(今日)", ("today", "by_ai_score")),
        ("判定別回収率(今日)", ("today", "by_verdict")),
        ("推奨金額別収支(今日)", ("today", "by_amount")),
    ]:
        block = safe_validation_period(r, key[0]).get(key[1], pd.DataFrame())
        lines.append(f"--- {title} ---")
        if block.empty:
            lines.append("  （データなし）")
        else:
            lines.append(block.to_string(index=False))
        lines.append("")

    lines.append("--- AIが強い条件 TOP ---")
    strong = r.get("strong_conditions", pd.DataFrame())
    if strong.empty:
        lines.append("  （なし）")
    else:
        lines.append(strong.to_string(index=False))
    lines.append("")

    lines.append("--- AIが弱い条件 TOP ---")
    weak = r.get("weak_conditions", pd.DataFrame())
    if weak.empty:
        lines.append("  （なし）")
    else:
        lines.append(weak.to_string(index=False))
    lines.append("")

    lines.append("--- 改善ポイント ---")
    for tip in r.get("improvements") or []:
        lines.append(f"  * {tip}")
    lines.append("")
    return lines
