# EXP-2026-0052 Validation Report

## 判定

- Research status: `REJECTED`
- Promotion status: `NOT_ELIGIBLE`
- paper / shadow / live変更: なし

固定ロジスティック回帰によるEXP-2026-0042のtrade-quality filterは、2025 retrospective evaluationで元戦略を大幅に下回った。最大drawdownだけは縮小したが、ほぼ全候補を見送った結果であり、損益改善と最低取引数を満たさない。

## 固定仕様

- EXP-2026-0042と同じZOOMEX linear perpetual固定4銘柄、30日相対momentum、週次top2、最下位転落退出。
- 特徴量は30日momentum、市場中央値momentum、cross-sectional rank、過去84本volatilityの4個。
- targetは次足openから84本後openまでのreturnが固定往復費用0.32%を上回るか。
- L2 logistic回帰、800 iterations、learning rate 0.05、L2 0.01、閾値0.50、最低80標本、1 variant。
- 各判断時点以前にtarget終端openが到来した標本だけで再学習。標準化も当該trainだけで計算。
- MLは候補の採否だけを返し、確率による増額は行わない。

## 2025 retrospective結果

| 指標 | EXP-0042 baseline | ML filter | ML・費用2倍 |
|---|---:|---:|---:|
| net PnL | +235.34 USDT | -32.69 USDT | -33.88 USDT |
| return | +17.38% | -3.21% | -3.38% |
| max DD | -10.11% | -5.87% | -5.99% |
| entry / exit | 19 / 21 | 2 / 2 | 2 / 2 |
| Funding | -7.75 USDT | -1.08 USDT | -1.08 USDT |
| fee | 4.97 USDT | 0.46 USDT | 0.92 USDT |

判定理由は次の3件である。

- completed leg round tripsが最低10に対して2。
- ML net PnLがbaselineを上回らない。
- 費用2倍stressのnet PnLが正でない。

## 失敗原因

2025年にEXP-0042が選んだ候補は42件あり、その84本target labelは20件が正（47.62%）だった。一方、モデル確率は全銘柄で平均0.4701、最大0.5128と0.50付近へ集中し、baseline候補を3件（7.14%）しか採用しなかった。採用したLINK 1件とUNI 2件のtarget labelはすべて負だった。

全2025予測のlog lossは0.6888、realized positive rateは41.67%だった。単純な線形4特徴量は、候補の次週成否を分ける有効な追加情報を示さなかった。閾値を事後に下げれば取引数は増えるが、結果を見た後の最適化になるため本実験では行わない。

最大DDの改善は、平均grossがbaseline 135.57 USDTから11.51 USDTへ低下したことによる曝露削減の影響が大きい。現金比率を増やすだけならMLを必要としないため、これを予測優位性とは解釈しない。

## データ・時点整合性

- source: ZOOMEX Global public V3 REST API、snapshot `DATA-2026-0004`。
- 内部時刻はUTC。4銘柄の確定2時間足とFundingを同期検査。
- 特徴量は判断足closeまで、約定は次足open。
- target終端indexが現在の判断index以下の標本だけをtrainへ投入するテストを追加。
- 標準化平均・標準偏差と係数を各時点のtrainだけから再計算。
- model、特徴量、予測確率、train標本数、realized labelを監査CSVへ保存。

## 会計・安全境界

- base: taker fee片道0.06%、round-trip spread 0.10%、slippage片道0.05%。
- stress: 上記をすべて2倍。
- EXP-2026-0042と同じ固定200 USDT/銘柄、最大2 long、reserve 200 USDT。
- allocation rejectionは0。実注文、認証情報、取引権限は使用していない。
- ML学習不能、単一class、非有限特徴量では新規候補を拒否する。

## 制約

- 2025年は既存研究で観測済みで、未観測OOSではない。
- 固定4銘柄の少数標本で、他銘柄・他期間への一般化を示さない。
- targetは84本open-to-openの費用近似で、途中の最下位退出とFundingを直接表さない。
- ZOOMEX公開履歴上の研究結果であり、実約定の有効性は未検証。

## 結論

このモデルをEXP-2026-0042のpaper/shadow経路へ追加しない。確率閾値、特徴量、targetを2025結果へ合わせて調整しない。次にMLを試す場合は、単なる採否分類よりも、未来の実現volatilityを予測して決定論的なposition size上限を下げる研究を、別ID・未観測forward評価で事前登録する方が検証しやすい。

## 再現

```bash
uv run python -m pytest tests/scripts/test_run_exp_2026_0052.py tests/scripts/test_run_exp_2026_0042.py
uv run python scripts/run_exp_2026_0052.py
./scripts/verify.sh
```

成果物: `artifacts/EXP-2026-0052/summary.json`、walk-forward predictions、4銘柄ML signals、baseline・ML・stress events/equity。
