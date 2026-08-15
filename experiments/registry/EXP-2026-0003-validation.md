# Validation Report: EXP-2026-0003

## Decision

- Status: `REJECTED`
- Validator: `research` quality gate（自動計算。paper/live承認ではない）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: none

Donchian 55/20は全期間でプラスだったが、買い持ちを下回り、予約したOOS期間でも買い持ちを上回れなかったため棄却する。

## Frozen artifacts

- Strategy: `experiments/registry/EXP-2026-0003-hypothesis.yaml`
- Data snapshot: `DATA-2026-0003`
- Parent experiment: `EXP-2026-0002`
- Transform commit: `8deecab`
- Reproduction: `uv run python scripts/build_exp_2026_0003_dataset.py` then `uv run python scripts/run_exp_2026_0003.py`
- Research venue: Binance Global Spot `BTCUSDT`
- Execution venue: Binance Japan Spot（今回の結果はshadow/liveではない）

## Data quality and daily aggregation

- 入力は52,608本の1時間足で、欠損0、重複0。
- 出力は2,192日で、日足欠損0、重複0。
- 15日の日足が線形補間された1時間足を含む（全日足の0.6843%）。
- 合成日足での売買は全期間0件、OOS期間0件。
- 合成日足でのシグナル状態変化も0件だった。ただし、合成日足は後続チャネル窓に含まれ得るため、影響が完全にないことを意味しない。

## Method and accounting

UTC日足について、直前55日間の最高値を終値で上抜けたら翌日始値で買い、保有中に直前20日間の最安値を終値で下抜けたら翌日始値で決済した。ロングのみ、初期資金1,000 USDT相当、片道fee 0.1%、往復spread 0.05%、片道slippage 0.05%で、各日終値にmark-to-marketした。

## Results

| 期間・手法 | 最終資産 | CAGR | 最大DD | 取引数 |
|---|---:|---:|---:|---:|
| 全期間・Donchian 55/20 | 4,506.09 | 28.53% | -47.71% | 30 |
| 全期間・買い持ち | 12,160.12 | 51.68% | -76.63% | — |
| OOS 2025・Donchian 55/20 | 885.16 | -12.32% | -21.83% | 8 |
| OOS 2025・買い持ち | 935.01 | -7.36% | -32.02% | — |

Donchian戦略は買い持ちより最大ドローダウンが小さかったが、事前登録した主要指標のCAGRでは下回った。OOS取引数は8件で少なく、収益性を強く主張できる統計的根拠もない。

| 片道fee | 全期間CAGR | OOS CAGR |
|---:|---:|---:|
| 0.10%（base） | 28.53% | -12.32% |
| 0.15%（adverse） | 28.20% | -12.63% |
| 0.20%（stress） | 27.88% | -12.94% |

## Rejection rationale

OOSのDonchian 55/20は買い持ちを下回り、取引数も8件にとどまった。したがって、今回の結果だけではpaper・shadow・liveへの昇格を認めない。ドローダウン低減という副次的性質は記録するが、それだけで収益戦略として採用しない。

この結果はBinance Global proxy上の研究結果であり、Binance Japanでの収益予測ではない。合成日足を含むため、実測データだけの性能を証明するものでもない。

## Required follow-up

1. EXP-2026-0003を棄却済みとして実験台帳に残し、55/20を結果に合わせて変更しない。
2. 次の候補を試す場合は、新しい実験IDで仮説、探索上限、OOS、費用、合成データの扱いを先に登録する。
3. Donchianを再検討する場合も、lookback変更は別実験とし、複数比較による多重検定を記録する。
4. 本実験からpaper・shadow・liveへの昇格申請は行わない。
