"""保存レースのデータ品質チェック — 学習可否判定"""

from __future__ import annotations

import re
from typing import Optional

import pandas as pd

from analyze import winning_combinations
from db import get_connection

MIN_ENTRIES = 5
MIN_FINISH_PARTS = 3
ODDS_MIN = 1.01
ODDS_MAX = 99999.0
MIN_BET_ODDS = 10

ISSUE_DUPLICATE = "duplicate"
ISSUE_MISSING = "missing"
ISSUE_NO_ODDS = "no_odds"
ISSUE_NO_RESULT = "no_result"
ISSUE_ANOMALY = "anomaly"

FIX_ACTIONS = {
    ISSUE_DUPLICATE: "重複race_idを確認し、誤登録を削除",
    ISSUE_MISSING: "python main.py fetch {race_id} で出走表を再取得",
    ISSUE_NO_ODDS: "python main.py fetch {race_id} でオッズ再取得",
    ISSUE_NO_RESULT: "python main.py fetch {race_id} --with-result で結果取得",
    ISSUE_ANOMALY: "python main.py fetch {race_id} --with-result で再取得・確認",
}


def _parse_finish_parts(finish_order: Optional[str]) -> list[str]:
    if not finish_order or not str(finish_order).strip():
        return []
    return [p.strip() for p in str(finish_order).split(",") if p.strip()]


def _load_race_stats(bet_type: str = "3連単") -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """
        SELECT
            r.race_id,
            r.race_date,
            r.venue_code,
            r.venue_name,
            r.race_no,
            r.grade,
            r.distance,
            res.finish_order,
            res.trifecta_pay,
            res.exacta_pay,
            (SELECT COUNT(*) FROM entries e WHERE e.race_id = r.race_id) AS entry_count,
            (SELECT COUNT(*) FROM entries e
             WHERE e.race_id = r.race_id
               AND (e.racer_name IS NULL OR TRIM(e.racer_name) = '')) AS missing_names,
            (SELECT COUNT(*) FROM odds o WHERE o.race_id = r.race_id) AS odds_total,
            (SELECT COUNT(*) FROM odds o
             WHERE o.race_id = r.race_id AND o.bet_type = ?) AS bet_odds_count,
            (SELECT MIN(o.odds) FROM odds o
             WHERE o.race_id = r.race_id AND o.bet_type = ?) AS min_odds,
            (SELECT MAX(o.odds) FROM odds o
             WHERE o.race_id = r.race_id AND o.bet_type = ?) AS max_odds,
            (SELECT COUNT(*) FROM odds o
             WHERE o.race_id = r.race_id AND o.bet_type = ?
               AND (o.odds < ? OR o.odds > ?)) AS bad_odds_count
        FROM races r
        LEFT JOIN results res ON r.race_id = res.race_id
        ORDER BY r.race_date DESC, r.venue_code, r.race_no
        """,
        conn,
        params=(bet_type, bet_type, bet_type, bet_type, ODDS_MIN, ODDS_MAX),
    )
    conn.close()
    return df


def _load_duplicate_groups() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """
        SELECT race_date, venue_code, race_no,
               COUNT(*) AS dup_count,
               GROUP_CONCAT(race_id, ', ') AS race_ids
        FROM races
        GROUP BY race_date, venue_code, race_no
        HAVING COUNT(*) > 1
        ORDER BY race_date DESC, venue_code, race_no
        """,
        conn,
    )
    conn.close()
    return df


def _duplicate_race_ids(dup_df: pd.DataFrame) -> set[str]:
    ids: set[str] = set()
    if dup_df.empty:
        return ids
    for row in dup_df["race_ids"]:
        for rid in str(row).split(","):
            rid = rid.strip()
            if rid:
                ids.add(rid)
    return ids


def _check_winning_odds(race_id: str, bet_type: str, finish_order: str) -> bool:
    wins = winning_combinations(bet_type, finish_order)
    if not wins:
        return False
    win_combo = next(iter(wins))
    conn = get_connection()
    row = conn.execute(
        """
        SELECT 1 FROM odds
        WHERE race_id = ? AND bet_type = ? AND combination = ?
        LIMIT 1
        """,
        (race_id, bet_type, win_combo),
    ).fetchone()
    conn.close()
    return row is not None


def assess_race(row: pd.Series, bet_type: str, duplicate_ids: set[str]) -> dict:
    """1レース分の品質判定"""
    race_id = str(row["race_id"])
    issues: list[str] = []
    issue_types: list[str] = []
    missing_fields: list[str] = []

    if race_id in duplicate_ids:
        issues.append("同一日・場・Rの重複登録")
        issue_types.append(ISSUE_DUPLICATE)

    if not row.get("race_date") or str(row["race_date"]).strip() == "":
        missing_fields.append("race_date")
    if not row.get("venue_code") or str(row["venue_code"]).strip() == "":
        missing_fields.append("venue_code")
    if not row.get("venue_name") or str(row["venue_name"]).strip() == "":
        missing_fields.append("venue_name")
    if pd.isna(row.get("race_no")):
        missing_fields.append("race_no")

    entry_count = int(row.get("entry_count") or 0)
    if entry_count == 0:
        missing_fields.append("entries")
    elif entry_count < MIN_ENTRIES:
        missing_fields.append(f"entries({entry_count}<{MIN_ENTRIES})")

    missing_names = int(row.get("missing_names") or 0)
    if missing_names > 0:
        missing_fields.append(f"racer_name({missing_names}件)")

    if missing_fields:
        issues.append("欠損: " + ", ".join(missing_fields))
        issue_types.append(ISSUE_MISSING)

    bet_odds_count = int(row.get("bet_odds_count") or 0)
    odds_total = int(row.get("odds_total") or 0)
    if bet_odds_count < MIN_BET_ODDS:
        if odds_total == 0:
            issues.append(f"オッズなし（{bet_type}）")
        else:
            issues.append(f"{bet_type}オッズ不足（{bet_odds_count}件）")
        issue_types.append(ISSUE_NO_ODDS)

    finish_order = row.get("finish_order")
    finish_parts = _parse_finish_parts(finish_order)
    if not finish_parts:
        issues.append("結果なし")
        issue_types.append(ISSUE_NO_RESULT)
    elif len(finish_parts) < MIN_FINISH_PARTS:
        issues.append(f"着順不足（{len(finish_parts)}件）")
        issue_types.append(ISSUE_NO_RESULT)

    anomalies: list[str] = []
    bad_odds = int(row.get("bad_odds_count") or 0)
    if bad_odds > 0:
        anomalies.append(f"異常オッズ{bad_odds}件")

    min_odds = row.get("min_odds")
    max_odds = row.get("max_odds")
    if pd.notna(min_odds) and float(min_odds) < ODDS_MIN:
        anomalies.append(f"最低オッズ={float(min_odds):.2f}")
    if pd.notna(max_odds) and float(max_odds) > ODDS_MAX:
        anomalies.append(f"最高オッズ={float(max_odds):.0f}")

    if finish_parts:
        invalid = [p for p in finish_parts if not re.fullmatch(r"\d+", p)]
        if invalid:
            anomalies.append("着順形式不正")
        brackets = {int(p) for p in finish_parts if re.fullmatch(r"\d+", p)}
        if entry_count > 0 and brackets and max(brackets) > entry_count + 2:
            anomalies.append("着順と出走数の不整合")

    if bet_type == "3連単" and finish_parts and pd.isna(row.get("trifecta_pay")):
        anomalies.append("3連単払戻なし")
    if bet_type == "2車単" and finish_parts and pd.isna(row.get("exacta_pay")):
        anomalies.append("2車単払戻なし")

    if finish_parts and bet_odds_count >= MIN_BET_ODDS:
        if not _check_winning_odds(race_id, bet_type, str(finish_order)):
            anomalies.append("勝ち組オッズ未登録")

    if anomalies:
        issues.append("異常: " + ", ".join(anomalies))
        issue_types.append(ISSUE_ANOMALY)

    learnable = len(issues) == 0

    score = 100
    if ISSUE_DUPLICATE in issue_types:
        score -= 30
    if ISSUE_MISSING in issue_types:
        score -= 20
    if ISSUE_NO_ODDS in issue_types:
        score -= 25
    if ISSUE_NO_RESULT in issue_types:
        score -= 25
    if ISSUE_ANOMALY in issue_types:
        score -= 15
    score = max(0, score)

    fix_parts = []
    for it in dict.fromkeys(issue_types):
        fix_parts.append(FIX_ACTIONS[it].format(race_id=race_id))
    fix_action = " / ".join(fix_parts) if fix_parts else ""

    return {
        "race_id": race_id,
        "race_date": row.get("race_date"),
        "venue_name": row.get("venue_name"),
        "race_no": row.get("race_no"),
        "entry_count": entry_count,
        "bet_odds_count": bet_odds_count,
        "issues": issues,
        "issue_types": issue_types,
        "issue_text": " / ".join(issues) if issues else "OK",
        "learnable": learnable,
        "quality_score": score,
        "fix_action": fix_action,
    }


def run_quality_audit(bet_type: str = "3連単") -> dict:
    """全レースの品質監査"""
    stats = _load_race_stats(bet_type)
    dup_df = _load_duplicate_groups()
    dup_ids = _duplicate_race_ids(dup_df)

    if stats.empty:
        return {
            "bet_type": bet_type,
            "total_races": 0,
            "valid_races": 0,
            "missing_count": 0,
            "duplicate_count": 0,
            "no_odds_count": 0,
            "no_result_count": 0,
            "anomaly_count": 0,
            "quality_score": 0.0,
            "race_details": pd.DataFrame(),
            "excluded": pd.DataFrame(),
            "fix_candidates": pd.DataFrame(),
            "duplicate_groups": dup_df,
            "summary_by_issue": pd.DataFrame(),
        }

    rows = [assess_race(row, bet_type, dup_ids) for row in stats.to_dict("records")]
    details = pd.DataFrame(rows)

    total = len(details)
    valid = int(details["learnable"].sum())
    missing_count = int(details["issue_types"].apply(lambda xs: ISSUE_MISSING in xs).sum())
    duplicate_count = int(details["issue_types"].apply(lambda xs: ISSUE_DUPLICATE in xs).sum())
    no_odds_count = int(details["issue_types"].apply(lambda xs: ISSUE_NO_ODDS in xs).sum())
    no_result_count = int(details["issue_types"].apply(lambda xs: ISSUE_NO_RESULT in xs).sum())
    anomaly_count = int(details["issue_types"].apply(lambda xs: ISSUE_ANOMALY in xs).sum())

    quality_score = round(float(details["quality_score"].mean()), 1) if total else 0.0

    excluded = details[~details["learnable"]].copy()
    fix_candidates = excluded[
        ["race_id", "race_date", "venue_name", "race_no", "issue_text", "fix_action", "quality_score"]
    ].copy()
    fix_candidates = fix_candidates.rename(
        columns={
            "race_id": "race_id",
            "race_date": "日付",
            "venue_name": "競輪場",
            "race_no": "R",
            "issue_text": "問題",
            "fix_action": "修正候補",
            "quality_score": "品質",
        }
    )

    summary_rows = [
        {"区分": "重複", "件数": duplicate_count},
        {"区分": "欠損", "件数": missing_count},
        {"区分": "オッズなし", "件数": no_odds_count},
        {"区分": "結果なし", "件数": no_result_count},
        {"区分": "異常値", "件数": anomaly_count},
        {"区分": "学習可", "件数": valid},
    ]

    return {
        "bet_type": bet_type,
        "total_races": total,
        "valid_races": valid,
        "missing_count": missing_count,
        "duplicate_count": duplicate_count,
        "no_odds_count": no_odds_count,
        "no_result_count": no_result_count,
        "anomaly_count": anomaly_count,
        "quality_score": quality_score,
        "race_details": details,
        "excluded": excluded,
        "fix_candidates": fix_candidates,
        "duplicate_groups": dup_df,
        "summary_by_issue": pd.DataFrame(summary_rows),
    }


def get_quality_bundle(bet_type: str = "3連単", *, refresh: bool = True) -> dict:
    audit = run_quality_audit(bet_type)
    audit["has_data"] = audit["total_races"] > 0
    audit["invalid_races"] = audit["total_races"] - audit["valid_races"]
    audit["valid_pct"] = (
        round(audit["valid_races"] / audit["total_races"] * 100, 1)
        if audit["total_races"]
        else 0.0
    )
    return audit


def build_quality_lines(bet_type: str = "3連単") -> list[str]:
    b = get_quality_bundle(bet_type)
    lines = [f"【データ品質】券種={bet_type}", ""]
    if not b["has_data"]:
        lines.append("レースデータがありません。")
        lines.append("")
        return lines

    lines.append(f"  総レース数: {b['total_races']}")
    lines.append(f"  有効(学習可): {b['valid_races']} ({b['valid_pct']}%)")
    lines.append(f"  除外: {b['invalid_races']}")
    lines.append(f"  欠損: {b['missing_count']} / 重複: {b['duplicate_count']}")
    lines.append(
        f"  オッズなし: {b['no_odds_count']} / 結果なし: {b['no_result_count']} / 異常: {b['anomaly_count']}"
    )
    lines.append(f"  品質スコア: {b['quality_score']}/100")
    lines.append("")

    if not b["fix_candidates"].empty:
        lines.append("--- 修正候補 TOP10 ---")
        lines.append(
            b["fix_candidates"].head(10)[["日付", "競輪場", "R", "問題", "修正候補"]].to_string(index=False)
        )
        lines.append("")

    if not b["duplicate_groups"].empty:
        lines.append("--- 重複グループ ---")
        lines.append(b["duplicate_groups"].to_string(index=False))
        lines.append("")

    return lines
