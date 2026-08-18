# Premium crowding ML設計

## 選んだ情報

EXP-2026-0054〜0057では15分trade Kline由来の価格・出来高特徴量だけでは、sourceで正の
横断rank ICを作れなかった。EXP-2026-0058では、同じ価格特徴量の閾値を再調整せず、
ZOOMEX linear perpetualのpremium indexと確定済みFundingを追加する。

ZOOMEX公式APIのpremium index Klineは`start`、`end`、15分intervalを指定して過去系列を
取得できる。一方、REST orderbookは現在snapshotで過去時刻parameterを持たず、WebSocketの
orderbookとliquidationは接続後のstreamである。そのため、現在の板・清算を過去Klineへ混ぜる
backtestは行わない。

- [Premium Index Price Kline](https://zoomexglobal.github.io/docs/v3/market/preimum-index-kline)
- [Funding Rate History](https://zoomexglobal.github.io/docs/v3/market/history-fund-rate)
- [REST Orderbook](https://zoomexglobal.github.io/docs/v3/market/orderbook)
- [WebSocket Orderbook](https://zoomexglobal.github.io/docs/v3/websocket/public/orderbook)

## 経済的仮説

Premium indexはperpetual価格とindex価格の乖離圧力を表す。高い正premiumや急なpremium上昇は
long側の混雑、負premiumはshort側の混雑を示す可能性がある。絶対水準、変化、分散、横断順位と、
判断時刻より前に確定したFundingだけを組み合わせれば、価格momentumだけでは区別できなかった
混雑の継続と巻き戻しを学習できる可能性がある。

ただしpremiumは将来returnの保証ではなく、Funding算定式や上限・下限の履歴変更も完全には
再現できない。15分Klineは期間内の経路を圧縮しており、板の厚さや強制清算量ではない。

## 固定特徴量

判断時刻`t`では`t-15分`までに終了したKlineと、event timeが`t`より前のFundingだけを使う。
30日warmup後の各銘柄標本は31特徴量とする。

価格control 14個:

- local return 6h・24h、volatility 6h・24h、volume z-score 24h
- BTC return 1h・6h・24h、BTC volatility 24h
- local 6h returnのcross-sectional rank・median差・dispersion
- UTC hour sin・cos

premium 13個:

- 直近close、mean 1h・6h・24h
- change 1h・6h・24h
- standard deviation 6h・24h、30日z-score
- current premiumのcross-sectional rank・median差・dispersion

Funding 4個:

- 直前確定rate、直前3回平均、直前9回平均、直前rateからの変化

生price、symbol ID、判断時刻と同時刻のFunding、将来Funding、target固有parameterは使わない。

## 学習と検証

候補pipelineは1個だけとする。XGBoost LambdaRankで6時間先の費用込みabsolute net return順位を
学習し、Pseudo-Huber回帰でabsolute net returnを推定する。各outer cutoff前最大730日を80% fit、
6時間purge、20% calibrationへ分ける。calibration errorのq30を回帰予測へ加えた下限が0を超え、
rank normalized marginが0.25以上の場合だけtop1を100 USDT longする。

同じfoldでpremium・Funding 17特徴量を除いた価格control-only modelを固定ablationとして評価する。
ablationは候補選択に使わず、full modelが新情報から増分を得たかを判定する比較対象とする。

source9銘柄の4 time fold×3 asset foldを一度評価する。ETC、FIL、TRX、XLMは新しいpremium snapshot
でも封印し、全source gate合格時だけ同じ固定pipelineで一度開く。同じsource foldの逐次利用による
多重試行リスクは解消されないため、合格しても最大`INCONCLUSIVE`でありpaperへ直接昇格しない。
