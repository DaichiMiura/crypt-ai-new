# EXP-2026-0057 残差ranking source gate結果

## 結論

BTC市場共通成分を30日betaで除いた残差rankingと、残差・BTC市場のPseudo-Huber回帰を
組み合わせたpipelineはsource-only gateを通過しなかった。ETC、FIL、TRX、XLMの封印targetは
開いていない。EXP-2026-0057は`REJECTED`で終了し、paper、shadow、liveへの昇格資格はない。

## 結果

| 指標 | 結果 | 事前条件 | 判定 |
|---|---:|---:|---|
| completed round trips | 171 | 240以上 | fail |
| 正のfold | 6/12 | 9以上 | fail |
| 正のsource銘柄 | 3/9 | 6以上 | fail |
| base net PnL | -2.0931 USDT | 0超・momentum超 | fail |
| 2x cost net PnL | -55.8020 USDT | 0超 | fail |
| momentum net PnL | -1895.4991 USDT | baseline | — |
| residual rank IC | -0.00376 | 0超・residual momentum超 | fail |
| residual momentum rank IC | -0.00999 | baseline | — |
| lower estimate coverage | 67.77% | 60–80% | pass |
| 最大正利益寄与 | 83.85% | 35%以下 | fail |

正のaggregate損益だった銘柄はAAVE、ADA、NEARの3銘柄だけだった。ADAの+43.03 USDTが
正利益合計の83.85%を占めた。held-out group C（AVAX、NEAR、XRP）は4期間すべてaggregateで
負だった。

## なぜ改善しきれなかったか

時間分離q30 calibrationは、評価標本の実現net returnが下限以上となる割合67.77%で事前範囲に
入り、費用gateを通過する取引はEXP-2026-0056の42件から171件へ増えた。この部分は改善した。

しかし、市場共通成分を除いても残差rank ICは-0.00376であり、予測順位は平均的にわずかに
逆向きだった。残差momentumの-0.00999よりは良いが、正の相対signalにはなっていない。
また、通常費用でほぼ損益ゼロ、費用2倍で-55.80 USDTとなった。両者の差53.71 USDTは171件
あたり約0.314 USDTで、微弱な費用前edgeが現実的な費用に吸収された形である。

12 fold中、正だったのは6 foldだけで、最後の期間は各held-out groupで5、6、3件しか取引が
なかった。したがって、単にthresholdを事後に緩めると、source期間への追加最適化になる。

## 完全性

- source標本51,462、除外判断時刻0。
- 4期間×3完全除外asset groupの12 OOS foldを固定条件で一度実行した。
- foldごとの取引PnL和、aggregate和、判断時刻の重複なし、held-out銘柄所属、gate再判定を
  機械的に再照合した。
- ETC、FIL、TRX、XLMは学習、予測、PnLに含まれず、封印を維持した。
- 成果物SHA-256:
  `6fc58da0e5de85da16412cced41ebca6e2aa124156368489b9f983bbfeb530d6`
- 作成者と承認者を分離する独立検証は未実施で、状態は`PENDING`のままである。

## 次に活かせること

絶対returnの校正不足より、横断順位signalの弱さが主な制約である。次の仮説では同じsource foldを
使った閾値調整を避け、注文板や建玉など新しい情報源を追加するか、別期間・別venueを新しい
sourceとして用意する必要がある。未観測4銘柄は、独立した有力仮説ができるまで保持する。
