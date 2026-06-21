"""「詳細データを読み込む」相当処理の内訳計測（本番コードは変更しない）"""
from __future__ import annotations

import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BET_TYPE = "3連単"


@dataclass
class Step:
    key: str
    label: str
    seconds: float
    note: str = ""


@dataclass
class ProfileReport:
    steps: list[Step] = field(default_factory=list)
    scenario: str = ""

    def add(self, key: str, label: str, sec: float, note: str = "") -> None:
        self.steps.append(Step(key, label, round(sec, 3), note))

    @property
    def total(self) -> float:
        return round(sum(s.seconds for s in self.steps), 3)

    def top5(self) -> list[Step]:
        return sorted(self.steps, key=lambda s: s.seconds, reverse=True)[:5]

    def get(self, key: str) -> Step | None:
        for s in self.steps:
            if s.key == key:
                return s
        return None


def _timed(fn):
    t0 = time.perf_counter()
    result = fn()
    return time.perf_counter() - t0, result


def profile_github_restore_cold(report: ProfileReport) -> None:
    from config import DB_PATH, GITHUB_PERSIST_BRANCH, GITHUB_REPO
    from db import get_connection, init_db, safe_table_count, table_exists
    from github_persist import (
        PERSIST_DIR,
        _github_get_file,
        import_snapshot,
        is_github_enabled,
        load_local_snapshot,
    )
    import requests
    from github_persist import _api_headers, GITHUB_REQUEST_TIMEOUT

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    init_db()
    conn = get_connection()
    try:
        if table_exists(conn, "races") and safe_table_count(conn, "races") > 0:
            report.add("precheck", "DB非空", 0.0, "cold計測失敗")
            return
    finally:
        conn.close()

    snapshot: dict = {}

    if is_github_enabled():
        def list_branch():
            list_url = (
                f"https://api.github.com/repos/{GITHUB_REPO.strip()}/contents/{PERSIST_DIR.name}"
            )
            resp = requests.get(
                list_url,
                headers=_api_headers(),
                params={"ref": GITHUB_PERSIST_BRANCH},
                timeout=GITHUB_REQUEST_TIMEOUT,
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            return resp.json()

        sec, items = _timed(list_branch)
        report.add(
            "1_github_branch",
            "GitHub dataブランチ取得（ファイル一覧API）",
            sec,
            f"branch={GITHUB_PERSIST_BRANCH} entries={len(items)}",
        )

        file_names = [
            it.get("name") or ""
            for it in items
            if it.get("type") == "file" and (it.get("name") or "").endswith(".json")
        ]

        def load_json_file(name: str):
            raw, _ = _github_get_file(name)
            return json.loads(raw) if raw else None

        if "races.json" in file_names:
            sec, data = _timed(lambda: load_json_file("races.json"))
            if data is not None:
                snapshot["races.json"] = data
            report.add("2_races_json", "races.json 読込", sec)

        if "results.json" in file_names:
            sec, data = _timed(lambda: load_json_file("results.json"))
            if data is not None:
                snapshot["results.json"] = data
            report.add("3_results_json", "results.json 読込", sec)

        odds_names = sorted(n for n in file_names if n.startswith("odds_"))
        odds_rows = 0

        def load_odds():
            nonlocal odds_rows
            for name in odds_names:
                data = load_json_file(name)
                if data is not None:
                    snapshot[name] = data
                    odds_rows += len(data)

        sec, _ = _timed(load_odds)
        report.add(
            "4_odds_json",
            "odds系json 読込",
            sec,
            f"files={len(odds_names)} rows={odds_rows}",
        )

        for extra in ("entries.json", "learned_patterns.json", "meta.json"):
            if extra in file_names and extra not in snapshot:
                data = load_json_file(extra)
                if data is not None:
                    snapshot[extra] = data
    else:
        sec, local = _timed(load_local_snapshot)
        report.add(
            "1_github_branch",
            "GitHub dataブランチ取得",
            0.0,
            "TOKEN/REPO未設定",
        )
        report.add(
            "1_local_snapshot",
            "ローカル persist/ 一括読込",
            sec,
            "GitHub不可時の代替",
        )
        if not local:
            return
        snapshot = local
        report.add("2_races_json", "races.json 読込", 0.0, "local一括")
        report.add("3_results_json", "results.json 読込", 0.0, "local一括")
        odds_n = sum(1 for k in snapshot if k.startswith("odds_"))
        report.add("4_odds_json", "odds系json 読込", 0.0, f"local odds_files={odds_n}")

    if not snapshot.get("races.json"):
        report.add("5_db_import", "DB反映", 0.0, "races.json なし")
        return

    sec, stats = _timed(lambda: import_snapshot(snapshot))
    report.add(
        "5_db_import",
        "DB反映（import_snapshot）",
        sec,
        f"races={stats.get('race_count')} results={stats.get('result_count')}",
    )


def profile_load_app_bundles(report: ProfileReport) -> None:
    from bundle_cache import build_full_app_bundles
    from race_features import clear_race_metrics_cache

    clear_race_metrics_cache()
    sec, _ = _timed(lambda: build_full_app_bundles(BET_TYPE))
    report.add("6_recommend", "AIおすすめ生成", 0.0, "full load 内")
    report.add("7_battle", "実戦判定生成", 0.0, "full load 内")
    report.add("8_line", "ライン分析生成", 0.0, "full load 内")
    report.add("9_charts", "グラフ生成", 0.0, "full load 内")
    report.add("full_bundles", "build_full_app_bundles() 全体", sec)


def profile_full_detail_load(*, cold_db: bool) -> ProfileReport:
    from config import DB_PATH

    report = ProfileReport(scenario="cold_db" if cold_db else "warm_db")
    backup: Path | None = None

    if cold_db and DB_PATH.exists():
        backup = DB_PATH.with_suffix(".bak_profile")
        shutil.copy2(DB_PATH, backup)
        DB_PATH.unlink()

    try:
        if cold_db:
            profile_github_restore_cold(report)
        else:
            for key, label in (
                ("1_github_branch", "GitHub dataブランチ取得"),
                ("2_races_json", "races.json 読込"),
                ("3_results_json", "results.json 読込"),
                ("4_odds_json", "odds系json 読込"),
                ("5_db_import", "DB反映"),
            ):
                report.add(key, label, 0.0, "DB既存 — スキップ")

        profile_load_app_bundles(report)
        report.add(
            "10_ui_rerender",
            "画面再描画（Streamlit rerun）",
            0.0,
            "CLI計測不可。load_app_bundles_cached 後の rerun + 全タブ描画で追加数秒の見込み",
        )
    finally:
        if backup and backup.exists():
            if DB_PATH.exists():
                DB_PATH.unlink()
            shutil.move(str(backup), str(DB_PATH))

    return report


def print_user_table(report: ProfileReport) -> None:
    rows = [
        ("1", "1_github_branch", "GitHub dataブランチ取得時間"),
        ("2", "2_races_json", "races.json読込時間"),
        ("3", "3_results_json", "results.json読込時間"),
        ("4", "4_odds_json", "odds系json読込時間"),
        ("5", "5_db_import", "DB反映時間"),
        ("6", "6_recommend", "AIおすすめ生成時間"),
        ("7", "7_battle", "実戦判定生成時間"),
        ("8", "8_line", "ライン分析生成時間"),
        ("9", "9_charts", "グラフ生成時間"),
        ("10", "10_ui_rerender", "画面再描画時間"),
    ]
    print(f"\n=== {report.scenario} ===")
    print("| # | 項目 | 秒 | 備考 |")
    print("|---:|---|---:|---|")
    total = 0.0
    for num, key, label in rows:
        s = report.get(key)
        if not s:
            continue
        total += s.seconds
        print(f"| {num} | {label} | {s.seconds:.3f} | {s.note} |")
    sb = report.get("score_base")
    if sb:
        print(f"| — | {sb.label} | {sb.seconds:.3f} | {sb.note} |")
    print(f"\n合計（1-10）: {total:.3f}s")
    print("\nTOP5:")
    for i, s in enumerate(report.top5(), 1):
        print(f"  {i}. {s.label}: {s.seconds:.3f}s")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    out_dir = ROOT / "scripts" / "benchmark_results"
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: list[ProfileReport] = []
    if mode in ("cold", "both"):
        reports.append(profile_full_detail_load(cold_db=True))
    if mode in ("warm", "both"):
        reports.append(profile_full_detail_load(cold_db=False))

    for r in reports:
        print_user_table(r)
        (out_dir / f"detail_load_{r.scenario}.json").write_text(
            json.dumps(
                [{"key": s.key, "label": s.label, "seconds": s.seconds, "note": s.note} for s in r.steps],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    warm = next((r for r in reports if r.scenario == "warm_db"), reports[-1])
    cold = next((r for r in reports if r.scenario == "cold_db"), None)
    top = warm.top5()

    print("\n=== 改善判断 ===")
    print("Render課金で改善しそうか:")
    print("  部分的にのみ。CPU/RAM増強は battle/charts/ML推論（項目7,9）に効く可能性はある。")
    print("  ただし GitHub API待ち（項目1-4）や line API sleep は課金では短縮しにくい。")
    print("  Render有料プラン単体では根本解決になりにくい。")
    print("コード修正で改善すべきか:")
    print("  はい。load_app_bundles() は14系統を直列実行し、API/重複計算が多い。")
    if top:
        print(f"  warm_db最大: {top[0].label} ({top[0].seconds}s)")
    if cold:
        ctop = cold.top5()
        if ctop:
            print(f"  cold_db最大: {ctop[0].label} ({ctop[0].seconds}s) — Render再起動直後に該当")

    print("\n=== コード上の経路 ===")
    print("  app.load_app_bundles_cached() → spinner「詳細データを読み込み中...」")
    print("  → load_app_bundles_safe() → load_app_bundles() + system_check")
    print("  現行UIは lazy load 化済みで、このボタンはホームから削除されている可能性あり。")
    print("  同等処理: ensure_db_ready + load_app_bundles / 各タブ「読み込む」")


if __name__ == "__main__":
    main()
