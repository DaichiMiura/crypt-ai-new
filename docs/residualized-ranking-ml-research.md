# Residualized ranking ML設計

## 目的

EXP-2026-0056は費用控除後q40と少数取引のPnLでは改善したが、rank ICが負で、42取引しか
作れなかった。q40 alphaやrank marginを緩めず、順位labelそのものからBTC市場共通成分を除く。

新しいpipelineは次を別々に学習する。

1. 各銘柄の費用込みnet returnから、判断時点までの30日beta×BTC将来returnを引いた残差。
2. BTC市場全体の6時間return。
3. 残差順位。

推論時は`予測残差 + 既知の過去beta × 予測BTC return`で期待net returnを再構成する。

## Point-in-time betaとregime

各判断時刻より前の確定15分足だけを使い、直近30日の非重複6時間log return 120個から
`cov(asset, BTC) / var(BTC)`を計算する。BTC分散が1e-12以下なら判断時刻を除外する。

BTC regimeは次の2 bit、4状態とする。

- trend: BTC 24時間returnが0より大きいか。
- volatility: BTC直近24時間volatilityが直近30日volatilityより大きいか。

既存26特徴量へbeta、trend、high-volatilityを加え、合計29特徴量とする。symbol IDは使わない。

## Robust expected return

XGBoost `reg:pseudohubererror`を固定設定で使う。残差return modelとBTC market modelは同じ
tree設定、`huber_slope=0.01`、300 round、seed 0とする。rankingは従来どおり
`rank:pairwise`を使う。

各outer foldの730日train windowを時間順80/20へ分ける。先頭80%だけで3 modelをfitし、
末尾20%で`actual net return - reconstructed expected net return`の30%分位点を求める。
評価時のone-sided lower estimateは`expected + calibration error q30`とする。

entryはrank normalized margin>=0.25かつlower estimate>0だけ。誤差分位点、margin、model設定は
探索しない。

## 検証と多重試行

EXP-2026-0054〜0056のsource結果を見た後の新仮説なので、過去実験とは別variantとして記録する。
同じsource12-foldを再利用するが、EXP-2026-0057内では単一pipelineだけを一度評価する。

ETC、FIL、TRX、XLMはDATA-2026-0008のまま封印を維持する。source gateを通過しない場合は
target値を開かない。過去source結果に合わせたalpha、margin、fold、銘柄変更は行わない。
