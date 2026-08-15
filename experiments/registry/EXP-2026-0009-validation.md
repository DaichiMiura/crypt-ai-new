# Validation Report: EXP-2026-0009

## Decision

- Research status: `INCONCLUSIVE`
- Promotion status: `NEEDS_FORWARD_EVIDENCE`
- Validator: `Independent Validation`（固定runnerの再実行結果）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: なし（paper・shadow・liveを許可しない）

EXP-2026-0008で固定したDonchian 55/20＋SMA200 entry filterを、2026-01-01〜2026-07-31で評価した。filteredは期間中に新規entryを行わず、baseの損失を回避したため、base feeで最大DDとCAGRは改善した。しかしfilteredのclosed round tripsは0件で、事前登録した性能候補条件（2件以上）を満たさない。

さらに、この2026年データは親実験EXP-2026-0007で既に観測済みである。そのため、今回の結果は独立した`PASSED_FORWARD_TEST`とは扱わず、研究上`INCONCLUSIVE`とする。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0009-hypothesis.yaml`（`3d8db3e`）
- Dataset manifest: `experiments/registry/DATA-2026-0004-manifest.yaml`
- Frozen implementation: `src/crypt_ai/research.py`
- Runner: `scripts/run_exp_2026_0009.py`
- Research venue: Binance Global Spot `BTCUSDT`
- Target venue: Binance Japan Spot（今回の結果はshadow/liveではない）
- Reproduction: `uv run python scripts/run_exp_2026_0009.py`

## Strategy semantics

- baseはDonchian 55日entry・20日exit。
- filteredは同じDonchian条件だが、flat状態からの新規entry時点でclose > SMA200を必須とする。
- 保有後にSMA200を下回っても、それだけでは退出しない。
- シグナル確定後、次日始値で約定する。
- 2020-01-01からの履歴で指標を計算し、forward期間の初期資金を1,000 USDT相当にリセットした。
- 期間末の未決済ポジションは強制決済せず、終値mark-to-marketとした。

## Data integrity and accounting

- 日足2,404本（2020-01-01〜2026-07-31）、重複0、欠損0だった。
- 線形補間を含む日足は15本で、forward期間の約定は合成日足上で0件だった。
- base・adverse・stressの片道fee 0.1%、0.15%、0.2%、往復spread 0.05%、片道slippage 0.05%を評価した。
- Global proxyの結果はBinance Japan固有のtick size、最小数量、部分約定、注文拒否、実手数料の証拠ではない。

## Results

### Base fee

| 指標 | Donchian単独 | SMA200 filtered | 差分（filtered - base） |
|---|---:|---:|---:|
| 最終資産 | 884.09 | 1,000.00 | +115.91 |
| CAGR | -19.21% | 0.00% | +19.21 points |
| 最大DD | -14.70% | 0.00% | +14.70 points |
| closed round trips | 2 | 0 | -2 |
| 期待値/closed trade | -57.96 | 該当なし | 該当なし |
| 総手数料 | 3.70 | 0.00 | -3.70 |

adverseとstressでもfilteredは取引を行わず、baseに対する最大DD改善幅はそれぞれ14.78 points、14.87 pointsだった。

## Preregistered decision

| 条件 | 結果 | 判定 |
|---|---:|---|
| 最大DDがbase以上 | +14.70 points | 性能条件を満たす |
| CAGRがbase以上 | +19.21 points | 性能条件を満たす |
| closed round trips 2件以上 | 0件 | 不合格 |
| 最大DDが2 points超悪化 | 該当なし | 棄却条件なし |
| CAGRが5 points超悪化 | 該当なし | 棄却条件なし |
| 独立した未観測OOS | 親実験で観測済み | forward合格不可 |

## Interpretation

SMA200フィルターは、2026年の下落局面でロングを完全に避けたため、損失制御には有効だった。しかし、取引をしなかった結果であり、利益を生む実証や約定可能性の証拠ではない。no-tradeが望ましい保護だったのか、単に機会損失を先送りしたのかは、将来の独立期間で判断する必要がある。

今回のデータは親実験で観測済みであるため、EXP-2026-0008の過去候補を独立forwardで確認した結果とは扱わない。

## Required follow-up

1. entry_window=55、exit_window=20、regime_window=200を変更せず、今後取得する新規データで真のforward testを行う。
2. forwardではentry拒否数、保有率、機会損失、最大DD、回復時間を必ず記録する。
3. filteredが長期間no-tradeになる場合の資本配分と運用上の扱いを別途定義する。
4. Binance Japanのread-only calibrationと注文なしshadowの証拠がそろうまで、paper・shadow・liveへの昇格申請を行わない。
