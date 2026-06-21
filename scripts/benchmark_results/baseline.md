# Tab speed benchmark (baseline)

計測条件: bet_type=3連単, 閾値=3.0s, 冷起動（キャッシュなし）

## タブ処理時間ランキング

| 順位 | タブ | 秒 |
|---:|---|---:|
| 1 | 実戦判定 | 18.995 |
| 2 | 改善提案 | 9.409 |
| 3 | 検証レポート | 7.702 |
| 4 | 資金管理 | 5.489 |
| 5 | システムチェック | 5.274 |
| 6 | グラフ | 4.273 |
| 7 | 今日のAIおすすめ | 3.851 |
| 8 | 収支検証 | 3.605 |
| 9 | AI指標 | 3.531 |
| 10 | ML予測 | 3.470 |
| 11 | 本格学習 | 1.859 |
| 12 | ライン分析 | 1.817 |
| 13 | パターン学習 | 0.028 |
| 14 | 直前分析 | 0.022 |
| 15 | データ品質 | 0.016 |
| 16 | バックアップ | 0.015 |
| 17 | 自動運用 | 0.015 |
| 18 | 100レース収集 | 0.012 |
| 19 | 市場監視 | 0.007 |
| 20 | ホーム | 0.007 |

## 3秒以上の処理

| キー | 種別 | 秒 |
|---|---|---:|
| battle (tab) | tab | 18.995 |
| improve (tab) | tab | 9.409 |
| validation (tab) | tab | 7.702 |
| bankroll (tab) | tab | 5.489 |
| check (tab) | tab | 5.274 |
| bundle:battle | bundle | 5.968 |
| bundle:validation_sync | bundle | 5.117 |
| predict_chart (tab) | tab | 4.273 |
| bundle:charts | bundle | 4.024 |
| rec (tab) | tab | 3.851 |
| bundle:ai_score | bundle | 3.689 |
| bundle:ml | bundle | 3.833 |
| pnl (tab) | tab | 3.605 |
| bundle:recommend | bundle | 3.563 |
| predict_ai (tab) | tab | 3.531 |
| bundle:ai_insights | bundle | 3.475 |
| predict_ml (tab) | tab | 3.470 |
