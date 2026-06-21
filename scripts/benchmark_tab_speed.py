"""全タブ・bundle の冷起動時間を計測（Streamlit 不要）"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BET_TYPE = "3連単"
SLOW_THRESHOLD_SEC = 3.0


@dataclass
class TimingRow:
    key: str
    label: str
    category: str
    seconds: float
    error: str | None = None


def _mtime() -> float:
    from config import DB_PATH

    try:
        return float(DB_PATH.stat().st_mtime) if DB_PATH.exists() else 0.0
    except OSError:
        return 0.0


def _time_call(key: str, label: str, category: str, fn) -> TimingRow:
    t0 = time.perf_counter()
    try:
        fn()
        sec = time.perf_counter() - t0
        return TimingRow(key, label, category, round(sec, 3))
    except Exception as exc:
        sec = time.perf_counter() - t0
        return TimingRow(key, label, category, round(sec, 3), str(exc))


def _tab_loaders(bet_type: str, mtime: float) -> dict[str, tuple[str, callable]]:
    from ai_insights import get_ai_insights_bundle
    from ai_recommend import get_ai_recommend_bundle
    from ai_score import get_ai_score_bundle
    from advanced_learning import get_advanced_learning_bundle
    from backup import get_backup_bundle
    from bankroll import get_bankroll_bundle
    from battle_judge import get_battle_judge_bundle
    from bet_tracker import get_pnl_bundle
    from bulk_collect import get_collect_bundle
    from charts import get_charts_bundle
    from data_quality import get_quality_bundle
    from data_quality import get_quality_bundle
    from improvement_ai import get_improvement_bundle
    from learning import get_learning_bundle
    from line_analysis import get_line_analysis_bundle
    from market_monitor import get_market_monitor_bundle
    from ml_model import get_ml_bundle
    from ops import get_ops_status
    from pre_race import get_pre_race_bundle
    from system_check import get_system_check_bundle
    from validation_report import get_validation_bundle

    def load_battle_bundle():
        with ThreadPoolExecutor(max_workers=6) as pool:
            f_score = pool.submit(get_ai_score_bundle, bet_type)
            f_market = pool.submit(get_market_monitor_bundle, bet_type)
            f_line = pool.submit(
                lambda: get_line_analysis_bundle(fetch_missing=False)
            )
            f_pre = pool.submit(get_pre_race_bundle, bet_type)
            f_quality = pool.submit(get_quality_bundle, bet_type, refresh=False)
            f_advanced = pool.submit(
                get_advanced_learning_bundle, bet_type, retrain=False
            )
            score_bundle = f_score.result()
            f_ml = pool.submit(
                lambda sb=score_bundle: get_ml_bundle(
                    bet_type, sb["scores"], retrain=False
                )
            )
            return get_battle_judge_bundle(
                bet_type,
                scores=score_bundle["scores"],
                market=f_market.result(),
                line=f_line.result(),
                pre_race=f_pre.result(),
                ml=f_ml.result(),
                quality=f_quality.result(),
                advanced=f_advanced.result(),
            )

    def battle_full():
        load_battle_bundle()

    def validation_full():
        battle = load_battle_bundle()
        bankroll = get_bankroll_bundle(bet_type, battle_bundle=battle)
        get_validation_bundle(
            bet_type,
            battle_bundle=battle,
            bankroll_plan=bankroll,
            sync_virtual=False,
        )

    def improve_full():
        battle = load_battle_bundle()
        bankroll = get_bankroll_bundle(bet_type, battle_bundle=battle)
        validation = get_validation_bundle(
            bet_type,
            battle_bundle=battle,
            bankroll_plan=bankroll,
            sync_virtual=False,
        )
        get_improvement_bundle(
            bet_type,
            validation=validation,
            bankroll_plan=bankroll,
            quality=get_quality_bundle(bet_type, refresh=False),
            advanced=get_advanced_learning_bundle(bet_type, retrain=False),
        )

    return {
        "home": ("🏠 ホーム", lambda: __import__("db", fromlist=["get_db_counts_fast"]).get_db_counts_fast()),
        "rec": (
            "⭐ 今日のAIおすすめ",
            lambda: (
                get_ai_recommend_bundle(
                    bet_type,
                    scores=get_ai_score_bundle(bet_type)["scores"],
                )
            ),
        ),
        "battle": ("🎯 実戦判定", battle_full),
        "line": ("🔗 ライン分析", lambda: get_line_analysis_bundle(fetch_missing=False)),
        "predict_ai": (
            "📊 AI指標",
            lambda: get_ai_insights_bundle(bet_type, fetch_missing=False, include_lines=True),
        ),
        "predict_ml": (
            "🤖 ML予測",
            lambda: get_ml_bundle(
                bet_type,
                scores=get_ai_score_bundle(bet_type)["scores"],
                retrain=False,
            ),
        ),
        "predict_prerace": ("⏱ 直前分析", lambda: get_pre_race_bundle(bet_type)),
        "predict_chart": (
            "📈 グラフ",
            lambda: get_charts_bundle(
                bet_type,
                min_score=70,
                scores=get_ai_score_bundle(bet_type)["scores"],
            ),
        ),
        "market": ("📡 市場監視", lambda: get_market_monitor_bundle(bet_type)),
        "bankroll": (
            "💰 資金管理",
            lambda: get_bankroll_bundle(
                bet_type,
                battle_bundle=load_battle_bundle(),
            ),
        ),
        "validation": ("📊 検証レポート", validation_full),
        "improve": ("💡 改善提案", improve_full),
        "pnl": (
            "📈 収支検証",
            lambda: get_pnl_bundle(
                bet_type,
                recommend=get_ai_recommend_bundle(
                    bet_type,
                    scores=get_ai_score_bundle(bet_type)["scores"],
                ),
                sync_virtual=False,
            ),
        ),
        "learn": ("🧠 パターン学習", lambda: get_learning_bundle(bet_type, refresh=False)),
        "advanced": ("🎓 本格学習", lambda: get_advanced_learning_bundle(bet_type, retrain=False)),
        "quality": ("📋 データ品質", lambda: get_quality_bundle(bet_type, refresh=False)),
        "collect": ("📥 100レース収集", lambda: get_collect_bundle(100)),
        "backup": ("💾 バックアップ", lambda: get_backup_bundle()),
        "check": (
            "🔧 システムチェック",
            lambda: get_system_check_bundle(
                bet_type,
                deep=False,
                quality=get_quality_bundle(bet_type, refresh=False),
                score_bundle=get_ai_score_bundle(bet_type),
                learning_bundle=get_learning_bundle(bet_type, refresh=False),
                backup_bundle=get_backup_bundle(),
            ),
        ),
        "settings_ops": ("⚙ 自動運用", lambda: get_ops_status(bet_type, fast=True, targets_count=0)),
    }


def _bundle_loaders(bet_type: str) -> dict[str, tuple[str, callable]]:
    from advanced_learning import get_advanced_learning_bundle
    from ai_insights import get_ai_insights_bundle
    from ai_recommend import get_ai_recommend_bundle
    from ai_score import get_ai_score_bundle
    from backup import get_backup_bundle
    from battle_judge import get_battle_judge_bundle
    from charts import get_charts_bundle
    from data_quality import get_quality_bundle
    from learning import get_learning_bundle
    from line_analysis import get_line_analysis_bundle
    from market_monitor import get_market_monitor_bundle
    from ml_model import get_ml_bundle
    from pre_race import get_pre_race_bundle

    return {
        "bundle:ai_score": ("ai_score", lambda: get_ai_score_bundle(bet_type)),
        "bundle:line": (
            "line_analysis (no API)",
            lambda: get_line_analysis_bundle(fetch_missing=False),
        ),
        "bundle:battle": ("battle_judge (full deps)", lambda: get_battle_judge_bundle(bet_type)),
        "bundle:learning_refresh": (
            "learning refresh=True",
            lambda: get_learning_bundle(bet_type, refresh=True),
        ),
        "bundle:learning_no_refresh": (
            "learning refresh=False",
            lambda: get_learning_bundle(bet_type, refresh=False),
        ),
        "bundle:validation_sync": (
            "validation sync_virtual=False",
            lambda: __import__(
                "validation_report", fromlist=["get_validation_bundle"]
            ).get_validation_bundle(bet_type, sync_virtual=False),
        ),
        "bundle:market": ("market_monitor", lambda: get_market_monitor_bundle(bet_type)),
        "bundle:ml": (
            "ml_model",
            lambda: get_ml_bundle(
                bet_type,
                scores=get_ai_score_bundle(bet_type)["scores"],
                retrain=False,
            ),
        ),
        "bundle:charts": ("charts", lambda: get_charts_bundle(bet_type, min_score=70)),
        "bundle:quality": ("quality", lambda: get_quality_bundle(bet_type, refresh=False)),
        "bundle:advanced": (
            "advanced_learning",
            lambda: get_advanced_learning_bundle(bet_type, retrain=False),
        ),
        "bundle:pre_race": ("pre_race", lambda: get_pre_race_bundle(bet_type)),
        "bundle:ai_insights": ("ai_insights", lambda: get_ai_insights_bundle(bet_type)),
        "bundle:recommend": (
            "ai_recommend",
            lambda: get_ai_recommend_bundle(
                bet_type, scores=get_ai_score_bundle(bet_type)["scores"]
            ),
        ),
        "bundle:backup": ("backup", lambda: get_backup_bundle()),
    }


def run_benchmark() -> list[TimingRow]:
    from auth import ensure_db_schema_only

    ensure_db_schema_only()
    mtime = _mtime()
    rows: list[TimingRow] = []

    for key, (label, fn) in _tab_loaders(BET_TYPE, mtime).items():
        rows.append(_time_call(key, label, "tab", fn))

    for key, (label, fn) in _bundle_loaders(BET_TYPE).items():
        rows.append(_time_call(key, label, "bundle", fn))

    return rows


def format_report(rows: list[TimingRow], title: str) -> str:
    tabs = sorted([r for r in rows if r.category == "tab"], key=lambda x: x.seconds, reverse=True)
    slow = sorted([r for r in rows if r.seconds >= SLOW_THRESHOLD_SEC], key=lambda x: x.seconds, reverse=True)

    lines = [
        f"# {title}",
        "",
        f"計測条件: bet_type={BET_TYPE}, 閾値={SLOW_THRESHOLD_SEC}s, 冷起動（キャッシュなし）",
        "",
        "## タブ処理時間ランキング",
        "",
        "| 順位 | タブ | 秒 | 備考 |",
        "|---:|---|---:|---|",
    ]
    for i, r in enumerate(tabs, 1):
        note = r.error or ""
        lines.append(f"| {i} | {r.label} | {r.seconds:.3f} | {note} |")

    lines.extend(["", "## 3秒以上の処理", ""])
    if not slow:
        lines.append("（なし）")
    else:
        lines.append("| キー | 種別 | 秒 |")
        lines.append("|---|---|---:|")
        for r in slow:
            lines.append(f"| {r.key} | {r.category} | {r.seconds:.3f} |")

    return "\n".join(lines)


def compare_reports(before: list[TimingRow], after: list[TimingRow]) -> str:
    bmap = {r.key: r for r in before if r.category == "tab"}
    amap = {r.key: r for r in after if r.category == "tab"}
    lines = [
        "# 修正前後 タブ処理時間比較",
        "",
        "| タブ | 修正前(s) | 修正後(s) | 差分(s) | 改善率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in sorted(bmap.keys(), key=lambda k: bmap[k].seconds, reverse=True):
        b = bmap[key].seconds
        a = amap.get(key, TimingRow(key, "", "tab", 0.0)).seconds
        diff = round(b - a, 3)
        pct = round((diff / b * 100) if b > 0 else 0, 1)
        label = bmap[key].label
        lines.append(f"| {label} | {b:.3f} | {a:.3f} | {diff:+.3f} | {pct:.1f}% |")
    return "\n".join(lines)


def main() -> None:
    out_dir = ROOT / "scripts" / "benchmark_results"
    out_dir.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) >= 2 and sys.argv[1] == "compare":
        before_path = out_dir / (sys.argv[2] if len(sys.argv) > 2 else "baseline.json")
        if not before_path.suffix:
            before_path = out_dir / f"{sys.argv[2]}.json"
        after_path = out_dir / (sys.argv[3] if len(sys.argv) > 3 else "round2.json")
        if not after_path.suffix:
            after_path = out_dir / f"{sys.argv[3]}.json"
        before = [TimingRow(**r) for r in json.loads(before_path.read_text(encoding="utf-8"))]
        after = [TimingRow(**r) for r in json.loads(after_path.read_text(encoding="utf-8"))]
        text = compare_reports(before, after)
        compare_path = out_dir / "comparison.md"
        compare_path.write_text(text, encoding="utf-8")
        print(text.replace("🏠", "home").replace("🎯", ">>"))
        print(f"\nSaved: {compare_path}")
        return

    tag = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    rows = run_benchmark()
    json_path = out_dir / f"{tag}.json"
    md_path = out_dir / f"{tag}.md"
    json_path.write_text(
        json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = format_report(rows, f"Tab speed benchmark ({tag})")
    md_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved: {json_path}\nSaved: {md_path}")


if __name__ == "__main__":
    main()
