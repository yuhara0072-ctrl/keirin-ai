"""改善提案AI — 検証レポートから次の改善案を自動生成"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from analyze import analyze_by_popularity, analyze_by_style, analyze_by_venue
from config import DATA_DIR, TARGET_RACES
from data_quality import get_quality_bundle
from db import db_session, get_connection
from learning import load_learned_patterns

IMPROVEMENT_DIR = DATA_DIR / "improvement"
IMPROVEMENT_DIR.mkdir(parents=True, exist_ok=True)

MIN_RACES_WEAK = 2
MIN_RACES_STRONG = 2
RECOVERY_WEAK = 75.0
RECOVERY_STRONG = 100.0
RECOVERY_AVOID = 60.0
VERIFY_MAX_RACES = 4

IMPROVEMENT_TABLE = """
CREATE TABLE IF NOT EXISTS improvement_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    bet_type        TEXT NOT NULL,
    proposal_count  INTEGER NOT NULL DEFAULT 0,
    report_path     TEXT,
    status          TEXT NOT NULL DEFAULT 'ok',
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def migrate_improvement_table(conn) -> None:
    conn.executescript(IMPROVEMENT_TABLE)


def _condition_row(
    category: str,
    label: str,
    *,
    recovery: float,
    races: int = 0,
    hit_rate: float = 0.0,
    reason: str = "",
    priority: int = 50,
) -> dict:
    return {
        "category": category,
        "条件": label,
        "レース数": int(races),
        "回収率": round(float(recovery), 1),
        "的中率": round(float(hit_rate), 1) if hit_rate else None,
        "理由": reason,
        "優先度": int(priority),
    }


def _collect_market_conditions(bet_type: str) -> list[dict]:
    rows: list[dict] = []
    for label, df in (
        ("venue", analyze_by_venue(bet_type)),
        ("style", analyze_by_style(bet_type)),
        ("popularity", analyze_by_popularity(bet_type)),
    ):
        if df.empty:
            continue
        for r in df.itertuples():
            name = getattr(r, "venue_name", None) or getattr(r, "first_style", None) or getattr(
                r, "popularity_label", None
            )
            if not name:
                continue
            prefix = {"venue": "競輪場", "style": "脚質", "popularity": "人気帯"}.get(label, label)
            rows.append(
                {
                    "条件": f"{prefix}:{name}",
                    "レース数": int(r.races),
                    "回収率": float(r.recovery_rate),
                    "的中率": float(r.hit_rate),
                    "source": label,
                }
            )
    patterns = load_learned_patterns(bet_type)
    if not patterns.empty:
        for _, p in patterns.iterrows():
            rows.append(
                {
                    "条件": str(p["condition_label"]),
                    "レース数": int(p["races"]),
                    "回収率": float(p["recovery_rate"]),
                    "的中率": float(p["hit_rate"]),
                    "source": "learned",
                }
            )
    return rows


def extract_weak_conditions(
    validation: dict,
    market_rows: list[dict],
    advanced_patterns: pd.DataFrame,
) -> pd.DataFrame:
    items: list[dict] = []

    for _, row in validation.get("weak_conditions", pd.DataFrame()).iterrows():
        items.append(
            _condition_row(
                "weak",
                str(row["条件"]),
                recovery=float(row["回収率"]),
                races=int(row.get("レース数") or 0),
                hit_rate=float(row.get("的中率") or 0),
                reason="検証レポート: 低回収",
                priority=80,
            )
        )

    for row in market_rows:
        if row["レース数"] >= MIN_RACES_WEAK and row["回収率"] <= RECOVERY_WEAK:
            items.append(
                _condition_row(
                    "weak",
                    row["条件"],
                    recovery=row["回収率"],
                    races=row["レース数"],
                    hit_rate=row.get("的中率", 0),
                    reason="市場データ: 回収75%以下",
                    priority=70,
                )
            )

    if not advanced_patterns.empty and "excluded" in advanced_patterns.columns:
        ex = advanced_patterns[advanced_patterns["excluded"].astype(bool)]
        for _, p in ex.head(10).iterrows():
            items.append(
                _condition_row(
                    "weak",
                    str(p["condition_label"]),
                    recovery=float(p["recovery_rate"]),
                    races=int(p["races"]),
                    hit_rate=float(p["hit_rate"]),
                    reason="本格学習: 除外条件",
                    priority=85,
                )
            )

    week = validation.get("week", {}).get("by_ai_score", pd.DataFrame())
    if not week.empty:
        low = week[week["回収率"].fillna(999) < 70]
        for _, r in low.iterrows():
            items.append(
                _condition_row(
                    "weak",
                    f"AIスコア帯:{r['区分']}",
                    recovery=float(r["回収率"]),
                    races=int(r["件数"]),
                    hit_rate=float(r.get("的中率") or 0),
                    reason="今週の実績が低い",
                    priority=75,
                )
            )

    if not items:
        return pd.DataFrame()
    df = pd.DataFrame(items).drop_duplicates(subset=["条件"], keep="first")
    return df.sort_values(["優先度", "回収率"], ascending=[False, True]).head(15)


def extract_strong_conditions(
    validation: dict,
    market_rows: list[dict],
) -> pd.DataFrame:
    items: list[dict] = []

    for _, row in validation.get("strong_conditions", pd.DataFrame()).iterrows():
        items.append(
            _condition_row(
                "strong",
                str(row["条件"]),
                recovery=float(row["回収率"]),
                races=int(row.get("レース数") or 0),
                hit_rate=float(row.get("的中率") or 0),
                reason="検証レポート: 高回収",
                priority=80,
            )
        )

    for row in market_rows:
        if row["レース数"] >= MIN_RACES_STRONG and row["回収率"] >= RECOVERY_STRONG:
            items.append(
                _condition_row(
                    "strong",
                    row["条件"],
                    recovery=row["回収率"],
                    races=row["レース数"],
                    hit_rate=row.get("的中率", 0),
                    reason="市場データ: 回収100%以上",
                    priority=70,
                )
            )

    month = validation.get("month", {}).get("by_rank", pd.DataFrame())
    if not month.empty:
        high = month[month["回収率"].fillna(0) >= 100]
        for _, r in high.iterrows():
            items.append(
                _condition_row(
                    "strong",
                    f"ランク:{r['区分']}",
                    recovery=float(r["回収率"]),
                    races=int(r["件数"]),
                    hit_rate=float(r.get("的中率") or 0),
                    reason="今月の実績が好調",
                    priority=72,
                )
            )

    if not items:
        return pd.DataFrame()
    df = pd.DataFrame(items).drop_duplicates(subset=["条件"], keep="first")
    return df.sort_values(["優先度", "回収率"], ascending=[False, False]).head(15)


def extract_avoid_conditions(weak: pd.DataFrame) -> pd.DataFrame:
    if weak.empty:
        return pd.DataFrame()
    avoid = weak[weak["回収率"] <= RECOVERY_AVOID].copy()
    if avoid.empty:
        avoid = weak.head(5).copy()
    avoid["category"] = "avoid"
    avoid["理由"] = avoid["理由"].astype(str) + " → 購入回避推奨"
    return avoid.head(10)


def extract_verify_conditions(market_rows: list[dict]) -> pd.DataFrame:
    """サンプル不足だが有望/要確認の条件"""
    items: list[dict] = []
    for row in market_rows:
        n = row["レース数"]
        rec = row["回収率"]
        if n <= 0 or n > VERIFY_MAX_RACES:
            continue
        if rec >= 90 or rec <= 50:
            items.append(
                _condition_row(
                    "verify",
                    row["条件"],
                    recovery=rec,
                    races=n,
                    hit_rate=row.get("的中率", 0),
                    reason=f"サンプル{n}件 — 追加検証が必要",
                    priority=60 if rec >= 90 else 55,
                )
            )
    if not items:
        return pd.DataFrame()
    return pd.DataFrame(items).sort_values("回収率", ascending=False).head(10)


def build_score_proposals(validation: dict) -> list[dict]:
    proposals: list[dict] = []
    week = validation.get("week", {}).get("by_ai_score", pd.DataFrame())
    month = validation.get("month", {}).get("by_ai_score", pd.DataFrame())
    src = week if not week.empty else month

    if src.empty:
        proposals.append(
            {
                "提案": "AIスコア65点以上のみ「買い候補」に（データ不足の既定値）",
                "根拠": "実績データが少ないため保守的閾値を推奨",
                "優先度": 50,
            }
        )
        return proposals

    for _, r in src.iterrows():
        band = str(r["区分"])
        rec = float(r.get("回収率") or 0)
        if rec < 70:
            proposals.append(
                {
                    "提案": f"AIスコア帯「{band}」の購入を抑制（閾値引き上げ）",
                    "根拠": f"回収率{rec:.0f}% — この帯は期待値が低い",
                    "優先度": 85,
                }
            )
        elif rec >= 110:
            proposals.append(
                {
                    "提案": f"AIスコア帯「{band}」を優先候補に（加点維持）",
                    "根拠": f"回収率{rec:.0f}% — 強み帯として活用",
                    "優先度": 80,
                }
            )

    tv = validation.get("today_virtual", {}).get("summary", {})
    ta = validation.get("today", {}).get("summary", {})
    if tv.get("settled", 0) >= 2 and tv.get("recovery_rate", 0) > ta.get("recovery_rate", 0) + 20:
        proposals.append(
            {
                "提案": "実戦判定の「買い候補」閾値を+3点厳格化",
                "根拠": "見送り候補の仮想成績の方が良い",
                "優先度": 78,
            }
        )

    if not proposals:
        proposals.append(
            {
                "提案": "現行AIスコア閾値を維持",
                "根拠": "スコア帯別に大きな偏りなし",
                "優先度": 40,
            }
        )
    return proposals


def build_bankroll_proposals(validation: dict, bankroll_plan: dict) -> list[dict]:
    proposals: list[dict] = []
    month_amt = validation.get("month", {}).get("by_amount", pd.DataFrame())

    if not month_amt.empty:
        heavy = month_amt[month_amt["区分"].astype(str).str.contains("300", na=False)]
        if not heavy.empty and float(heavy.iloc[0].get("回収率") or 0) < 80:
            proposals.append(
                {
                    "提案": "Sランク以外は1レース200円上限に引き下げ",
                    "根拠": f"300円帯回収{heavy.iloc[0]['回収率']}%",
                    "優先度": 82,
                }
            )
        light = month_amt[month_amt["区分"].astype(str).str.contains("100", na=False)]
        if not light.empty and float(light.iloc[0].get("回収率") or 0) >= 100:
            proposals.append(
                {
                    "提案": "100円帯は維持 — 少額分散が機能",
                    "根拠": f"100円帯回収{light.iloc[0]['回収率']}%",
                    "優先度": 65,
                }
            )

    streaks = validation.get("streaks") or {}
    if streaks.get("lose_streak", 0) >= 3:
        proposals.append(
            {
                "提案": "連敗中は1日上限を50%に（自動減額を継続）",
                "根拠": f"{streaks['lose_streak']}連敗",
                "優先度": 90,
            }
        )

    if streaks.get("win_streak", 0) >= 3:
        proposals.append(
            {
                "提案": "連勝中も増額上限+15%を守る（現行ルール維持）",
                "根拠": f"{streaks['win_streak']}連勝 — 急増リスク回避",
                "優先度": 70,
            }
        )

    daily = bankroll_plan.get("max_daily", 1500)
    rec_total = bankroll_plan.get("recommended_total", 0)
    if rec_total > daily * 0.8:
        proposals.append(
            {
                "提案": "推奨合計が日次上限に近い — 候補レース数を絞る",
                "根拠": f"推奨{rec_total}円 / 上限{daily}円",
                "優先度": 75,
            }
        )

    if not proposals:
        proposals.append(
            {
                "提案": "現行の資金配分（S300/A200/B100）を維持",
                "根拠": "金額帯別に大きな問題なし",
                "優先度": 40,
            }
        )
    return proposals


def build_data_proposals(quality: dict, validation: dict) -> list[dict]:
    proposals: list[dict] = []
    total = quality.get("total_races", 0)
    valid = quality.get("valid_races", 0)
    remaining = max(0, TARGET_RACES - valid)

    if remaining > 0:
        proposals.append(
            {
                "提案": f"結果付きレースをあと{remaining}件収集（目標{TARGET_RACES}）",
                "根拠": f"現在有効{valid}件 — 学習精度向上に必要",
                "優先度": 88 if remaining > 50 else 70,
            }
        )

    if quality.get("quality_valid_pct", 100) < 80:
        proposals.append(
            {
                "提案": "データ品質タブで欠損レースを再取得",
                "根拠": f"有効率{quality.get('quality_valid_pct')}%",
                "優先度": 85,
            }
        )

    if validation.get("summary_all_actual", {}).get("settled", 0) < 10:
        proposals.append(
            {
                "提案": "収支検証タブで実購入/仮想記録を増やす",
                "根拠": "検証サンプルが10件未満",
                "優先度": 80,
            }
        )

    venue_df = analyze_by_venue()
    if not venue_df.empty:
        low_n = venue_df[venue_df["races"] <= 2]
        if not low_n.empty:
            v = low_n.iloc[0]["venue_name"]
            proposals.append(
                {
                    "提案": f"競輪場「{v}」のレースを追加収集",
                    "根拠": "場別サンプルが2件以下",
                    "優先度": 65,
                }
            )

    if not proposals:
        proposals.append(
            {
                "提案": "現状のデータ量で継続検証",
                "根拠": "急ぎの収集必要なし",
                "優先度": 35,
            }
        )
    return proposals


def build_hypotheses(
    weak: pd.DataFrame,
    strong: pd.DataFrame,
    verify: pd.DataFrame,
) -> list[dict]:
    hypos: list[dict] = []

    if not strong.empty:
        top = strong.iloc[0]
        hypos.append(
            {
                "仮説": f"「{top['条件']}」を買い候補に含めると回収UP",
                "検証方法": "該当条件のみで10レース追跡",
                "期待": f"回収{top['回収率']}%前後を維持できるか",
            }
        )

    if not weak.empty:
        top = weak.iloc[0]
        hypos.append(
            {
                "仮説": f"「{top['条件']}」を除外すると損失減",
                "検証方法": "除外条件で仮想成績を比較",
                "期待": "回収率が75%以上に改善するか",
            }
        )

    if not verify.empty:
        v = verify.iloc[0]
        hypos.append(
            {
                "仮説": f"「{v['条件']}」はサンプル追加で有効条件になる可能性",
                "検証方法": f"あと{VERIFY_MAX_RACES - v['レース数']}件以上データ収集",
                "期待": f"回収{v['回収率']}%が再現するか",
            }
        )

    hypos.append(
        {
            "仮説": "AIスコア閾値を65→68に上げると実購入回収が改善",
            "検証方法": "2週間A/B比較（仮想成績）",
            "期待": "見送り増でも実回収+10%",
        }
    )
    hypos.append(
        {
            "仮説": "危険人気レースを完全0円にすると資金効率UP",
            "検証方法": "危険人気の仮想 vs 実績比較",
            "期待": "ドローダウン縮小",
        }
    )
    return hypos[:6]


def _rank_top5(*proposal_lists: list[dict]) -> pd.DataFrame:
    all_p: list[dict] = []
    for lst in proposal_lists:
        for p in lst:
            all_p.append(
                {
                    "改善案": p.get("提案", ""),
                    "根拠": p.get("根拠", ""),
                    "優先度": int(p.get("優先度", 50)),
                    "種別": p.get("種別", "総合"),
                }
            )
    if not all_p:
        return pd.DataFrame()
    df = pd.DataFrame(all_p).sort_values("優先度", ascending=False)
    return df.drop_duplicates(subset=["改善案"], keep="first").head(5)


def build_improvement_proposals(
    bet_type: str = "3連単",
    *,
    validation: Optional[dict] = None,
    bankroll_plan: Optional[dict] = None,
    quality: Optional[dict] = None,
    advanced: Optional[dict] = None,
) -> dict:
    """改善提案バンドル"""
    if validation is None:
        from validation_report import build_validation_report

        validation = build_validation_report(bet_type, sync_virtual=False)
    if bankroll_plan is None:
        from bankroll import get_bankroll_bundle

        bankroll_plan = get_bankroll_bundle(bet_type)
    if quality is None:
        quality = get_quality_bundle(bet_type, refresh=False)
    if advanced is None:
        from advanced_learning import get_advanced_learning_bundle

        advanced = get_advanced_learning_bundle(bet_type, retrain=False)

    market_rows = _collect_market_conditions(bet_type)
    adv_patterns = advanced.get("patterns", pd.DataFrame())

    weak = extract_weak_conditions(validation, market_rows, adv_patterns)
    strong = extract_strong_conditions(validation, market_rows)
    avoid = extract_avoid_conditions(weak)
    verify = extract_verify_conditions(market_rows)

    score_props = build_score_proposals(validation)
    bank_props = build_bankroll_proposals(validation, bankroll_plan)
    data_props = build_data_proposals(quality, validation)

    for p in score_props:
        p["種別"] = "AIスコア"
    for p in bank_props:
        p["種別"] = "資金配分"
    for p in data_props:
        p["種別"] = "データ収集"

    top5 = _rank_top5(score_props, bank_props, data_props)
    hypotheses = build_hypotheses(weak, strong, verify)

    weaknesses = []
    if not weak.empty:
        weaknesses = [
            f"{row['条件']}（回収{row['回収率']}%）"
            for _, row in weak.head(5).iterrows()
        ]
    else:
        weaknesses = ["明確な弱点条件は未検出 — データ追加で精度向上"]

    strengths = []
    if not strong.empty:
        strengths = [
            f"{row['条件']}（回収{row['回収率']}%）"
            for _, row in strong.head(5).iterrows()
        ]
    else:
        strengths = ["強み条件はサンプル不足 — 100レース収集後に再評価"]

    return {
        "bet_type": bet_type,
        "ref_date": validation.get("ref_date", ""),
        "has_data": validation.get("has_data", False),
        "weaknesses": weaknesses,
        "strengths": strengths,
        "weak_conditions": weak,
        "strong_conditions": strong,
        "avoid_conditions": avoid,
        "verify_conditions": verify,
        "score_proposals": score_props,
        "bankroll_proposals": bank_props,
        "data_proposals": data_props,
        "top5_proposals": top5,
        "hypotheses": hypotheses,
        "quality_valid_pct": quality.get("quality_valid_pct", quality.get("valid_pct", 0)),
    }


def save_improvement_report(bet_type: str = "3連単", bundle: Optional[dict] = None) -> Path:
    b = bundle or build_improvement_proposals(bet_type)
    lines = build_improvement_lines(b)
    text = "\n".join(lines)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = IMPROVEMENT_DIR / f"improvement_{ts}.txt"
    path.write_text(text, encoding="utf-8")
    (IMPROVEMENT_DIR / "improvement_latest.txt").write_text(text, encoding="utf-8")

    with db_session() as conn:
        migrate_improvement_table(conn)
        conn.execute(
            """
            INSERT INTO improvement_runs (started_at, bet_type, proposal_count, report_path, status)
            VALUES (?, ?, ?, ?, 'ok')
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                bet_type,
                len(b.get("top5_proposals", [])),
                str(path),
            ),
        )
    return path


def get_improvement_bundle(
    bet_type: str = "3連単",
    *,
    validation: Optional[dict] = None,
    bankroll_plan: Optional[dict] = None,
    quality: Optional[dict] = None,
    advanced: Optional[dict] = None,
) -> dict:
    bundle = build_improvement_proposals(
        bet_type,
        validation=validation,
        bankroll_plan=bankroll_plan,
        quality=quality,
        advanced=advanced,
    )
    bundle["lines"] = build_improvement_lines(bundle)
    return bundle


def build_improvement_lines(bundle: Optional[dict] = None, bet_type: str = "3連単") -> list[str]:
    b = bundle or build_improvement_proposals(bet_type)
    lines = [f"【改善提案AI】券種={bet_type}  基準日={b.get('ref_date', '')}", ""]

    lines.append("--- 今のAIの弱点 ---")
    for w in b.get("weaknesses") or []:
        lines.append(f"  * {w}")
    lines.append("")

    lines.append("--- 強み ---")
    for s in b.get("strengths") or []:
        lines.append(f"  + {s}")
    lines.append("")

    lines.append("--- 改善案 TOP5 ---")
    top5 = b.get("top5_proposals", pd.DataFrame())
    if top5.empty:
        lines.append("  （なし）")
    else:
        for i, row in top5.iterrows():
            lines.append(f"  {row['改善案']} - {row['根拠']}")
    lines.append("")

    lines.append("--- 買い控えるべき条件 ---")
    avoid = b.get("avoid_conditions", pd.DataFrame())
    if avoid.empty:
        lines.append("  （なし）")
    else:
        for _, row in avoid.head(5).iterrows():
            lines.append(f"  x {row['条件']} ({row['回収率']}%)")
    lines.append("")

    lines.append("--- 次に検証する仮説 ---")
    for h in b.get("hypotheses") or []:
        lines.append(f"  ? {h['仮説']}")
        lines.append(f"    方法: {h['検証方法']}")
    lines.append("")
    return lines
