# Validation Report: EXP-2026-0004

## Decision

- Status: `NEEDS_EVIDENCE`
- Validator: `research` quality gate（自動計算。paper/live承認ではない）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: none
- Expiry or review date: 次の時系列検証とvenue差分検証が完了するまで

2025年の予約OOSでは買い持ちを上回ったが、全期間では大幅に下回った。OOSは1年・9往復取引に限られ、Global proxyからBinance Japanへの移転可能性、注文制約、安全運用の証拠も不足しているため、paper・shadow・liveのいずれにも昇格させない。`NEEDS_EVIDENCE`は承認ではない。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0004-hypothesis.yaml`（`b587e2b`）
- Strategy implementation: `src/crypt_ai/research.py`（`b587e2b`）
- Reproducible runner and trade statistics: `scripts/run_exp_2026_0004.py`（`c7c74ac`）
- Data snapshot: `DATA-2026-0003`
- Data transform commit: `8deecab`
- Reproduction: `uv run python scripts/build_exp_2026_0003_dataset.py` then `uv run python scripts/run_exp_2026_0004.py`
- Research venue: Binance Global Spot `BTCUSDT`
- Execution venue: Binance Japan Spot（今回の結果はshadow/liveではない）
- Venue/pair mapping: Global `BTCUSDT`をJapan側target pairと同一価格とは扱わない
- Fee model: `FEE-2026-0001-initial-assumption`
- Risk policy: `docs/risk-policy.md`

## Independence statement

- Strategy implementer: Codex
- Validator: research quality gate（独立した人間承認ではない）
- Conflicts or shared context: 実装者と自動検証のコンテキストは共有されるため、paper昇格の独立レビューを代替しない

## Findings

### Data integrity

- 2020-01-01から2025-12-31 UTCの日足2,192本を使用した。
- 欠損日足0、重複0、日足間隔の欠損0だった。
- DATA-2026-0002由来の線形補間時間足を含む日足は15本（0.6843%）で、`is_interpolated=true`を保持した。
- 合成日足でのシグナル状態変化0、約定0だった。ただし、合成終値が20日窓へ入り得るため、影響が完全にゼロとは主張しない。
- ボリンジャーバンドは確定した日tの終値を含めて計算し、`desired_position`を1本shiftして日t+1始値で約定した。テストでエントリーと決済の遅延を確認した。
- 現在足の終値で同時に約定する処理、未知の欠損日を自動生成する処理はない。
- Global履歴はBinance Japanの価格basis、板、流動性、約定率、手数料の正本ではない。

### Backtest accounting

- 現物ロング専用、レバレッジなし、同時保有1ポジション、初期資金1,000 USDT相当、全量売買である。
- 約定候補は次日始値。片道feeはbase 0.1%、adverse 0.15%、stress 0.2%、往復spread 0.05%、片道slippage 0.05%とした。
- 価格、数量、手数料、現金は`Decimal`で計算し、終値mark-to-marketを行った。
- BUY/SELLの往復損益、手数料、勝率、平均損益、期待値は`run_exp_2026_0004.py`が約定履歴から再計算する。
- Binance Japan固有のtick size、最小数量、最小notional、部分約定、注文拒否は未実装であり、実運用会計の証拠ではない。

### Statistical robustness

- 事前登録した最大variant数は1。window=20、母標準偏差`ddof=0`、倍率2を結果後に探索していない。
- 主要OOSは2025年だけであり、ウォークフォワード、複数OOS期間、別銘柄・別venueでの再現性は未検証である。
- 全期間では買い持ちを大幅に下回った。OOSの改善が2025年の特定相場環境に依存する可能性を排除できない。
- OOSは9往復取引（18 fills）で、単一期間の統計的根拠としては限定的である。
- risk-of-ruinは推定していない。OOSの良好な数字を将来の収益保証として扱わない。

## Results

Base fee（片道0.1%、往復spread 0.05%、片道slippage 0.05%）の結果は次のとおり。

| 期間・手法 | 最終資産 | CAGR | 最大DD | 約定数 | 往復取引 | 勝率 | 往復期待値 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 全期間・Bollinger 20/2σ | 1,071.40 | 1.16% | -53.05% | 70 | 35 | 68.57% | 2.04 USDT |
| 全期間・買い持ち | 12,160.12 | 51.68% | -76.63% | — | — | — | — |
| OOS 2025・Bollinger 20/2σ | 1,155.97 | 15.65% | -20.07% | 18 | 9 | 88.89% | 17.33 USDT |
| OOS 2025・買い持ち | 935.01 | -7.36% | -32.02% | — | — | — | — |

全期間の平均勝ちトレードは51.27 USDT、平均負けトレードは-105.37 USDTだった。OOSでは平均勝ち35.91 USDT、平均負け-131.28 USDTであり、勝率が高くても損失分布が左右非対称である。OOSの手数料合計はbaseで20.37 USDTだった。

| 片道fee | 全期間CAGR | OOS CAGR | 全期間往復期待値 | OOS往復期待値 |
|---:|---:|---:|---:|---:|
| 0.10%（base） | 1.16% | 15.65% | 2.04 USDT | 17.33 USDT |
| 0.15%（adverse） | 0.57% | 14.61% | 0.99 USDT | 16.18 USDT |
| 0.20%（stress） | -0.02% | 13.58% | -0.03 USDT | 15.03 USDT |

## Rejection or hold rationale

OOSの結果だけなら仮説と整合するが、全期間CAGRは1.16%にとどまり買い持ち51.68%を下回る。さらにOOSは2025年の1期間・9往復取引で、Global proxyとBinance Japanの差、実注文制約、再起動・照合・kill switchの安全試験も未完了である。このため、研究上の候補として記録するにとどめ、`NEEDS_EVIDENCE`とする。

この結果はBinance Global proxy上のpaper計算であり、Binance Japanでの利益、fill、手数料、shadowまたはlive運用の証拠ではない。合成日足を含むため、実測データだけの性能証明でもない。

## Required follow-up

1. EXP-2026-0004の20日・2σ条件を固定したまま、結果に合わせた再探索を行わず、複数の時系列OOSまたはwalk-forwardで再検証する。
2. Binance Japanの公式fee、対象pair、tick size、LOT_SIZE、最小notional、部分約定、注文拒否を確認し、fee modelとexecution modelの版を固定する。
3. Japan shadowで注文なしのリアルタイムデータ、遅延、spread、想定fill、basis、拒否条件、損益照合をGlobal結果と比較する。
4. stale data、切断、再起動、重複注文、リスク制限、kill switch、ロールバックのテストを追加する。
5. 上記の証拠が揃うまで、paper・shadow・liveへの昇格申請、資金投入、上限緩和を行わない。
