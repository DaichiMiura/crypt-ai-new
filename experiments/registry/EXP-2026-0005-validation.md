# Validation Report: EXP-2026-0005

## Decision

- Status: `NEEDS_EVIDENCE`
- Validator: `research` quality gate（自動計算。paper/live承認ではない）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: none
- Expiry or review date: 複数OOS・walk-forwardとvenue差分検証が完了するまで

事前登録した候補基準（OOS最大ドローダウン改善、費用控除後期待値、全期間最大ドローダウン非悪化）はbase feeと感度ケースで満たした。しかし、予約OOSは4往復取引だけであり、単一Global proxyの結果からpaper・shadow・liveへ昇格させる証拠としては不足しているため、`NEEDS_EVIDENCE`とする。これは承認ではない。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0005-hypothesis.yaml`（`f33e87b`）
- Strategy implementation: `src/crypt_ai/research.py`（`f33e87b`）
- Reproducible runner: `scripts/run_exp_2026_0005.py`（`f33e87b`）
- Data snapshot: `DATA-2026-0003`
- Data transform commit: `8deecab`
- Reproduction: `uv run python scripts/build_exp_2026_0003_dataset.py` then `uv run python scripts/run_exp_2026_0005.py`
- Research venue: Binance Global Spot `BTCUSDT`
- Execution venue: Binance Japan Spot（今回の結果はshadow/liveではない）
- Venue/pair mapping: Global `BTCUSDT`をJapan側target pairと同一価格とは扱わない
- Fee model: `FEE-2026-0001-initial-assumption`
- Risk policy: `docs/risk-policy.md`

## Independence statement

- Strategy implementer: Codex
- Validator: research quality gate（独立した人間承認ではない）
- Conflicts or shared context: 実装者と自動検証のコンテキストは共有されるため、paper昇格の独立レビューを代替しない

## Method

Donchian単独を比較対象とし、entryは両方とも「直前55日high最高値の上抜けを翌日始値で買う」に固定した。

- Donchian単独: 直前20日low最安値の下抜けを翌日始値で決済
- Exit overlay: 保有中に20日中心線から母標準偏差2倍を引いた下側バンドを終値で下回ったら待機し、その後中心線を終値で上回った翌日始値で決済
- 下側バンド割れを経ていない中心線上抜けでは、overlayは決済しない
- 現物ロング専用、レバレッジなし、同時保有1ポジション
- 確定日足で判定し、同じ終値では約定しない

このoverlayは即時の損切りではなく、Donchianのexitを平均回帰型の退出へ置き換える実験である。

## Findings

### Data integrity

- 2020-01-01から2025-12-31 UTCの日足2,192本を使用した。
- 欠損日足0、重複0、日足間隔の欠損0だった。
- DATA-2026-0002由来の線形補間時間足を含む日足は15本（0.6843%）で、`is_interpolated=true`を保持した。
- base・overlayとも、補間日足でのentry、退出シグナル、約定は0件だった。ただし合成終値が後続の55日・20日窓へ入り得るため、影響が完全にゼロとは主張しない。
- entry、退出待機、退出の状態は日tの確定値から計算し、`desired_position`を1本shiftして日t+1始値へ遅延させた。
- Global履歴はBinance Japanの価格basis、板、流動性、約定率、手数料の正本ではない。

### Backtest accounting

- 初期資金1,000 USDT相当、片道fee 0.1%をbase、0.15%をadverse、0.2%をstressとした。
- 往復spread 0.05%、片道slippage 0.05%、Decimalによる価格・数量・手数料・現金計算、終値mark-to-marketを共通化した。
- base feeの全期間手数料はDonchian単独100.52 USDT、overlay235.32 USDT、OOS手数料はそれぞれ7.87 USDT、8.98 USDTだった。overlayは保有資産が大きくなったため、約定回数が少なくても名目手数料は増え得る。
- Binance Japan固有のtick size、最小数量、最小notional、部分約定、注文拒否は未実装であり、実運用会計の証拠ではない。

### Statistical robustness

- entry=55、band=20、倍率2、ddof=0を固定し、最大variant数は1とした。
- OOSは2025年の4往復取引（8 fills）で、単一期間の統計的根拠として限定的である。
- 複数OOS、walk-forward、別銘柄・別venue、相場環境別の独立検証は未実施である。
- risk-of-ruin、ドローダウン回復時間、時間加重の保有率は未推定である。
- overlayがDonchian単独より良い結果になったのは、Donchianの20日exitより長くトレンドを保有できた影響も考えられ、損失制御だけの効果とは分離できない。

## Results

Base fee（片道0.1%、往復spread 0.05%、片道slippage 0.05%）の結果は次のとおり。

| 期間・手法 | 最終資産 | CAGR | 最大DD | 約定数 | 往復取引 | 往復期待値 |
|---|---:|---:|---:|---:|---:|---:|
| 全期間・Donchian 55/20 | 4,506.09 | 28.53% | -47.71% | 30 | 15 | 233.74 USDT |
| 全期間・Donchian entry＋BB exit | 17,822.58 | 61.64% | -39.71% | 26 | 13 | 1,294.04 USDT |
| 全期間・買い持ち | 12,160.12 | 51.68% | -76.63% | — | — | — |
| OOS 2025・Donchian 55/20 | 885.16 | -12.32% | -21.83% | 8 | 4 | -28.71 USDT |
| OOS 2025・Donchian entry＋BB exit | 1,089.44 | 7.99% | -14.33% | 8 | 4 | 22.36 USDT |
| OOS 2025・買い持ち | 935.01 | -7.36% | -32.02% | — | — | — |

OOSの最大DD改善幅は7.50 percentage points、CAGR差は+20.31 pointsだった。全期間でも最大DDは8.00 points改善した。

| 片道fee | overlay OOS CAGR | overlay OOS最大DD | DD改善幅 | overlay OOS期待値 |
|---:|---:|---:|---:|---:|
| 0.10%（base） | 7.99% | -14.33% | 7.50 points | 22.36 USDT |
| 0.15%（adverse） | 7.61% | -14.42% | 7.68 points | 21.27 USDT |
| 0.20%（stress） | 7.23% | -14.50% | 7.87 points | 20.19 USDT |

## Rejection or hold rationale

今回の固定条件では、Donchian単独に比べてoverlayのOOS最大DDが小さくなり、OOS期待値も正だった。全期間の最大DDも悪化せず、事前登録した候補基準は満たしている。

一方、OOSは4往復に限られ、overlayの優位性が2025年の特定相場環境やDonchian exitの仕様差に依存している可能性がある。Global proxyからBinance Japanへの移転可能性、実注文制約、再起動・照合・kill switchの安全試験も未完了である。このため、研究上の有望候補として記録するが、paper・shadow・liveへは昇格させない。

## Required follow-up

1. entry=55、band=20、2σを変更せず、複数の時系列OOSまたはwalk-forwardで再検証する。
2. Donchian単独とoverlayのドローダウン回復時間、保有率、退出後の再上昇取り逃し、連敗、リスク・オブ・ルインを追加計測する。
3. Binance Japanの公式fee、対象pair、tick size、LOT_SIZE、最小notional、部分約定、注文拒否を確認し、execution modelの版を固定する。
4. Japan shadowで注文なしのリアルタイムデータ、遅延、spread、想定fill、basis、拒否条件、損益照合をGlobal結果と比較する。
5. 上記の証拠が揃うまで、paper・shadow・liveへの昇格申請、資金投入、上限緩和を行わない。
