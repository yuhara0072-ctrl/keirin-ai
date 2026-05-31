"""競輪観測AI — エントリポイント"""

import argparse
import sys

import requests

from analyze import print_report as print_analyze_report
from config import DAILY_FETCH_LIMIT, DATA_DIR, TARGET_RACES
from db import init_db
from detect_anomaly import print_report as print_anomaly_report
from fetch_daily import fetch_daily
from fetch_entries import fetch_and_save as fetch_entries
from fetch_odds import fetch_and_save as fetch_odds
from fetch_results import fetch_and_save as fetch_results
from ai_insights import build_ai_insights_lines
from ai_recommend import build_recommend_lines
from learning import build_learning_lines, save_learned_patterns
from market_monitor import build_monitor_lines
from pre_race import build_pre_race_lines, poll_pre_race_due
from ops import build_ops_lines, run_daily_auto, run_full_ops
from ml_model import build_ml_lines, train_ml_model
from notifications import build_notify_lines, get_notification_bundle
from backup import build_backup_lines, create_backup, restore_backup
from bet_tracker import add_bets_from_cards, build_pnl_lines, get_pnl_bundle, settle_pending_bets, sync_virtual_bets
from bulk_collect import build_collect_lines, fetch_bulk, get_collect_bundle
from advanced_learning import build_advanced_learning_lines, get_advanced_learning_bundle, run_advanced_learning
from battle_judge import build_battle_judge_lines, get_battle_judge_bundle
from bankroll import build_bankroll_lines, get_bankroll_bundle
from validation_report import build_validation_lines, run_daily_validation, save_validation_report
from improvement_ai import build_improvement_lines, get_improvement_bundle, save_improvement_report
from system_check import build_system_check_lines, get_system_check_bundle, save_system_check_report
from data_quality import build_quality_lines, get_quality_bundle
from fetch_odds import list_race_ids_in_db, poll_odds_for_races
from ai_score import build_ai_score_lines
from report import save_report


def cmd_init(_: argparse.Namespace) -> None:
    init_db()
    print("DBを初期化しました。")


def cmd_fetch(args: argparse.Namespace) -> None:
    init_db()
    race_id = args.race_id
    print(f"[1/3] 出走表: {race_id}")
    n = fetch_entries(race_id)
    print(f"  → {n}名")

    print(f"[2/3] オッズ: {race_id}")
    n = fetch_odds(race_id)
    print(f"  → {n}件")

    if args.with_result:
        print(f"[3/3] 結果: {race_id}")
        res = fetch_results(race_id)
        print(f"  → 着順 {res.finish_order}")
    else:
        print("[3/3] 結果: スキップ（--with-result で取得）")


def cmd_daily(args: argparse.Namespace) -> None:
    fetch_daily(
        kaisai_date=args.date,
        limit=args.limit,
        with_result=args.with_result,
        venue_code=args.venue,
    )


def cmd_analyze(args: argparse.Namespace) -> None:
    print_analyze_report(args.bet_type)


def cmd_detect(args: argparse.Namespace) -> None:
    print_anomaly_report(args.bet_type)


def cmd_ai(args: argparse.Namespace) -> None:
    for line in build_ai_insights_lines(args.bet_type):
        print(line)


def cmd_score(args: argparse.Namespace) -> None:
    for line in build_ai_score_lines(args.bet_type):
        print(line)


def cmd_monitor(args: argparse.Namespace) -> None:
    if args.poll:
        init_db()
        ids = list_race_ids_in_db(args.limit)
        print(f"オッズ再取得: {len(ids)}レース")
        for r in poll_odds_for_races(ids):
            status = "OK" if r.get("ok") else r.get("error")
            print(f"  {r['race_id']}: {status}")
        print()
    for line in build_monitor_lines(args.bet_type):
        print(line)


def cmd_learn(args: argparse.Namespace) -> None:
    init_db()
    n = save_learned_patterns(args.bet_type)
    print(f"学習条件を {n} 件保存しました（券種={args.bet_type}）")
    for line in build_learning_lines(args.bet_type):
        print(line)


def cmd_pre_race(args: argparse.Namespace) -> None:
    init_db()
    if args.poll:
        results = poll_pre_race_due(within_hours=args.within_hours)
        print(f"直前スナップショット: {len(results)} 件処理")
        for r in results:
            if r.get("ok"):
                print(f"  {r['race_id']} {r['phase']}: OK ({r.get('count')}件)")
            else:
                print(f"  {r.get('race_id')} {r.get('phase')}: {r.get('error')}")
        print()
    for line in build_pre_race_lines(args.bet_type):
        print(line)


def cmd_pnl(args: argparse.Namespace) -> None:
    init_db()
    from ai_recommend import build_daily_recommendations
    from ai_score import build_race_scores

    scores = build_race_scores(args.bet_type)
    rec = build_daily_recommendations(args.bet_type, scores=scores)
    if args.sync_virtual:
        n = sync_virtual_bets(rec, args.bet_type, bet_amount=args.amount)
        print(f"仮想同期: {n} 件")
    if args.record_targets:
        n = add_bets_from_cards(rec.get("targets") or [], args.bet_type, args.amount)
        print(f"狙い目記録: {n} 件")
    n = settle_pending_bets(args.bet_type)
    print(f"確定: {n} 件")
    for line in build_pnl_lines(args.bet_type):
        print(line)


def cmd_backup(args: argparse.Namespace) -> None:
    init_db()
    if args.restore:
        result = restore_backup(
            args.restore,
            restore_db=not args.skip_db,
            restore_settings=not args.skip_settings,
            restore_reports=not args.skip_reports,
            restore_models=not args.skip_models,
        )
        if not result["ok"]:
            print(f"エラー: {result.get('error')}", file=sys.stderr)
            raise SystemExit(1)
        for line in result.get("log", []):
            print(line)
        return
    if args.list_only:
        for line in build_backup_lines():
            print(line)
        return
    result = create_backup(note=args.note or "")
    print(f"バックアップ完了: {result['backup_id']}")
    print(f"  保存先: {result['path']}")
    m = result["manifest"]
    print(f"  DB: {m.get('db_size_bytes', 0)} bytes  レポート: {m.get('report_count', 0)} 件")


def cmd_notify(args: argparse.Namespace) -> None:
    init_db()
    from ai_recommend import build_daily_recommendations
    from ai_score import build_race_scores

    scores = build_race_scores(args.bet_type)
    rec = build_daily_recommendations(args.bet_type, scores=scores)
    bundle = get_notification_bundle(
        args.bet_type,
        scores=scores,
        recommend=rec,
        persist=True,
    )
    print(f"本日候補: {bundle['candidate_count']} 件（新規記録 {bundle['saved_count']} 件）")
    for line in build_notify_lines(args.bet_type):
        print(line)


def cmd_ml(args: argparse.Namespace) -> None:
    init_db()
    if args.train:
        from ai_score import build_race_scores

        scores = build_race_scores(args.bet_type) if not args.skip_scores else None
        result = train_ml_model(args.bet_type, scores=scores)
        if not result.get("ok"):
            print(f"エラー: {result.get('error')}", file=sys.stderr)
            raise SystemExit(1)
        print(f"学習完了: {result['n_train']} レース")
        print(f"  CV R2={result['cv_r2']:.3f}  RMSE={result['cv_rmse']:.2f}")
        print(f"  モデル: {result['model_path']}")
        print()
    for line in build_ml_lines(args.bet_type):
        print(line)


def cmd_ops(args: argparse.Namespace) -> None:
    init_db()
    if args.run_now:
        if args.date:
            result = run_full_ops(
                kaisai_date=args.date,
                limit=args.limit,
                with_result=args.with_result,
                venue_code=args.venue,
                bet_type=args.bet_type,
                trigger="manual",
                skip_fetch=args.skip_fetch,
            )
        else:
            result = run_daily_auto(
                args.bet_type,
                limit=args.limit,
                with_result=args.with_result,
                venue_code=args.venue,
                trigger="manual",
            )
        print(result.get("log_text", ""))
        tr = result.get("today_results") or {}
        if tr:
            print("")
            print("--- 今日見るべき結果 ---")
            print(f"  取得: {tr.get('races_fetched', 0)} / "
                  f"おすすめ: {tr.get('targets_count', 0)} / "
                  f"危険人気: {tr.get('danger_count', 0)} / "
                  f"通知: {tr.get('notify_count', 0)}")
        if not result["ok"]:
            raise SystemExit(1)
        return
    if args.daemon:
        from ops import set_ops_config, start_scheduler_thread

        set_ops_config("schedule_hour", str(args.schedule_hour))
        set_ops_config("schedule_minute", str(args.schedule_minute))
        set_ops_config("auto_enabled", "1")
        print(f"自動運用デーモン起動（毎朝 {args.schedule_hour:02d}:{args.schedule_minute:02d}）")
        print("Ctrl+C で停止")
        start_scheduler_thread(args.bet_type)
        import time

        while True:
            time.sleep(3600)
    for line in build_ops_lines(args.bet_type):
        print(line)


def cmd_validate(args: argparse.Namespace) -> None:
    init_db()
    if args.save:
        path = save_validation_report(args.bet_type)
        print(f"保存: {path}")
    else:
        result = run_daily_validation(args.bet_type)
        print(f"検証完了: {result.get('report_path')}")
    for line in build_validation_lines(bet_type=args.bet_type):
        print(line)


def cmd_improve(args: argparse.Namespace) -> None:
    init_db()
    if args.save:
        path = save_improvement_report(args.bet_type)
        print(f"保存: {path}")
    else:
        bundle = get_improvement_bundle(args.bet_type)
        print(f"改善提案: TOP{len(bundle.get('top5_proposals', []))}件")
    for line in build_improvement_lines(bet_type=args.bet_type):
        safe = line.replace("\u2014", "-").replace("\u2013", "-")
        print(safe)


def cmd_check(args: argparse.Namespace) -> None:
    init_db()
    bundle = get_system_check_bundle(args.bet_type, deep=args.deep)
    if args.save:
        path = save_system_check_report(args.bet_type, bundle=bundle)
        print(f"保存: {path}")
    s = bundle.get("summary", {})
    print(
        f"完成チェック: {bundle.get('overall_label')} "
        f"(正常{s.get('ok', 0)} / 注意{s.get('warn', 0)} / エラー{s.get('error', 0)})"
    )
    for line in build_system_check_lines(bundle):
        safe = line.replace("\u2014", "-").replace("\u2013", "-")
        print(safe)


def cmd_bankroll(args: argparse.Namespace) -> None:
    init_db()
    settle_pending_bets(args.bet_type)
    for line in build_bankroll_lines(bet_type=args.bet_type):
        print(line)


def cmd_battle(args: argparse.Namespace) -> None:
    init_db()
    for line in build_battle_judge_lines(bet_type=args.bet_type):
        print(line)


def cmd_advanced_learn(args: argparse.Namespace) -> None:
    init_db()
    if args.retrain:
        result = run_advanced_learning(args.bet_type)
        if not result.get("ok"):
            print(f"エラー: {result.get('error')}", file=sys.stderr)
            raise SystemExit(1)
        print(
            f"本格学習完了: {result['n_valid_races']}R / "
            f"前{result['before_recovery']}% → 後{result['after_predicted_recovery']}%"
        )
        print(f"モデル: {result['model_path']}")
    for line in build_advanced_learning_lines(args.bet_type):
        print(line)


def cmd_quality(args: argparse.Namespace) -> None:
    init_db()
    for line in build_quality_lines(args.bet_type):
        print(line)


def cmd_collect(args: argparse.Namespace) -> None:
    init_db()
    bundle = get_collect_bundle(args.target)
    print(f"保存(結果あり): {bundle['saved_races']} / 目標 {bundle['target_races']}")
    print(f"あと {bundle['remaining_to_target']} レース")
    print()

    def on_progress(p: dict) -> None:
        print(
            f"  [{p['day_idx']}/{p['total_days']}] {p['message']} | "
            f"new={p['fetched_new']} skip={p['skipped_dup']} err={p['error_count']} "
            f"saved={p['saved_races']}"
        )

    result = fetch_bulk(
        args.start,
        args.end,
        per_day_limit=args.limit,
        with_result=args.with_result,
        venue_code=args.venue,
        target_races=args.target,
        skip_existing=not args.no_skip,
        run_post=not args.no_post,
        bet_type=args.bet_type,
        progress_callback=on_progress if not args.quiet else None,
    )
    print()
    print(
        f"完了: 新規{result['fetched_new']} / "
        f"スキップ{result['skipped_dup']} / エラー{result['error_count']}"
    )
    print(f"保存: {result['saved_races']}R / あと{result['remaining_to_target']}件")
    print(f"ログ: {result['log_path']}")
    if result.get("post_result"):
        pr = result["post_result"]
        print(f"学習: {pr.get('learning_count')} 件 / レポート: {pr.get('report_path')}")
    for line in build_collect_lines():
        print(line)


def cmd_report(args: argparse.Namespace) -> None:
    path = save_report(output=args.output, bet_type=args.bet_type)
    print(f"レポート保存: {path}")
    print(f"最新版: {DATA_DIR / 'report_latest.txt'}")


def cmd_workflow(args: argparse.Namespace) -> None:
    """毎日ルーチン: 取得 → 分析 → 歪み検知 → レポート保存"""
    init_db()
    if not args.skip_fetch:
        print("=== STEP 1/4: レース取得 (daily) ===")
        fetch_daily(
            kaisai_date=args.date,
            limit=args.limit,
            with_result=args.with_result,
            venue_code=args.venue,
        )
        print()
    else:
        print("=== STEP 1/4: 取得スキップ ===\n")

    print("=== STEP 2/4: 集計 (analyze + AI指標) ===")
    print_analyze_report(args.bet_type)
    print()
    print("\n".join(build_ai_insights_lines(args.bet_type)))
    print()
    print("\n".join(build_ai_score_lines(args.bet_type)))
    print()
    print("\n".join(build_recommend_lines(args.bet_type)))
    print()
    print("\n".join(build_learning_lines(args.bet_type)))
    print()
    print("\n".join(build_pre_race_lines(args.bet_type)))
    print()

    print("=== STEP 3/4: 歪み検知 (detect) ===")
    print_anomaly_report(args.bet_type)
    print()

    print("=== STEP 4/4: レポート保存 ===")
    path = save_report(output=args.output, bet_type=args.bet_type)
    print(f"レポート保存: {path}")
    print(f"最新版: {DATA_DIR / 'report_latest.txt'}")


def cmd_run(args: argparse.Namespace) -> None:
    cmd_fetch(args)
    print()
    cmd_analyze(argparse.Namespace(bet_type=args.bet_type))
    print()
    cmd_detect(argparse.Namespace(bet_type=args.bet_type))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="競輪観測AI: データ取得・回収率分析・異常検知",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="DBテーブルを作成")
    p_init.set_defaults(func=cmd_init)

    p_fetch = sub.add_parser("fetch", help="1レース取得")
    p_fetch.add_argument("race_id", help="netkeirinのrace_id（例: 202508115601）")
    p_fetch.add_argument("--with-result", action="store_true")
    p_fetch.set_defaults(func=cmd_fetch)

    p_daily = sub.add_parser("daily", help="指定日の複数レースを取得")
    p_daily.add_argument("--date", default=None, help="YYYYMMDD（省略時=今日）")
    p_daily.add_argument("--limit", type=int, default=DAILY_FETCH_LIMIT)
    p_daily.add_argument("--venue", default=None, help="場コード2桁")
    p_daily.add_argument("--with-result", action="store_true")
    p_daily.set_defaults(func=cmd_daily)

    p_analyze = sub.add_parser("analyze", help="市場偏りを表示")
    p_analyze.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_analyze.set_defaults(func=cmd_analyze)

    p_ai = sub.add_parser(
        "ai",
        help="AI予測強化指標（ライン・逃げ・荒れ指数など）",
    )
    p_ai.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_ai.set_defaults(func=cmd_ai)

    p_score = sub.add_parser("score", help="AIスコア・おすすめ買い目TOP3")
    p_score.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_score.set_defaults(func=cmd_score)

    p_learn = sub.add_parser("learn", help="過去結果から勝ちパターンを学習")
    p_learn.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_learn.set_defaults(func=cmd_learn)

    p_pre_race = sub.add_parser(
        "pre-race",
        help="レース直前モード（T-30/T-10/T-0 記録・直前分析）",
    )
    p_pre_race.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_pre_race.add_argument(
        "--poll",
        action="store_true",
        help="発走前ウィンドウ内のレースを自動記録",
    )
    p_pre_race.add_argument("--within-hours", type=float, default=3.0, dest="within_hours")
    p_pre_race.set_defaults(func=cmd_pre_race)

    p_ml = sub.add_parser("ml", help="XGBoost予測モデル（回収率・期待値）")
    p_ml.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_ml.add_argument("--train", action="store_true", help="モデルを学習")
    p_ml.add_argument(
        "--skip-scores",
        action="store_true",
        help="学習時にAIスコア再計算をスキップ",
    )
    p_ml.set_defaults(func=cmd_ml)

    p_notify = sub.add_parser("notify", help="通知候補の検出とDB記録")
    p_notify.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_notify.set_defaults(func=cmd_notify)

    p_backup = sub.add_parser("backup", help="DB・設定・レポートのバックアップ/復元")
    p_backup.add_argument("--list", action="store_true", dest="list_only", help="一覧表示")
    p_backup.add_argument("--restore", default=None, help="復元する backup_id")
    p_backup.add_argument("--note", default="", help="バックアップメモ")
    p_backup.add_argument("--skip-db", action="store_true")
    p_backup.add_argument("--skip-settings", action="store_true")
    p_backup.add_argument("--skip-reports", action="store_true")
    p_backup.add_argument("--skip-models", action="store_true")
    p_backup.set_defaults(func=cmd_backup)

    p_pnl = sub.add_parser("pnl", help="収支検証・購入記録")
    p_pnl.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_pnl.add_argument("--amount", type=int, default=100)
    p_pnl.add_argument("--record-targets", action="store_true", help="狙い目TOP1を記録")
    p_pnl.add_argument("--sync-virtual", action="store_true", help="仮想成績を同期")
    p_pnl.set_defaults(func=cmd_pnl)

    p_ops = sub.add_parser("ops", help="自動運用（全処理・朝6時スケジュール）")
    p_ops.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_ops.add_argument("--run-now", action="store_true", help="全処理を今すぐ実行")
    p_ops.add_argument("--daemon", action="store_true", help="朝6時自動実行デーモン")
    p_ops.add_argument("--date", default=None, help="開催日 YYYYMMDD")
    p_ops.add_argument("--limit", type=int, default=DAILY_FETCH_LIMIT)
    p_ops.add_argument("--venue", default=None)
    p_ops.add_argument("--with-result", action="store_true", help="結果も取得")
    p_ops.add_argument("--skip-fetch", action="store_true")
    p_ops.add_argument("--schedule-hour", type=int, default=6, dest="schedule_hour")
    p_ops.add_argument("--schedule-minute", type=int, default=0, dest="schedule_minute")
    p_ops.set_defaults(func=cmd_ops)

    p_collect = sub.add_parser("collect", help="100レース収集モード（日付範囲一括取得）")
    p_collect.add_argument("--start", required=True, help="開始日 YYYYMMDD")
    p_collect.add_argument("--end", required=True, help="終了日 YYYYMMDD")
    p_collect.add_argument("--limit", type=int, default=5, help="1日あたり件数")
    p_collect.add_argument("--target", type=int, default=TARGET_RACES, help="目標レース数")
    p_collect.add_argument("--venue", default=None, help="場コード2桁")
    p_collect.add_argument("--with-result", action="store_true", help="結果・払戻も取得")
    p_collect.add_argument("--no-skip", action="store_true", help="重複も再取得")
    p_collect.add_argument("--no-post", action="store_true", help="取得後の学習・レポートをスキップ")
    p_collect.add_argument("--quiet", action="store_true", help="進捗表示を省略")
    p_collect.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_collect.set_defaults(func=cmd_collect)

    p_quality = sub.add_parser("quality", help="データ品質チェック（学習可否判定）")
    p_quality.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_quality.set_defaults(func=cmd_quality)

    p_advanced = sub.add_parser("advanced-learn", help="本格学習（有効データのみ）")
    p_advanced.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_advanced.add_argument("--retrain", action="store_true", help="再学習を実行")
    p_advanced.set_defaults(func=cmd_advanced_learn)

    p_battle = sub.add_parser("battle", help="実戦判定（買い/見送り総合判定）")
    p_battle.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_battle.set_defaults(func=cmd_battle)

    p_bankroll = sub.add_parser("bankroll", help="資金管理（購入金額・元手管理）")
    p_bankroll.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_bankroll.set_defaults(func=cmd_bankroll)

    p_validate = sub.add_parser("validate", help="検証レポート（日次/週次/月次成績）")
    p_validate.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_validate.add_argument("--save", action="store_true", help="ファイルに保存")
    p_validate.set_defaults(func=cmd_validate)

    p_improve = sub.add_parser("improve", help="改善提案AI（弱点・強み・次の改善案）")
    p_improve.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_improve.add_argument("--save", action="store_true", help="ファイルに保存")
    p_improve.set_defaults(func=cmd_improve)

    p_check = sub.add_parser("check", help="完成チェック（全体の正常/注意/エラー）")
    p_check.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_check.add_argument("--save", action="store_true", help="ファイルに保存")
    p_check.add_argument("--deep", action="store_true", help="取得API疎通も確認")
    p_check.set_defaults(func=cmd_check)

    p_monitor = sub.add_parser("monitor", help="市場監視（急変・人気集中）")
    p_monitor.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_monitor.add_argument(
        "--poll",
        action="store_true",
        help="表示前に全レースのオッズを再取得（スナップショット追加）",
    )
    p_monitor.add_argument("--limit", type=int, default=30)
    p_monitor.set_defaults(func=cmd_monitor)

    p_detect = sub.add_parser("detect", help="歪み・異常を表示")
    p_detect.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_detect.set_defaults(func=cmd_detect)

    p_report = sub.add_parser("report", help="分析+検知をtxtに保存")
    p_report.add_argument(
        "--output",
        default=None,
        help="保存先（省略時 data/report_日時.txt）",
    )
    p_report.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_report.set_defaults(func=cmd_report)

    p_workflow = sub.add_parser(
        "workflow",
        help="毎日ルーチン（daily→analyze→detect→report）",
    )
    p_workflow.add_argument("--date", default=None)
    p_workflow.add_argument("--limit", type=int, default=DAILY_FETCH_LIMIT)
    p_workflow.add_argument("--venue", default=None)
    p_workflow.add_argument(
        "--with-result",
        action="store_true",
        help="結果・払戻も取得（推奨）",
    )
    p_workflow.add_argument(
        "--skip-fetch",
        action="store_true",
        help="取得をスキップ（分析とレポートのみ）",
    )
    p_workflow.add_argument("--output", default=None)
    p_workflow.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_workflow.set_defaults(func=cmd_workflow)

    p_run = sub.add_parser("run", help="1レース: fetch→analyze→detect")
    p_run.add_argument("race_id")
    p_run.add_argument("--with-result", action="store_true")
    p_run.add_argument("--bet-type", default="3連単", dest="bet_type")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    except requests.HTTPError as e:
        print(f"HTTPエラー: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
