# EXP-2026-0056 ranking + quantile source gate結果

## 結論

15分足LambdaRankと費用控除後net return q40を組み合わせたpipelineは、source-only gateを通過
しなかった。ETC、FIL、TRX、XLMの封印targetは開いていない。EXP-2026-0056は`REJECTED`で
終了し、paper、shadow、liveへの昇格資格はない。

## 結果

| 指標 | 結果 | 事前条件 | 判定 |
|---|---:|---:|---|
| completed round trips | 42 | 240以上 | fail |
| 正のfold | 8/12 | 9以上 | fail |
| 正のsource銘柄 | 6/9 | 6以上 | pass |
| base net PnL | +37.6432 USDT | 0超・momentum超 | pass |
| 2x cost net PnL | +24.4559 USDT | 0超 | pass |
| momentum net PnL | -1895.4991 USDT | baseline | — |
| rank IC | -0.00331 | 0超・momentum超 | fail |
| momentum rank IC | -0.01134 | baseline | — |
| q40 pinball loss | 0.0079446 | constant未満 | pass |
| constant q40 loss | 0.0079850 | baseline | — |
| 最大正利益寄与 | 31.38% | 35%以下 | pass |

9銘柄中、LINK、UNI、AVAX、XRP、ADA、NEARはaggregate損益が正、AAVE、ETH、SOLは負だった。

## 解釈

費用控除後q40を直接学ぶ変更は、定数q40よりpinball lossを約0.5%改善し、成立した42取引は
費用2倍でもaggregateで正だった。この部分はEXP-2026-0055の絶対確率分類より経済量へ直接
つながっている。

一方、次の理由で未知銘柄へ移すには弱い。

- 42取引は事前条件240の17.5%しかなく、少数の好結果に依存する。
- 正のfoldは8/12で、時間×銘柄組の安定性条件へ届かない。
- rank ICは-0.0033で、相対順位を学ぶ主目的を達成していない。
- q40改善幅は小さく、thresholdを事後に下げればsource最適化になる。

したがって、PnLが正という理由だけでgateを緩めず棄却した。

## 完全性

- source標本52,542、除外判断時刻0。
- 4時間fold × 3完全除外asset foldの12 OOS foldを固定条件で実行した。
- source入力からnet labelを再構築し、全base・momentum取引のrealized net returnと
  `net_return × quantity × raw entry`会計を照合した。
- fold合計、銘柄分離、重複判断なし、最低200 USDT reserveを再確認した。
- ETC、FIL、TRX、XLMはsource dataset、学習、予測、PnLに含まれず未開封である。
- 成果物SHA-256:
  `2fceee5ff68302150bd60a1a3b6671ae3e3434c402db62d8b891af271526416f`

## 次に活かせること

15分足を増やすだけでは順位signalは改善しなかった。次の新仮説を作るなら、targetを開かずに
保持したまま、source側で市場regime別にrankerを分ける、またはcross-sectional returnを市場
共通成分と残差へ分解して残差順位を学ぶ方法が候補になる。ただし本結果を使ったmarginやq40
alphaの調整は行わない。
