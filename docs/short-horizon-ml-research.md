# 短期ML取引の事前調査

## 結論

短い足を使う次の実験は、単純な「次の足が上か」を分類するのではなく、ZOOMEX linear perpetualの1時間足から6時間先のgross returnを予測し、予測値が往復費用を十分に上回る場合だけ最大1銘柄をlongする設計が妥当である。

実験前に新しいZOOMEX 1時間足snapshotを作り、2025年までを開発・model選択、未確認の2026-01-01〜2026-07-31を封印holdoutとする。holdout取得後は、欠測・hash・端点だけを先に確認し、価格、label、損益を開封する前に仮説を登録する。

## 外部調査から得た設計原則

### 予測精度より費用付き変換規則が重要

2026年のBTCUSDT 1時間足研究は、XGBoost、LSTM、iTransformerの単純な符号売買が10 bpsの費用で崩れ、予測returnの絶対値が費用ベース閾値を超えた場合だけpositionを変更する規則が結果へ大きく影響したと報告している。モデル間の優劣よりexecution filterの影響が大きく、論文自身も板、部分約定、時変spreadを再現していない制約を明記している。

- [Machine Learning-Based Bitcoin Trading Under Transaction Costs](https://arxiv.org/abs/2606.00060)

したがって次の実験ではaccuracy 50%超を主目的にせず、予測gross returnが固定往復費用の2倍を超える場合だけ取引する。現在のbase往復仮定はfee 0.12% + spread 0.10% + slippage 0.10% = 0.32%なので、entry閾値はgross予測`+0.64%`とする。

### 複雑な深層モデルを最初に採用しない

暗号資産LOB研究では、logistic回帰、XGBoost、DeepLOB、Conv1D+LSTMを比較し、入力の前処理とfeature設計を適切に行うと単純モデルが複雑なモデルと同等以上になり得ると報告されている。短期化を理由にLSTMやTransformerを最初から採用する根拠は弱い。

- [Exploring Microstructural Dynamics in Cryptocurrency Limit Order Books](https://arxiv.org/abs/2506.05764)

次の候補はridge回帰をsanity baseline、浅いXGBoost回帰を非線形候補とする。model familyは2個までとし、ニューラルネットは比較しない。

### regime変更をまたぐ検証が必要

rolling-windowで暗号資産を検証した既存研究でも、validationとtestで市場方向が変わるとmodel・銘柄別の精度に一貫した優劣がなく、複数の設計判断が結果を左右すると報告されている。

- [Forecasting and trading cryptocurrencies with machine learning under changing market conditions](https://doi.org/10.1186/s40854-020-00217-x)

random splitは使わない。時刻単位でtrain/validation/holdoutを分離し、6時間labelの境界には最低6時間のpurgeを入れる。銘柄別行を混ぜても同時刻の行を別foldへ分離しない。

## ZOOMEXデータの実現可能性

ZOOMEX公式APIはlinear perpetualについて次を公開している。

- Kline: 1、3、5、15、30、60分など。trade OHLCにvolumeとturnoverを含む。1ページ最大1000行。
- Mark price、index price、premium index priceのhistorical Kline。
- Funding history。銘柄ごとにintervalが異なるためinstrument情報と一緒に固定する必要がある。
- Public trade: price、size、taker side、時刻。RESTはrecent dataで、ページはarchiveへの導線を持つ。
- Orderbook: linearは最大500 levelのsnapshotとupdate IDを返す。

公式資料:

- [ZOOMEX Get Kline](https://zoomexglobal.github.io/docs/v3/market/kline)
- [ZOOMEX Mark Price Kline](https://zoomexglobal.github.io/docs/v3/market/mark-kline)
- [ZOOMEX Premium Index Price Kline](https://zoomexglobal.github.io/docs/v3/market/preimum-index-kline)
- [ZOOMEX Funding History](https://zoomexglobal.github.io/docs/v3/market/history-fund-rate)
- [ZOOMEX Public Trading History](https://zoomexglobal.github.io/docs/v3/market/recent-trade)
- [ZOOMEX Orderbook](https://zoomexglobal.github.io/docs/v3/market/orderbook)

2026-01-01のLINKUSDTについて、認証なしの公開APIを4行だけ問い合わせ、trade Klineとpremium index Klineの`interval=60`がともに成功することを確認した。値は表示せず、行数と成功codeだけを確認した。

REST orderbook資料が定義するのは時点snapshotであり、任意の過去時刻を指定するparameterはない。このendpointだけから過去板を再構築できないと判断する。orderbook imbalanceを使う実験は、今後WebSocketまたは定期snapshotを収集して別snapshotを作るまで行わない。現在のKline backtestへ現在の板snapshotを混ぜない。

## リポジトリ内データの探索的確認

現在のtarget venue正本`DATA-2026-0005`はZOOMEX 2時間足で、2026-01-01より前までである。既存のBinance 1時間足`DATA-2026-0004`はSpot proxyであり、ZOOMEX perpetualの新実験には使わない。

既観測のZOOMEX 2時間足を探索専用に使い、次足openから将来closeまでのgross return頻度を確認した。4銘柄で`+0.50%`を超えた割合は概ね次の範囲だった。

| horizon | 正例率の範囲 |
|---|---:|
| 2時間 | 29.4%〜30.6% |
| 6時間 | 37.1%〜37.9% |
| 12時間 | 40.6%〜41.8% |
| 24時間 | 42.6%〜44.1% |

この集計は1時間足結果ではなく、既観測2時間足からのhorizon選定用近似である。2時間は費用に対して短く正例が少ない一方、12〜24時間は短期状態の検証という目的から離れる。6時間なら費用超過候補が極端に少なくなく、Funding intervalより短い固定保有を基本にできるため、最初のhorizonとする。

## 推奨する次実験の骨格

まだ仮説IDは登録せず、データ取得・hash固定前の設計候補とする。

### Data

- venue/product: ZOOMEX linear USDT perpetual。
- 取引対象: LINKUSDT、UNIUSDT、AVAXUSDT、AAVEUSDT。
- 市場context専用: BTCUSDT。BTCは売買しない。
- interval: 1時間。
- 系列: trade OHLCV/turnover、mark OHLC、index OHLC、premium index OHLC、Funding。
- train: 利用可能な開始時点〜2024-12-31。
- model selection: 2025-01-01〜2025-12-31。既観測developmentとして扱う。
- sealed holdout: 2026-01-01〜2026-07-31。最終判定まで1回だけ開封する。
- 補間: 行わない。欠測を含むfeature windowとlabelを除外し、件数を報告する。

### Decision and target

- 判断時刻: UTC 00、06、12、18時の1日4回。
- feature cutoff: 判断足の確定closeまで。
- entry: 次の1時間足open。
- exit: entryから6時間後のopen。途中resizeなし。
- target: entry openからexit openまでのgross log return。
- position: 予測値最大の1銘柄だけを候補とし、予測gross returnが`+0.64%`以下ならcash。
- size: 100 USDT固定、最大1 long。shortは行わない。
- Funding: 保有区間がsettlement時刻をまたいだ場合だけ実績rateを適用する。

6時間ごとの判断に限定することで、同一銘柄の保有期間とlabelを重ねず、毎時売買による見かけの標本増加と過大turnoverを避ける。

### Features

technical indicatorを大量生成せず、経済的意味のある固定feature群に限定する。

- trade return: 1、3、6、12、24時間。
- candle: 1時間range、body、upper/lower wick。
- activity: volumeとturnoverの24時間z-score。
- risk: 6時間・24時間realized volatility、24時間downside semivolatility。
- derivatives: trade-mark basis、mark-index basis、premium index close、直近既知Funding rate、次回Fundingまでの時間。
- market context: BTCの6時間・24時間returnと24時間volatility。
- cross-section: 4対象銘柄の6時間return順位と中央値差。

すべてdimensionless return、ratio、z-scoreへ変換し、生price水準を入力しない。

### Models

1. Ridge regression: 非線形modelが単純baselineを上回るか確認する。
2. XGBoost regression: depth 3以下、固定seed、単一thread、強いL2、固定round数。hyperparameter探索はしない。

2025 developmentで費用込みnet PnLが高い方を1モデルだけ固定し、その後に2026 holdoutを一度だけ実行する。同点または両方がcash baseline以下ならholdoutを開封せず実験を棄却する。

XGBoostはApache-2.0で、公開release policyとsecurity reporting窓口を持つ成熟OSSである。ただし最新releaseだけがsecurity support対象と明記されている。採用時にはPython 3.12互換性、wheel、lockfile、license、モデルartifact hashを確認し、外部から受け取ったmodel fileは読み込まない。

- [XGBoost repository](https://github.com/dmlc/xgboost)
- [XGBoost release policy](https://xgboost.readthedocs.io/en/stable/contrib/release.html)
- [XGBoost security policy](https://xgboost.readthedocs.io/en/latest/security.html)

### Baselines and gates

baselineは次の2つを固定する。

- cash control。
- 同じ6時間周期・100 USDT・top1制約で、直近6時間returnが最大かつ正の銘柄をlongする単純momentum。

候補判定には最低限、次を要求する。

- sealed holdoutで費用・Funding込みnet PnLが正、かつ単純momentumを上回る。
- base費用で完了往復30件以上。
- 費用2倍stressでもnet PnLが正。
- 最大DDが-10%より良い。
- allocation rejection、未来参照、欠測混入が0。
- 候補とbaselineの日次return差について24時間・72時間・168時間block bootstrapを報告し、優位差CIが0をまたぐ場合は`INCONCLUSIVE`を上限とする。

予測MAE、directional accuracy、rank IC、feature importanceは診断値であり、PnL条件の代替にしない。

## 行わない設計

- 5分・15分足から開始しない。現行費用とKline-only約定モデルに対してmicrostructure依存が強すぎる。
- 同じ2025年でhorizon、閾値、feature、銘柄を反復最適化しない。
- random train/test split、shuffle、通常のIID K-foldを使わない。
- 未確定Klineのclose、将来Funding、将来の銘柄順位をfeatureへ入れない。
- accuracyだけで採用しない。
- 板履歴がない状態でorderbook imbalance backtestを作らない。
- LSTM、Transformer、強化学習を最初の比較へ入れない。
- ML確率や予測returnに比例して元本を増額しない。

## 実験前の順序

1. ZOOMEX 1時間足data manifestとdownloaderを作る。
2. 2026 holdoutを表示せず取得し、端点、行数、欠測、重複、hashだけを固定する。
3. XGBoostの依存審査とlockを別変更で行う。
4. 上記仕様をEXP ID付きで事前登録する。
5. trainと2025 developmentだけで2モデルを比較する。
6. gateを満たした1モデルだけで2026 sealed holdoutを一度実行する。
7. 結果に関係なく台帳へ保存し、paper/shadow変更は別承認とする。
