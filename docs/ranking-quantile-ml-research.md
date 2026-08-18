# Ranking + quantile MLの次実験設計

## EXP-2026-0055からの変更理由

EXP-2026-0055は、未観測4銘柄で予測確率と上昇eventに弱い相関を示したが、全予測が固定確率
gate 0.45未満で、期待gross returnも費用基準へ届かなかった。絶対確率分類をさらに調整せず、
「同時刻でどの銘柄が相対的に強いか」と「費用控除後returnの下側分位点」を別modelで学ぶ。

今回確認済みのtargetを再学習へ戻さず、sourceは従来の9銘柄だけとする。最終評価は価格未確認の
ETCUSDT、FILUSDT、TRXUSDT、XLMUSDTを封印する。

## OSS・公式仕様の確認

XGBoost公式Learning to Rankは、同じqueryに属する候補をqidでgroup化し、LambdaMARTで候補間の
順位を学習する。取引では一つの判断時刻をquery、同時刻の銘柄を候補として対応させる。

- Learning to Rank: <https://xgboost.readthedocs.io/en/stable/tutorials/learning_to_rank.html>
- Parameters: <https://xgboost.readthedocs.io/en/stable/parameter.html>

公式quantile regressionは`reg:quantileerror`と`quantile_alpha`を提供し、`hist` tree methodを
例示する。quantile crossingなどの制約はあるため、今回は単一alpha=0.40だけを使う。

- Quantile regression example:
  <https://xgboost.readthedocs.io/en/stable/python/examples/quantile_regression.html>

新しいframeworkや依存は追加せず、既存固定XGBoost 3.4.1と決定論的会計を使う。

## Targetとlabel

各UTC 6時間境界`t`で、`t`の15分足openから`t+6h`の15分足openまで100 USDT longした場合の
費用・Funding込みnet returnをlabelとする。fee、spread、slippage、Funding、quantity stepを
既存会計と同じ規則で反映する。

- Rank model: 同時刻のtrain銘柄をnet return昇順順位0..N-1へ変換し、最高をN-1とする。
- Magnitude model: 同じnet returnの条件付き40%分位点を直接推定する。
- Entry: rank score最大、正規化rank marginが固定下限以上、かつ予測q40 net returnが0より大きい。

絶対上昇event確率を経由せず、費用を引いた経済量を直接gateにする。

## Multi-timeframe feature

15分trade Klineから次のdimensionless特徴量だけを作る。

- local log return: 15分、30分、1、3、6、12、24時間。
- local realized volatility: 1、6、24時間。
- downside semivolatility: 6、24時間。
- 直前15分candle body、range。
- log volume、turnoverの直近24時間z-score。
- BTC context: 1、6、24時間returnと6、24時間volatility。
- cross-section: 6時間return rank、中央値差、dispersion。
- UTC hour sin/cos。

合計26特徴量。生price、symbol ID、target label、将来Funding、現在の板snapshotを入力しない。

## Source検証

Source 9銘柄をsymbol昇順でround-robinし、結果と無関係に3組へ固定する。

- fold A: AAVEUSDT、ETHUSDT、SOLUSDT
- fold B: ADAUSDT、LINKUSDT、UNIUSDT
- fold C: AVAXUSDT、NEARUSDT、XRPUSDT

各foldで3銘柄を丸ごと外し、残り6銘柄だけで学習する。時間評価は2024-H1、2024-H2、
2025-H1、2025-H2の4期間。各cutoff直前730日だけをtrainへ使い、6時間labelをpurgeする。
合計12個のasset×time OOS foldとする。

source gate、rank margin、model parameters、target gateはデータ完全性確認後、source値を用いた
学習結果を見る前にEXP-2026-0056台帳で固定する。

## 未観測target

2026-08-18にZOOMEX public instruments-infoだけを確認した。価格、return、volumeは未確認。

| symbol | status | launchTime UTC | role |
|---|---|---|---|
| ETCUSDT | Trading | 2021-06-29 | sealed target |
| FILUSDT | Trading | 2021-06-29 | sealed target |
| TRXUSDT | Trading | 2021-08-12 | sealed target |
| XLMUSDT | Trading | 2021-08-12 | sealed target |
| ICPUSDT | Trading | 2021-09-15 | reserve |
| ALGOUSDT | Trading | 2021-09-23 | reserve |
| ATOMUSDT | Trading | 2021-10-11 | reserve |

既存実験未使用、Trading、USDT linear、2022年以前launchを満たす候補からlaunchTime昇順、
同値symbol昇順で4銘柄固定した。source gate不合格ならtarget値を開かない。
