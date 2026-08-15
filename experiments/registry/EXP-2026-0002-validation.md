# Validation Report: EXP-2026-0002

## Decision

- Research status: `REJECTED`
- Promotion status: `NOT_ELIGIBLE`
- Validator: `research` quality gate（自動計算。paper/live承認ではない）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: none

線形補間で欠損を埋めたデータセットは生成できたが、予約したOOS期間で戦略が買い持ちを大きく下回ったため、EXP-2026-0002を棄却する。

## Frozen artifacts

- Strategy: `experiments/registry/EXP-2026-0002-hypothesis.yaml`
- Data snapshot: `DATA-2026-0002`
- Parent experiment: `EXP-2026-0001`
- Transform commit: `a41cb3a`
- Reproduction: `uv run python scripts/build_exp_2026_0002_dataset.py` then `uv run python scripts/run_exp_2026_0002.py`
- Research venue: Binance Global Spot `BTCUSDT`
- Execution venue: Binance Japan Spot（今回の結果はshadow/liveではない）

## Data quality and interpolation

- 入力は52,577行、15区間、31時間の欠損。
- 出力は52,608行、重複0、欠損0。
- 合成行は31行で、全体の0.0589%。
- 合成行には`is_interpolated=true`を付け、原本の月次・日次データは変更していない。
- 合成行での売買は全期間0件、OOS期間0件だった。
- 合成行でシグナル状態が変化した行も0件だった。ただし、合成closeは後続SMAの窓に含まれ得るため、補間の影響が完全にないことを意味しない。

## Backtest accounting

20/50 SMA、確定足終値、次バー始値、現物ロング、片道fee 0.1%、往復spread 0.05%、片道slippage 0.05%、初期資金1,000 USDT相当で実行した。損益は各バー終値でmark-to-marketし、Global proxy上のpaper計算である。

## Results

| 期間・手法 | 最終資産 | CAGR | 最大DD | 取引数 |
|---|---:|---:|---:|---:|
| 全期間・SMA戦略 | 597.90 | -8.21% | -81.24% | 1,267 |
| 全期間・買い持ち | 12,160.12 | 51.74% | -77.20% | — |
| OOS 2025・SMA戦略 | 465.74 | -53.78% | -56.63% | 203 |
| OOS 2025・買い持ち | 935.01 | -7.16% | -34.76% | — |

費用感度でもOOSのSMA戦略は改善しなかった。

| 片道fee | 全期間CAGR | OOS CAGR |
|---:|---:|---:|
| 0.10%（base） | -8.21% | -53.78% |
| 0.15%（adverse） | -17.42% | -58.23% |
| 0.20%（stress） | -25.71% | -62.25% |

## Rejection rationale

OOSで費用控除後のCAGRが買い持ちを下回り、全期間でも大幅な資産減少となった。したがって、このSMA設定はpaper・shadow・liveへ昇格させない。線形補間は欠損による計算停止を解消したが、戦略の優位性を示す結果にはならなかった。

この結果はBinance Global proxy上の研究結果であり、Binance Japanでの収益予測ではない。合成行を含むため、実測データだけの性能を証明するものでもない。

## Required follow-up

1. EXP-2026-0002を棄却済みとして実験台帳に残し、パラメータを結果に合わせて変更しない。
2. 次の戦略を試す場合は、新しい実験IDで仮説、探索上限、OOS、費用、補間規則を先に登録する。
3. 研究を続ける場合も、まず買い持ちを上回る経済的根拠と、Global/Japan差を検証できるshadow設計を用意する。
4. 本実験からpaper・shadow・liveへの昇格申請は行わない。
