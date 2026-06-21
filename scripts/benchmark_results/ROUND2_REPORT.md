# 4タブ 2秒以内達成レポート（round2）

計測: `python scripts/benchmark_tab_speed.py round2`  
プロファイル: `python scripts/profile_slow_tabs.py`

## 目標タブ — 修正後（冷起動）

| タブ | 修正前(after) | 修正後(round2) | 目標 |
|---|---:|---:|---|
| 実戦判定 | 4.666s | **1.617s** | ≤2s ✓ |
| 改善提案 | 4.028s | **0.187s** | ≤2s ✓ |
| システムチェック | 4.017s | **0.075s** | ≤2s ✓ |
| AI指標 | 3.610s | **0.052s** | ≤2s ✓ |

## 関数別ボトルネック（修正前 → 対策）

### 実戦判定（4.7s → 1.6s）

| 関数 | 修正前 | 修正後 | 対策 |
|---|---:|---:|---|
| `build_race_metrics` + API sleep | ~1.7s | ~0.05s | `fetch_missing=False` + LRU |
| `get_advanced_learning_bundle` 空patterns時 `run_advanced_learning` | ~1.9s | ~0.02s | `retrain=True` 時のみ学習 |
| `get_ml_bundle` / `predict_races` | ~1.6s | ~1.55s | scores 渡し・並列パイプライン |
| 依存7本の直列読込 | ~19s(初回) | 並列+上記 | `_load_battle_dependencies` |

### AI指標（3.6s → 0.05s）

| 関数 | 原因 | 対策 |
|---|---|---|
| `build_race_metrics` API | 1.7s×2回 | `fetch_missing=False` |
| `build_ai_insights_lines` | metrics再計算 | 同一 metrics を渡す |
| `recovery_by_feature`×4 | 軽量（metrics共有） | 変更なし |

### 改善提案（4.0s → 0.19s）

| 関数 | 原因 | 対策 |
|---|---|---|
| `cached_battle_judge_bundle` | 上記と同じ | battle 高速化 |
| `build_improvement_proposals` 内 quality/advanced 再取得 | 重複 | 引数で渡す |
| `_collect_market_conditions` | 軽量 | — |

### システムチェック（4.0s → 0.08s）

| 関数 | 原因 | 対策 |
|---|---|---|
| `check_fetch(deep=True)` API | ~1.8s | 通常読込は `deep=False` |
| `get_ai_score_bundle` API経由 metrics | ~1.7s | `fetch_missing_lines=False` |
| 依存4本の直列読込 | — | `ThreadPoolExecutor` 並列 |

## 実装変更ファイル

- `race_features.py` — `fetch_missing` + `@lru_cache` metrics
- `advanced_learning.py` — 空 patterns 時の自動再学習停止
- `ai_score.py` — UI 默认 `fetch_missing_lines=False`
- `ai_insights.py` — metrics 共有・lines 重複除去
- `bundle_cache.py` — battle パイプライン・check 並列・improve 引数渡し
- `improvement_ai.py` — quality 引数・refresh=False

## 再計測コマンド

```powershell
python scripts/benchmark_tab_speed.py round2
python scripts/benchmark_tab_speed.py compare baseline round2
```

**注:** 「API含む再チェック」ボタン / CLI の `fetch_missing=True` では従来どおり API 取得します（意図的な深い確認用）。
