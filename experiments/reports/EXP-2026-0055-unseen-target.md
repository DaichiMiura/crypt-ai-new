# EXP-2026-0055 未観測4銘柄評価

## 結論

事前登録済みXGBoostは、BCH、LTC、DOT、DOGEの未観測期間で取引条件を1回も満たさず、
`REJECTED`となった。PnL 0は利益ではなく、取引可能な確信を検出できなかった結果である。

ZOOMEX公開履歴上の結果であり、実約定での有効性は未検証である。paper、shadow、liveへの
昇格資格はない。

## 固定評価結果

| strategy | 往復 | net PnL | 2x cost PnL | max drawdown |
|---|---:|---:|---:|---:|
| adaptive XGBoost | 0 | 0 USDT | 0 USDT | 0% |
| 6時間momentum | 592 | -225.4504 USDT | 対象外 | -23.77% |

Target標本は3,388行、判断時刻は847、欠測・補間による除外は0だった。XGBoostは30日ごとに
8回再学習し、各回ともtarget labelを使わず、source9銘柄だけの直近730日を時間順80/20で
fitとPlatt calibrationへ分離した。

## 0取引の原因

固定entryは次の3条件を同時に要求した。

1. 4銘柄中topの`P(UP) >= 0.45`
2. topと2位の差`>= 0.03`
3. source校正期間から求めた期待gross return`> 0.0064`

結果は、確率gate通過0回、margin gate単独通過46回、期待return gate通過0回、同時通過0回。
全3,388予測の最大確率は0.41964、各判断のtop期待gross return最大値は0.25730%であり、
固定0.64%へ届かなかった。

Target event率は25.77%。Brier scoreは0.190970で、target event率を知った定数予測の
0.191278より0.000308だけ良かった。予測確率とeventの相関は0.097、gross returnとの相関は
0.049だった。弱い順位情報はあるが、取引費用を超えるedgeとしては小さすぎる。

## なぜsource gateから移転しなかったか

- source leave-one-asset-outでは固定過去期間の校正確率がgateを超えたが、2026 rolling校正では
  event率が約32.1%から27.4%へ低下し、Platt校正後の確率が0.42未満へ圧縮された。
- return magnitudeを直接当てずevent分類へ変えたため、EXP-2026-0054の「回帰値が0へ縮む」問題は
  別形式になったが、経済gateを超える強さがないという本質は変わらなかった。
- Brier改善と相関はいずれも小さく、分類品質のわずかな改善を利益可能性と同一視できない。
- 0.45や0.64%をtarget確認後に下げるとholdout最適化になるため行わない。

## 完全性

- 成果物SHA-256: `7d7d25d72157ed07a14823e15c04adc60bf3ad0f7595da48590985064dcdd441`
- 開封sentinel SHA-256: `b6059dc05def2ae94dd17194a51b2f4421130df180adcb62760c609c7eb50c9b`
- Source-only model auditにtarget4銘柄が含まれないことを再検査した。
- 全momentum取引を明細から再集計し、gross、Funding、fee、spread、slippageとnet PnLが一致した。
- 条件変更、再選択、target別threshold、target labelによるfine-tuningは行っていない。

## 次の扱い

このexperimentは閉じる。targetを再利用した閾値調整は行わない。次の仮説を作る場合は、別の
未観測期間または新しい封印universeを確保し、分類確率だけでなく費用控除後return magnitudeを
時系列cross-fittingで推定する設計を、結果を見る前に新しいIDで登録する。
