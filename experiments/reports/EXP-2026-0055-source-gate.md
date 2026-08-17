# EXP-2026-0055 source-only gate結果

## 判定

XGBoostだけが事前登録した7条件をすべて満たし、未観測4銘柄を一度だけ評価する権限を得た。
Logisticは取引数とBrier条件だけを満たしたが、損益、銘柄分散、費用stressで棄却した。

この判定はZOOMEX公開履歴上のsource銘柄転移gateに限る。targetでの有効性、paper、shadow、
実約定を承認しない。

## 集計

| model | 往復 | base net PnL | 2x cost net PnL | 正の銘柄 | Brier | constant Brier | 判定 |
|---|---:|---:|---:|---:|---:|---:|---|
| Logistic | 217 | -98.4569 USDT | -166.7150 USDT | 1/9 | 0.224337 | 0.225858 | reject |
| XGBoost | 108 | +80.8359 USDT | +46.8644 USDT | 8/9 | 0.224405 | 0.225858 | pass |

XGBoostの銘柄別base net PnLは、LINK +17.58、UNI +4.05、AVAX -2.55、AAVE +1.34、
ETH +4.46、SOL +9.05、XRP +11.11、ADA +4.23、NEAR +31.56 USDTだった。
最大のNEAR寄与は正利益合計の37.85%で、事前登録上限40%以内だが余裕は小さい。

## 検証

- 51,471標本を作成し、除外判断時刻は0だった。
- 各foldでheld-out銘柄のlabelはfit、標準化、Platt校正、期待return推定に使用していない。
- 全取引を明細から再集計し、gross価格損益、Funding、fee、spread、slippageとnet PnLが一致した。
- 取引判断時刻の重複はなく、各foldの最低equityは200 USDT以上だった。
- BCH、LTC、DOT、DOGEはsource dataset・学習・評価に含まれず、metadataの封印状態は未開封のままである。
- 成果物: `artifacts/EXP-2026-0055-source-gate/summary.json`
- SHA-256: `60355b6a36c8c8020d813ed43f9717c735dc6ec2a04254250c3f5592b54836db`

## 解釈上の注意

XGBoostのBrier改善は約0.00145に留まり、確率予測力は弱い。AVAXは損失で、NEARへの利益集中も
上限に近い。source gateは「未知銘柄を確認する価値がある」という判定であり、普遍的edgeの
証明ではない。次は事前登録済みXGBoost、30日周期、730日source-only window、確率0.45、
4銘柄top差0.03を変更せず、封印targetを一度だけ評価する。
