"""分析・異常検知レポートの生成と保存"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from analyze import (
    analyze_by_bet_type,
    analyze_by_odds_bucket,
    analyze_by_popularity,
    analyze_by_senko1,
    analyze_by_style,
    analyze_by_style_in_race,
    analyze_by_time_slot,
    analyze_by_venue,
    list_senko1_races,
    load_bet_frame,
)
from config import DATA_DIR
from db import get_connection
from detect_anomaly import detect_all
from ai_insights import build_ai_insights_lines
from ai_recommend import build_recommend_lines
from line_analysis import build_line_analysis_lines
from learning import build_learning_lines
from market_monitor import build_monitor_lines
from pre_race import build_pre_race_lines
from ai_score import build_ai_score_lines


def _df_block(title: str, df: pd.DataFrame, empty_msg: str = "") -> list[str]:
    lines = [f"--- {title} ---"]
    if df is None or df.empty:
        lines.append(empty_msg or "  （データなし）")
    else:
        lines.append(df.to_string(index=False))
    lines.append("")
    return lines


def db_summary_lines() -> list[str]:
    conn = get_connection()
    races = conn.execute("SELECT COUNT(*) FROM races").fetchone()[0]
    results = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    odds = conn.execute("SELECT COUNT(*) FROM odds").fetchone()[0]
    rows = conn.execute(
        """
        SELECT race_id, race_date, venue_name, race_no, race_start, time_slot
        FROM races ORDER BY race_date DESC, race_start, race_id
        LIMIT 30
        """
    ).fetchall()
    conn.close()

    lines = [
        "【DBサマリー】",
        f"  レース数: {races}  結果あり: {results}  オッズ行数: {odds}",
        "",
        "【登録レース一覧（最大30件）】",
    ]
    for r in rows:
        lines.append(
            f"  {r['race_id']} {r['race_date']} {r['venue_name']} "
            f"{r['race_no']}R {r['race_start'] or '-'} ({r['time_slot'] or '-'})"
        )
    lines.append("")
    return lines


def build_analyze_lines(bet_type: str = "3連単") -> list[str]:
    df = load_bet_frame(bet_type=bet_type)
    if df.empty:
        return [
            "【市場偏り分析】",
            "分析対象がありません。",
            "先に: python main.py daily --with-result",
            "",
        ]

    lines = [
        f"【市場偏り分析】券種={bet_type}（各組み合わせ100円ずつ購入想定）",
        f"対象レース数: {df['race_id'].nunique()}",
        "",
    ]
    lines.extend(_df_block("競輪場別回収率", analyze_by_venue(bet_type)))

    ts = analyze_by_time_slot(bet_type)
    lines.extend(
        _df_block(
            "時間帯別回収率",
            ts,
            "  fetch_daily で取得すると発走時刻が入ります",
        )
    )
    lines.extend(_df_block("脚質別回収率（1着目車番）", analyze_by_style(bet_type)))

    rst = analyze_by_style_in_race(bet_type)
    if not rst.empty:
        lines.extend(_df_block("脚質構成別回収率", rst))

    lines.extend(_df_block("人気別回収率", analyze_by_popularity(bet_type)))

    lines.append("--- 先行1車判定 ---")
    senko = list_senko1_races()
    if senko.empty:
        lines.append("  先行1車レース: 0件")
    else:
        for _, row in senko.iterrows():
            lines.append(
                f"  {row['race_id']} {row['venue_name']} "
                f"先行車番:{row['senko_brackets']}"
            )
    lines.extend(_df_block("先行1車 × 回収率", analyze_by_senko1(bet_type)))
    lines.extend(_df_block("オッズ帯別回収率", analyze_by_odds_bucket(bet_type)))
    lines.extend(_df_block("券種別回収率", analyze_by_bet_type()))
    return lines


def build_detect_lines(bet_type: str = "3連単") -> list[str]:
    df = detect_all(bet_type)
    lines = [f"【異常・オッズ歪み検知】券種={bet_type}", ""]
    if df.empty:
        lines.append("異常は検出されませんでした。")
        lines.append("")
        return lines

    lines.append(f"検出件数: {len(df)}")
    lines.append("")
    for atype in [
        "オッズ歪み",
        "回収率スパイク",
        "確率集中",
        "オッズ乖離",
        "極端高配当",
        "極端人気",
        "的中高配当",
    ]:
        part = df[df["anomaly_type"] == atype]
        if part.empty:
            continue
        lines.append(f"--- {atype} ({len(part)}件) ---")
        lines.append(part.head(15).to_string(index=False))
        if len(part) > 15:
            lines.append(f"  ... 他 {len(part) - 15} 件")
        lines.append("")
    return lines


def generate_full_report(bet_type: str = "3連単") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = [
        "=" * 60,
        "競輪観測AI レポート",
        f"作成日時: {now}",
        "=" * 60,
        "",
    ]
    body = []
    body.extend(db_summary_lines())
    body.extend(build_analyze_lines(bet_type))
    body.extend(build_ai_insights_lines(bet_type))
    body.extend(build_line_analysis_lines())
    body.extend(build_ai_score_lines(bet_type))
    body.extend(build_recommend_lines(bet_type))
    body.extend(build_learning_lines(bet_type))
    body.extend(build_pre_race_lines(bet_type))
    from ops import build_ops_lines

    body.extend(build_ops_lines(bet_type))
    from ml_model import build_ml_lines

    body.extend(build_ml_lines(bet_type))
    from notifications import build_notify_lines

    body.extend(build_notify_lines(bet_type))
    body.extend(build_monitor_lines(bet_type))
    body.extend(build_detect_lines(bet_type))
    body.append("=" * 60)
    body.append("レポート終了")
    return "\n".join(header + body)


def default_report_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / f"report_{stamp}.txt"


def save_report(
    output: Optional[str] = None,
    bet_type: str = "3連単",
) -> Path:
    text = generate_full_report(bet_type)
    path = Path(output) if output else default_report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    latest = DATA_DIR / "report_latest.txt"
    latest.write_text(text, encoding="utf-8")
    return path


if __name__ == "__main__":
    p = save_report()
    print(f"保存しました: {p}")
    print(f"最新版: {DATA_DIR / 'report_latest.txt'}")
