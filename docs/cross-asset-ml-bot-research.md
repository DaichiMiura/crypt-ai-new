# Cross-asset ML bot調査と次実験設計

## 結論

EXP-2026-0054の失敗は、平均0に近い6時間returnを二乗誤差で直接回帰した結果、予測が
0付近へ縮み、費用ベース閾値を847判断中4回しか超えなかったことにある。次の実験では、
「6時間後returnが往復費用の2倍を超える状態」を直接分類し、確率の校正、周期再学習、
時系列OOS、銘柄leave-one-out、完全未学習銘柄holdoutを分離する。

最終対象は、このリポジトリの既存実験で一度も使用していないZOOMEX linear perpetualの
`BCHUSDT`、`LTCUSDT`、`DOTUSDT`、`DOGEUSDT`とする。これらの価格、特徴量、label、損益は、
source銘柄だけの開発gateを通過するまで表示・集計しない。

## ML bot OSS調査

### Freqtrade / FreqAI

- repository: <https://github.com/freqtrade/freqtrade>
- license: GPL-3.0
- 2026-08-18調査時のGitHub最新release表示: 2026.5.1
- docs: <https://www.freqtrade.io/en/stable/freqai/>

FreqAIは、周期的な再学習、履歴backtestでの再学習再現、prediction保存、model expiration、
NaN/outlier処理、dry-run接続を提供する。公式example strategy自身がproduction用ではないと
警告され、保存modelは自分で学習したものだけを読み込むsecurity noticeもある。

今回取り込む設計原則:

- 固定頻度のrolling retrainingをhistorical backtestでも同じ順序で再現する。
- feature生成とlabel生成を分離し、lookahead検査を独立テストにする。
- model version、train cutoff、予測、欠測・期限切れ判定を監査可能に保存する。
- botへ接続する前にresearch、次に注文なしdry/shadowで評価する。

今回は直接依存に追加しない。公式exchange資料にZOOMEXの明示対応を確認できず、GPL-3.0の
大きなexecution frameworkを研究runnerへ入れるより、既存の決定論的会計と安全境界を保った
薄い実装の方が監査しやすい。paper/shadow接続を検討する段階で別途採用審査する。

### Microsoft Qlib

- repository: <https://github.com/microsoft/qlib>
- license: MIT
- docs: <https://qlib.readthedocs.io/en/latest/>

Qlibはdata processing、model training、backtest、portfolioを分離し、cross-sectional model、
rolling/online serving、concept driftを扱う。今回取り込むのは、model scoreとportfolio変換を
分離すること、時刻単位で銘柄群をgroup化すること、銘柄を丸ごと外した評価を持つこと。
中国株中心のdata/provider前提と大きな依存面を持つため、ZOOMEX研究へ直接追加しない。

### FinRL / FinRL_Crypto

- repository: <https://github.com/AI4Finance-Foundation/FinRL_Crypto>
- license: MIT
- paper: <https://arxiv.org/abs/2209.05559>

crypto向けdeep reinforcement learningとbacktest overfitting検査を提供する。一方、現在の
Kline-only環境ではaction、reward、fill、market impactのsimulator誤差がそのままpolicyへ
学習される。今回の目的は予測失敗の切り分けであり、RLを追加すると原因が増えるため採用しない。

## EXP-2026-0054から変更する学習

### Target

各判断境界`t`で、entry=`t open`、exit=`t+6h open`とする。

- `UP=1`: gross simple returnが`+0.64%`より大きい。
- `UP=0`: それ以外。

0.64%はbase往復費用0.32%の2倍であり、予測対象と取引gateを同じ経済量へ合わせる。
raw returnの平均を回帰せず、費用を十分に超えるtail eventの確率を学ぶ。

### Source assets

既に他実験で観測済みの次の9銘柄だけを学習・model選択に使う。

- LINKUSDT、UNIUSDT、AVAXUSDT、AAVEUSDT
- ETHUSDT、SOLUSDT、XRPUSDT、ADAUSDT、NEARUSDT

BTCUSDTはmarket context専用で売買しない。symbol ID、symbol one-hot、生priceは入力せず、
dimensionless featureだけを使って未知銘柄へ移転可能な仮説に限定する。

### Final unseen assets

2026-08-18にZOOMEX public instruments-infoだけを問い合わせ、価格を見ずに次を確認した。

| symbol | status | launchTime UTC | selection |
|---|---|---|---|
| BCHUSDT | Trading | 2018-01-01 | holdout |
| LTCUSDT | Trading | 2018-01-01 | holdout |
| DOTUSDT | Trading | 2021-03-18 | holdout |
| DOGEUSDT | Trading | 2021-06-02 | holdout |
| ETCUSDT | Trading | 2021-06-29 | reserve |
| TRXUSDT | Trading | 2021-08-12 | reserve |
| ATOMUSDT | Trading | 2021-10-11 | reserve |
| SUIUSDT | Trading | 2023-05-02 | 履歴不足で除外 |

既存実験に一度も登場せず、2022年開始前に上場済みの候補をlaunchTime昇順で4銘柄固定した。
同日のBCH/LTCはsymbol昇順で固定する。reserveとの入れ替えを結果確認後に行わない。

## 検証構造

### Stage 1: source-only temporal development

- train: source assetsの2022-02-01〜2024-12-31。
- development: source assetsの2025年。
- 6時間label境界をpurgeし、同時刻の銘柄行を別splitへ分けない。
- logistic regressionとXGBoost binary classifierの2 family、各1設定だけを比較する。
- Brier score、log loss、precision/recall、確率bin、費用込みPnLを報告する。

### Stage 2: leave-one-asset-out transfer gate

9 source assetsを1銘柄ずつ完全に外し、残り8銘柄で学習して外した1銘柄の2025年を予測する。
feature scaling、class weight、確率calibration、閾値選択へ外した銘柄のlabelを使わない。

最終unseen銘柄を開く前に最低限、次を要求する。

- 9 fold合計で100往復以上。
- 9銘柄中6銘柄以上でbase費用net PnLが正。
- fold合計net PnLがcashと6時間momentumを上回る。
- 費用2倍でfold合計net PnLが正。
- constant base-rate predictorよりBrier scoreが改善する。
- 1銘柄の利益寄与が合計正利益の40%を超えない。
- 未来参照、補間、allocation rejection、会計不一致が0。

未達ならfinal unseen assetデータを開かず棄却する。

### Stage 3: adaptive unseen-asset holdout

Stage 2通過modelだけを使い、2026-01-01〜2026-07-31の4 unseen assetsを一度評価する。
source assetsだけを用いて30日ごとに直近730日で再学習し、target assetのlabelは過去分も含めて
一切train、calibration、threshold変更へ使わない。target assetでは判断直前24時間のfeatureだけを
inference入力に使う。

各時刻で4銘柄中`P(UP)`最大を選び、確率が固定gate以上かつ2位との差が固定margin以上の場合だけ
100 USDT longする。確率gateとmarginはsource-only Stage 1/2の結果を見る前に仮説台帳で固定する。

最終gateは、cash・momentum超過、費用2倍正、合計40往復以上、各銘柄5往復以上、4銘柄中3銘柄
以上のnet PnL正、最大DD>-10%、block bootstrap、データ・会計完全性を要求する。

## Features

EXP-2026-0054の24個を無批判に全採用しない。相関がほぼ0だったFunding、basis、wick等を含む
全特徴量modelと、価格・volatility・activityだけのcore modelを同時に探索すると試行数が増える。
次の単一core setへ絞る。

- local log return: 1、3、6、12、24時間。
- local realized volatility: 6、24時間。
- downside semivolatility: 24時間。
- candle body/range: 直前1時間。
- log volume/turnover z-score: 24時間。
- BTC context: 6、24時間return、24時間volatility。
- cross-section: sourceまたはtarget group内の6時間return rank、中央値差、dispersion。
- regime: UTC hour sin/cos、直近24時間のBTC方向とvolatilityのinteraction 1個。

合計21特徴量、生price、symbol ID、将来Funding、orderbook代理値は使わない。特徴量はtrainだけで
処理し、欠測を補間しない。

## FreqAIから採用するrolling設計

- model retrain interval: 30日。
- rolling train window: 730日。
- label maturity purge: 6時間。
- model expiration: 次の30日境界。期限超過modelでは新規取引しない。
- prediction log: model cutoff、source symbols、sample count、class balance、feature hash、probability。
- model artifact: local生成物だけ。外部modelを読み込まない。

## 行わないこと

- EXP-2026-0054のholdoutをtrainへ戻さない。
- unseen assetの過去labelを「少量fine-tuning」と称して使わない。
- target asset別にthresholdやfeatureを変えない。
- LSTM、Transformer、RL、AutoML、数千featureを同時に試さない。
- FreqAI example strategyを利益戦略としてコピーしない。
- 現在の板snapshotを過去Klineへ混ぜない。
- source gate未達時にunseen assetを開かない。

## 実行順序

1. source追加5銘柄とsealed target 4銘柄のZOOMEX 1時間足snapshotを取得し、hash・端点・欠測だけを固定する。
2. data manifestをcommitする。target価格、feature、label、損益は表示しない。
3. EXP-2026-0055として21特徴量、2 model、確率gate、margin、rolling条件、全採否条件を事前登録する。
4. source-only developmentとleave-one-asset-outを実行する。
5. gate通過時だけ4 unseen assetsを一度開く。
6. 成否に関係なく結果を台帳へ残し、paper/shadow変更は別承認とする。
