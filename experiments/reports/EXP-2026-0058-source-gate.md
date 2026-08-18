# EXP-2026-0058 premium crowding ML source gate結果

## 結論

premium indexと確定済みFundingは、価格control-only modelより横断rank ICを改善した。しかし
top1取引の費用控除後損益は悪化し、source gateを通過しなかった。ETC、FIL、TRX、XLMの
価格・premium targetは開いていない。EXP-2026-0058は`REJECTED`で、paper、shadow、liveへの
昇格資格はない。

## 結果

| 指標 | full | 価格control-only | 事前条件 | 判定 |
|---|---:|---:|---:|---|
| completed round trips | 158 | 149 | 240以上 | fail |
| 正のfold | 5/12 | — | 9以上 | fail |
| 正のsource銘柄 | 5/9 | — | 6以上 | fail |
| net PnL | -48.2630 USDT | +29.0352 USDT | 0超・ablation超 | fail |
| 2x cost net PnL | -98.1036 USDT | — | 0超 | fail |
| rank IC | +0.00642 | -0.00077 | 0超・ablation超 | pass |
| lower estimate coverage | 68.15% | — | 60–80% | pass |
| 最大正利益寄与 | 30.93% | — | 35%以下 | pass |

full modelの正のaggregate損益はAVAX、ETH、XRP、ADA、NEARの5銘柄だった。AAVEの
-40.06 USDTが最大の負寄与である。通常費用で負のため、2倍費用ではさらに悪化した。

## 何が改善し、なぜ利益にならなかったか

premium・Fundingを加えるとrank ICは正になり、価格control-onlyより0.00719改善した。価格以外の
perpetual混雑情報に、全3候補の相対順序をわずかに説明する増分情報があったことを示す。

一方、rank ICは各queryの全候補順位の平均であり、実際に売買するtop1の条件付き期待値を保証しない。
full modelの158取引は勝率46.8%、平均-0.305 USDT、中央値-0.272 USDTだった。固定ablationの149取引は
勝率51.7%、平均+0.195 USDT、中央値+0.083 USDTである。premium特徴量によってtop選択または取引時刻が
変わり、順位全体は改善しても極端な負けを含むtop1選択へ変換された。

12 fold中full PnLが正だったのは5 foldだけで、最後の期間は3 group合計9取引だった。したがって
marginやq30を事後調整して取引を増やすことは、同じsource foldへの追加最適化になるため行わない。

## 完全性

- source標本51,462、除外判断時刻0。
- 4期間×3完全除外asset groupの12 OOS foldを固定条件で一度実行した。
- full、2倍費用、価格ablation、momentumの取引PnL和、aggregate和、判断時刻重複なし、
  held-out所属、gate再判定を機械照合した。
- DATA-2026-0009は14銘柄各160,608本、重複0、欠測0、補間0で固定した。
- ETC、FIL、TRX、XLMは学習、予測、PnLに含めず封印を維持した。
- 成果物SHA-256:
  `6b1e4aa7d5e2f476b0a15701a13b7029a157a1ed562b1fca3758c79f7ad5c523`
- 作成者と承認者を分離する独立検証は未実施で、状態は`PENDING`である。

## 次に活かせること

premium・Fundingには順位情報がある可能性を捨てる必要はないが、同じsourceでtop1 lossやthresholdを
再調整してはならない。次の有効な検証は、今後収集するZOOMEX realtime orderbook・public trade・
liquidationを新しい時系列sourceとして固定し、premium signalがtop-of-book流動性と組み合わさった時だけ
機能するかを前向きに検証することである。これは即時バックテストではなく、注文なしのデータ収集から始める。
