"""ライン分析 — 構成・有利/危険・番手期待値"""

from typing import Optional

import pandas as pd

from analyze import SENKO_STYLES, load_entries_frame
from db import get_connection
from race_features import update_line_from_api

JIRIKI_STYLES = ("逃", "捲", "両")
ADVANTAGE_SCORE_THRESHOLD = 52
DANGER_SCORE_THRESHOLD = 38


def parse_line_groups(line_info: str) -> list[list[int]]:
    """line_info 文字列 → 車番グループのリスト"""
    if not line_info or line_info == "不明":
        return []
    groups: list[list[int]] = []
    for part in str(line_info).split("|"):
        brackets = []
        for token in part.strip().split("-"):
            if token.strip().isdigit():
                brackets.append(int(token.strip()))
        if brackets:
            groups.append(brackets)
    return groups


def _entry_map(entries: pd.DataFrame, race_id: str) -> dict[int, dict]:
    sub = entries[entries["race_id"] == race_id]
    out: dict[int, dict] = {}
    for _, row in sub.iterrows():
        out[int(row["bracket"])] = {
            "racer_name": row["racer_name"],
            "region": str(row.get("region") or ""),
            "style": str(row.get("style") or ""),
            "racer_grade": str(row.get("racer_grade") or ""),
        }
    return out


def _is_jiriki(style: str, grade: str) -> bool:
    if style in JIRIKI_STYLES:
        return True
    g = grade.upper().strip()
    return g.startswith("S") or g in ("SS", "S1", "S2")


def _line_ai_score(
    length: int,
    head_style: str,
    ban_style: str,
    region_link: bool,
    senko_line: bool,
    jiriki_count: int,
    solo: bool,
) -> tuple[float, list[str]]:
    """ライン別AIスコア（0〜100）と理由"""
    score = 0.0
    reasons: list[str] = []

    if length >= 3:
        score += 14
        reasons.append(f"ライン長{length}（団結力あり）")
    elif length == 2:
        score += 10
        reasons.append("ライン長2（コンパクト）")
    elif solo:
        score += 6
        reasons.append("単騎（独立策動）")

    if head_style in ("逃", "捲"):
        score += 16
        reasons.append(f"先頭{head_style}（先行ライン）")
    elif head_style == "両":
        score += 12
        reasons.append("先頭が両（自在）")

    if length >= 2 and ban_style in ("捲", "両", "逃"):
        score += 14
        reasons.append(f"番手{ban_style}（番手有利）")
    elif length >= 2 and ban_style:
        score += 5
        reasons.append(f"番手{ban_style}")

    if region_link and length >= 2:
        score += 12
        reasons.append("地区連携（同地区複数）")

    if senko_line:
        score += 10
        reasons.append("先行脚質を含むライン")

    score += min(12, jiriki_count * 4)
    if jiriki_count:
        reasons.append(f"自力型{jiriki_count}名")

    if solo and length == 1:
        score -= 5

    if head_style in ("追", "差") and length >= 2:
        score -= 8
        reasons.append("追い・差し先頭はライン不利になりやすい")

    final = round(min(100.0, max(0.0, score)), 1)
    return final, reasons[:6]


def analyze_race_lines(
    race_id: str,
    line_info: str,
    entries: pd.DataFrame,
    venue_name: str = "",
    race_no: int = 0,
) -> dict:
    """1レースのライン分析"""
    groups = parse_line_groups(line_info)
    emap = _entry_map(entries, race_id)

    lines: list[dict] = []
    solo_alerts: list[dict] = []
    ban_te_rows: list[dict] = []

    for idx, brackets in enumerate(groups, start=1):
        length = len(brackets)
        solo = length == 1
        members = []
        regions: list[str] = []
        styles: list[str] = []
        jiriki_count = 0
        senko_line = False

        for b in brackets:
            e = emap.get(b, {})
            style = e.get("style", "")
            region = e.get("region", "")
            grade = e.get("racer_grade", "")
            members.append(
                {
                    "bracket": b,
                    "racer_name": e.get("racer_name", ""),
                    "region": region,
                    "style": style,
                    "racer_grade": grade,
                    "jiriki": _is_jiriki(style, grade),
                }
            )
            if region:
                regions.append(region)
            styles.append(style)
            if _is_jiriki(style, grade):
                jiriki_count += 1
            if style in SENKO_STYLES:
                senko_line = True

        head_style = styles[0] if styles else ""
        ban_style = styles[1] if length >= 2 else ""
        region_link = len(regions) >= 2 and len(set(regions)) == 1
        ban_advantage = length >= 2 and ban_style in ("捲", "両", "逃")

        line_score, line_reasons = _line_ai_score(
            length, head_style, ban_style, region_link, senko_line, jiriki_count, solo
        )

        line_label = "-".join(str(b) for b in brackets)
        line_rec = {
            "race_id": race_id,
            "venue_name": venue_name,
            "race_no": race_no,
            "line_no": idx,
            "line_label": line_label,
            "line_length": length,
            "solo": solo,
            "head_bracket": brackets[0] if brackets else None,
            "ban_bracket": brackets[1] if length >= 2 else None,
            "head_style": head_style,
            "ban_style": ban_style,
            "ban_advantage": ban_advantage,
            "region_link": region_link,
            "regions": ",".join(sorted(set(regions))) if regions else "",
            "senko_line": senko_line,
            "jiriki_count": jiriki_count,
            "line_ai_score": line_score,
            "line_reasons": line_reasons,
            "members_json": members,
        }
        lines.append(line_rec)

        if solo:
            solo_alerts.append(
                {
                    "race_id": race_id,
                    "venue_name": venue_name,
                    "race_no": race_no,
                    "bracket": brackets[0],
                    "racer_name": members[0]["racer_name"] if members else "",
                    "style": head_style,
                    "line_ai_score": line_score,
                    "alert": "単騎はライン援護なし。消耗戦・挟まれに注意",
                }
            )

        if length >= 2:
            ban_ev = line_score * 0.45 + (14 if ban_advantage else 0)
            ban_te_rows.append(
                {
                    "race_id": race_id,
                    "venue_name": venue_name,
                    "race_no": race_no,
                    "line_no": idx,
                    "line_label": line_label,
                    "ban_bracket": brackets[1],
                    "ban_racer": members[1]["racer_name"] if len(members) > 1 else "",
                    "ban_style": ban_style,
                    "ban_advantage": ban_advantage,
                    "ban_expect_score": round(min(100, ban_ev), 1),
                    "line_ai_score": line_score,
                }
            )

    senko_line_count = sum(1 for ln in lines if ln["senko_line"])
    solo_count = sum(1 for ln in lines if ln["solo"])
    max_length = max((ln["line_length"] for ln in lines), default=0)
    total_jiriki = sum(ln["jiriki_count"] for ln in lines)
    region_link_lines = sum(1 for ln in lines if ln["region_link"])

    advantageous = [ln for ln in lines if ln["line_ai_score"] >= ADVANTAGE_SCORE_THRESHOLD]
    dangerous = [
        ln for ln in lines
        if ln["line_ai_score"] <= DANGER_SCORE_THRESHOLD
        or (ln["solo"] and ln["line_ai_score"] < 45)
        or (ln["line_length"] >= 2 and ln["head_style"] in ("追", "差"))
    ]

    advantageous.sort(key=lambda x: x["line_ai_score"], reverse=True)
    dangerous.sort(key=lambda x: x["line_ai_score"])
    ban_te_rows.sort(key=lambda x: x["ban_expect_score"], reverse=True)

    return {
        "race_id": race_id,
        "venue_name": venue_name,
        "race_no": race_no,
        "line_info": line_info,
        "line_count": len(lines),
        "senko_line_count": senko_line_count,
        "solo_count": solo_count,
        "max_line_length": max_length,
        "total_jiriki": total_jiriki,
        "region_link_lines": region_link_lines,
        "lines": lines,
        "advantageous": advantageous,
        "dangerous": dangerous,
        "solo_alerts": solo_alerts,
        "ban_te": ban_te_rows,
    }


def build_all_line_analysis(*, fetch_missing: bool = False) -> tuple[pd.DataFrame, list[dict]]:
    """全レースのライン分析

    fetch_missing=False のとき API 取得をスキップ（UI 読込高速化、DB の line_info のみ使用）
    """
    entries = load_entries_frame()
    conn = get_connection()
    races = pd.read_sql(
        """
        SELECT race_id, race_date, venue_name, race_no, line_info, line_count
        FROM races ORDER BY race_date DESC, race_id
        """,
        conn,
    )
    conn.close()

    if races.empty:
        return pd.DataFrame(), []

    race_reports: list[dict] = []
    line_rows: list[dict] = []

    for _, race in races.iterrows():
        race_id = race["race_id"]
        line_info = race.get("line_info") or ""
        if not line_info or line_info == "不明" or pd.isna(line_info):
            if fetch_missing:
                try:
                    line_info, _ = update_line_from_api(race_id)
                except Exception:
                    line_info = "不明"
            else:
                line_info = "不明"

        report = analyze_race_lines(
            race_id,
            str(line_info),
            entries,
            str(race.get("venue_name") or ""),
            int(race.get("race_no") or 0),
        )
        report["race_date"] = race.get("race_date")
        race_reports.append(report)

        for ln in report["lines"]:
            row = {k: v for k, v in ln.items() if k != "members_json" and k != "line_reasons"}
            row["line_reasons"] = " / ".join(ln.get("line_reasons") or [])
            line_rows.append(row)

    return pd.DataFrame(line_rows), race_reports


def build_line_analysis_lines(reports: Optional[list[dict]] = None) -> list[str]:
    if reports is None:
        _, reports = build_all_line_analysis()
    lines = ["【ライン分析】", ""]
    if not reports:
        lines.append("データがありません。")
        return lines

    lines.append(f"対象レース: {len(reports)}")
    lines.append("")
    for rep in reports[:15]:
        lines.append(
            f"--- {rep['venue_name']} {rep['race_no']}R ({rep['line_info']}) ---"
        )
        lines.append(
            f"  先行ライン{rep['senko_line_count']} / 単騎{rep['solo_count']} / "
            f"自力計{rep['total_jiriki']} / 地区連携{rep['region_link_lines']}本"
        )
        if rep["advantageous"]:
            top = rep["advantageous"][0]
            lines.append(
                f"  有利: ライン{top['line_no']} {top['line_label']} "
                f"スコア{top['line_ai_score']}"
            )
        if rep["dangerous"]:
            d = rep["dangerous"][0]
            lines.append(
                f"  危険: ライン{d['line_no']} {d['line_label']} "
                f"スコア{d['line_ai_score']}"
            )
        lines.append("")
    return lines


def get_line_analysis_bundle(*, fetch_missing: bool = False) -> dict:
    """Streamlit 用"""
    lines_df, race_reports = build_all_line_analysis(fetch_missing=fetch_missing)

    advantageous: list[dict] = []
    dangerous: list[dict] = []
    solo_alerts: list[dict] = []
    ban_te: list[dict] = []

    for rep in race_reports:
        for ln in rep.get("advantageous", []):
            advantageous.append(ln)
        for ln in rep.get("dangerous", []):
            dangerous.append(ln)
        for s in rep.get("solo_alerts", []):
            solo_alerts.append(s)
        for b in rep.get("ban_te", []):
            ban_te.append(b)

    advantageous.sort(key=lambda x: x["line_ai_score"], reverse=True)
    dangerous.sort(key=lambda x: x["line_ai_score"])
    ban_te.sort(key=lambda x: x["ban_expect_score"], reverse=True)

    return {
        "has_data": bool(race_reports),
        "lines_df": lines_df,
        "race_reports": race_reports,
        "advantageous": advantageous[:20],
        "dangerous": dangerous[:20],
        "solo_alerts": solo_alerts,
        "ban_te": ban_te[:30],
        "lines": build_line_analysis_lines(race_reports),
    }
