# Validation Report: EXP-2026-0008

## Decision

- Research status: `PASSED_RETROSPECTIVE_VALIDATION`
- Promotion status: `NEEDS_FORWARD_EVIDENCE`
- Validator: `Independent Validation`（固定runnerの再実行結果）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: なし（paper・shadow・liveを許可しない）

Donchian 55/20単独の新規entryを、終値が200日SMAを上回る場合だけ許可するレジームフィルターを、2021〜2025年の5暦年で比較した。base feeでは最大DDが3年で改善し、改善幅中央値は4.58 percentage points、CAGRは4年でbase以上、filteredのclosed round tripsは11件となり、事前登録した候補基準を満たした。

ただし、対象期間は既存データであり、Binance Global proxy上の単一銘柄・過去診断である。したがって研究上の候補として記録するだけで、未観測forward、Binance Japan calibration、注文なしshadow、paper・live運用への昇格は行わない。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0008-hypothesis.yaml`（`a16af84`）
- Dataset manifest: `experiments/registry/DATA-2026-0003-manifest.yaml`
- Frozen implementation: `src/crypt_ai/research.py`
- Runner: `scripts/run_exp_2026_0008.py`
- Research venue: Binance Global Spot `BTCUSDT`
- Target venue: Binance Japan Spot（今回の結果はshadow/liveではない）
- Reproduction: `uv run python scripts/run_exp_2026_0008.py`

## Strategy semantics

- baseはDonchian 55日entry・20日exit。
- filteredは同じDonchian条件だが、flat状態からの新規entry時点でclose > SMA200を必須とする。
- 保有後にSMA200を下回っても、それだけでは退出しない。退出はDonchian 20日条件だけである。
- シグナル確定後、次日始値で約定する。
- 各年は初期資金1,000 USDT相当にリセットし、年末未決済は終値mark-to-marketとした。

## Data integrity and accounting

- DATA-2026-0003の日足2,192本、重複0、欠損0を使用した。
- 線形補間を含む日足は15本で、合成日足上の約定は別集計した。
- base・adverse・stressの片道fee 0.1%、0.15%、0.2%、往復spread 0.05%、片道slippage 0.05%を評価した。
- Global proxyの結果はBinance Japan固有のtick size、最小数量、部分約定、注文拒否、実手数料の証拠ではない。

## Base fee results

| 年 | Donchian CAGR | Filtered CAGR | Donchian最大DD | Filtered最大DD | DD改善幅 | Filtered往復数 |
|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 46.24% | 52.08% | -38.22% | -33.64% | +4.58 points | 2 |
| 2022 | -15.93% | 0.00% | -16.81% | 0.00% | +16.81 points | 0 |
| 2023 | 17.80% | 2.97% | -26.65% | -20.88% | +5.77 points | 3 |
| 2024 | 45.11% | 45.11% | -20.89% | -20.89% | 0.00 points | 2 |
| 2025 | -12.32% | -12.32% | -21.83% | -21.83% | 0.00 points | 4 |

### Preregistered scorecard

| 条件 | 結果 | 判定 |
|---|---:|---|
| 最大DD改善年数 | 3 / 5 | 合格（3年以上） |
| 最大DD改善幅中央値 | +4.58 points | 合格（正） |
| CAGRがbase以上の年数 | 4 / 5 | 合格（2年以上） |
| Filtered closed round trips | 11 | 合格（5以上） |

adverseとstressでも、最大DD改善年数3 / 5、改善幅中央値4.57 / 4.57 points、CAGR優位年数4 / 5、closed round trips 11は変わらなかった。

## Interpretation

2022年のような長期下降局面では新規entryを全く行わず、baseの損失を避けた。一方、2023年はDDを抑えたものの、上昇局面への参加が遅れてCAGRがbaseを下回った。したがって、このフィルターは利益を常に増やす手法ではなく、長期下降局面でのロング曝露を減らすリスク制御候補と解釈する。

2024年と2025年はbaseと同じ結果であり、SMAフィルターが常に取引を変えるわけではない。取引数11件も5年・単一銘柄としては十分とは言えず、過去結果の合格を独立OOSの証拠として扱わない。

## Required follow-up

1. パラメータを変更せず、EXP-2026-0008の固定戦略を次に取得する未観測期間でforward testする。
2. forward期間ではentry拒否数、保有率、機会損失、最大DD、回復時間を記録する。
3. Binance Japanのread-onlyデータで価格basis、spread、遅延、想定fill、注文制約をcalibrationする。
4. forward・Japan calibration・注文なしshadowの証拠がそろうまで、paper・shadow・liveへの昇格申請を行わない。
