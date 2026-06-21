# タブ速度改善レポート

計測: `python scripts/benchmark_tab_speed.py baseline` / `after`  
比較: `python scripts/benchmark_tab_speed.py compare`

予測ロジックは変更していません。読込順序・重複計算の除去・表示時の同期スキップのみです。

---

## 1. 全タブ表示速度（修正後・冷起動）

| タブ | 秒 |
|---|---:|
| 実戦判定 | 4.666 |
| 改善提案 | 4.028 |
| システムチェック | 4.017 |
| AI指標 | 3.610 |
| グラフ | 2.426 |
| 資金管理 | 2.087 |
| 今日のAIおすすめ | 2.078 |
| ML予測 | 2.049 |
| 検証レポート | 2.002 |
| 収支検証 | 1.933 |
| 本格学習 | 1.903 |
| ライン分析 | 1.785 |
| その他（ホーム・市場等） | < 0.03 |

---

## 2. タブ処理時間ランキング（修正前 TOP10）

1. 実戦判定 — 18.995s  
2. 改善提案 — 9.409s  
3. 検証レポート — 7.702s  
4. 資金管理 — 5.489s  
5. システムチェック — 5.274s  
6. グラフ — 4.273s  
7. 今日のAIおすすめ — 3.851s  
8. 収支検証 — 3.605s  
9. AI指標 — 3.531s  
10. ML予測 — 3.470s  

---

## 3. 3秒以上だった処理（修正前）

**タブ（11件）**

- 実戦判定 18.995s  
- 改善提案 9.409s  
- 検証レポート 7.702s  
- 資金管理 5.489s  
- システムチェック 5.274s  
- グラフ 4.273s  
- 今日のAIおすすめ 3.851s  
- 収支検証 3.605s  
- AI指標 3.531s  
- ML予測 3.470s  

**内部 bundle（7件）**

- battle_judge（depsなし）5.968s  
- validation sync_virtual=True 5.117s  
- charts 4.024s  
- ai_score 3.689s  
- ml_model 3.833s  
- ai_recommend 3.563s  
- ai_insights 3.475s  

---

## 4. 実施した改善（効果大きい順 TOP5）

| # | 内容 | 主な効果 |
|---:|---|---|
| 1 | **実戦判定の依存 bundle を並列取得**（`bundle_cache._load_battle_dependencies`） | 実戦判定 19.0s → 4.7s（75%短縮） |
| 2 | **ai_score の二重 `build_race_scores` 除去** | ai_score bundle 3.7s → 1.8s、rec/pnl 約46%短縮 |
| 3 | **検証タブ: `sync_virtual=False` + 改善タブの二重 validation 除去** | 検証 7.7s → 2.0s、改善 9.4s → 4.0s |
| 4 | **charts の二重 `build_race_scores` 除去** | グラフ 4.3s → 2.4s |
| 5 | **ライン分析: UI読込時 API スキップ**（`fetch_missing=False`） | line bundle 1.7s → 0.01s（DBのみ） |

補足: 学習タブの `refresh=True` 常時実行を `refresh=False` に変更（ボタン押下時のみ再学習）。

---

## 5. 修正前後 比較表

| タブ | 修正前(s) | 修正後(s) | 差分 | 改善率 |
|---|---:|---:|---:|---:|
| 実戦判定 | 18.995 | 4.666 | -14.329 | 75.4% |
| 改善提案 | 9.409 | 4.028 | -5.381 | 57.2% |
| 検証レポート | 7.702 | 2.002 | -5.700 | 74.0% |
| 資金管理 | 5.489 | 2.087 | -3.402 | 62.0% |
| システムチェック | 5.274 | 4.017 | -1.257 | 23.8% |
| グラフ | 4.273 | 2.426 | -1.847 | 43.2% |
| 今日のAIおすすめ | 3.851 | 2.078 | -1.773 | 46.0% |
| 収支検証 | 3.605 | 1.933 | -1.672 | 46.4% |
| ML予測 | 3.470 | 2.049 | -1.421 | 41.0% |
| AI指標 | 3.531 | 3.610 | +0.079 | — |

**3秒超タブ: 修正前 10件 → 修正後 4件**（実戦判定・改善・システムチェック・AI指標）

---

## 再計測コマンド

```powershell
cd c:\Users\yuhar\OneDrive\Desktop\keirin_ai
python scripts/benchmark_tab_speed.py baseline
python scripts/benchmark_tab_speed.py after
python scripts/benchmark_tab_speed.py compare
```

結果 JSON: `scripts/benchmark_results/*.json`
