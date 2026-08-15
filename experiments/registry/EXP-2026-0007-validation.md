# Validation Report: EXP-2026-0007

## Decision

- Research status: `REJECTED`
- Promotion status: `NOT_ELIGIBLE`
- Validator: `Independent Validation`（固定runnerの再実行結果）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: none

EXP-2026-0006で固定したDonchian 55 entry＋Bollinger 20日・2σ exit overlayを、事前登録した未観測期間（2026-01-01〜2026-07-31）で検証した。データ品質と会計検査は通過したが、base feeでoverlayの最大ドローダウンは29.42 percentage points悪化し、CAGRは39.78 percentage points悪化した。overlayのclosed round tripsも2件で、事前登録した候補条件の3件を下回った。したがって今回のforward結果は仮説を支持せず、研究上`REJECTED`とする。

この判定はGlobal proxy上の固定strategyに対するものであり、Binance Japanでの利益、約定、paper、shadow、liveの証拠ではない。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0007-hypothesis.yaml`（`95ec236`）
- Dataset manifest: `experiments/registry/DATA-2026-0004-manifest.yaml`
- Dataset builder: `scripts/build_exp_2026_0007_dataset.py`（`998d9c8`）
- Runner: `scripts/run_exp_2026_0007.py`（`637fbab`、判定status修正後の再実行）
- Frozen strategy implementation: `src/crypt_ai/research.py`
- Research venue: Binance Global Spot `BTCUSDT`
- Target venue: Binance Japan Spot（今回の結果はshadow/liveではない）
- Reproduction:
  `uv run python scripts/download_binance_global_data.py --symbol BTCUSDT --interval 1h --start-month 2020-01 --end-month 2026-07 ...`
  `uv run python scripts/build_exp_2026_0007_dataset.py ...`
  `uv run python scripts/run_exp_2026_0007.py`

## Evaluation semantics

- baseはDonchian 55日entry・20日exit、overlayは同じentryと20日・2σ Bollinger退出待機を使った。
- 2020-01-01からの全履歴で指標を計算し、資金はforward開始時に1,000 USDT相当にリセットした。
- シグナル確定後、次日始値で約定する固定ルールを使った。
- forward期間末の未決済ポジションは強制決済せず、終値mark-to-marketとした。
- fee、spread、slippageを含むbase・adverse・stressの3ケースを一度だけ評価した。

## Data integrity and accounting

- 日足2,404本（2020-01-01〜2026-07-31）、重複0、欠損0だった。
- 1時間足31本を線形補間し、15日が合成日足となった。forward期間内の約定は合成日足上で0件だった。
- Global monthly archive 79ファイルを公式`.CHECKSUM`とSHA-256で検証した。
- Binance Japan固有のtick size、最小数量、部分約定、注文拒否、実手数料は未検証である。

## Results

### Base fee

| 指標 | Donchian単独 | Bollinger退出overlay | 差分（overlay - base） |
|---|---:|---:|---:|
| 最終資産 | 884.09 | 597.62 | -286.47 |
| CAGR | -19.21% | -58.98% | -39.78 points |
| 最大DD | -14.70% | -44.11% | -29.42 points |
| closed round trips | 2 | 2 | 0 |
| 期待値/closed trade | -57.96 | -201.19 | -143.23 |
| 総手数料 | 3.70 | 3.04 | -0.66 |

Buy and Holdは最終資産716.25、CAGR -45.01%、最大DD -39.53%だった。baseはBuy and Holdを上回ったが、overlayはBuy and Holdも下回った。

### Fee sensitivity

adverse（片道0.15%）とstress（片道0.2%）でも、overlayはbaseより最大DD・CAGR・最終資産のすべてで悪化した。最大DD改善幅はそれぞれ-29.39 points、-29.36 pointsだった。

## Preregistered decision

| 条件 | 結果 | 判定 |
|---|---:|---|
| データ品質・会計・未来参照 | 通過 | 継続可能 |
| 最大DDがbase以上 | -29.42 points | 不合格 |
| CAGRがbase以上 | -39.78 points | 不合格 |
| closed round trips 3件以上 | 2件 | 不合格 |
| 最大DDが2 points超悪化 | 該当 | 棄却条件 |
| CAGRが5 points超悪化 | 該当 | 棄却条件 |

## Interpretation

2026年のforward期間では、overlayの「下側band割れ後に中心線への戻りを待つ」設計が、baseのDonchian exitよりも下落局面での退出を遅らせた可能性が高い。EXP-2026-0006で観測された過去期間のDD改善は、2026年の未観測期間では再現しなかった。

取引数が2件と少ないため、overlayがあらゆる市場環境で無効だと一般化はしない。しかし、今回の事前登録forward証拠では、overlayをpaper、shadow、liveへ進める根拠はない。

## Required follow-up

1. このoverlayのパラメータを結果に合わせて再探索せず、現行設定は廃止候補として凍結する。
2. 失敗例として2022年と2026年の下落局面を記録し、退出遅延が損失を増やす条件を分析する。
3. baseのDonchian単独を別戦略として扱う場合も、Global proxyの結果だけで昇格せず、独立したforward・Japan calibration・注文なしshadowを行う。
4. 本実験からpaper・shadow・liveへの昇格申請、資金投入、上限緩和は行わない。
