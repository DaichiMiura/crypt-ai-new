# EXP-2026-0050 Validation Report

## 判定

- Research status: `REJECTED`
- Promotion status: `NOT_ELIGIBLE`
- paper / shadow / live変更: なし

AVAX/NEARペア平均回帰は取引数要件を満たしたが、retrospective holdoutで基本費用・費用2倍とも純損失となった。費用ゼロ診断でも負であり、閾値調整やpair差替えを行わず棄却する。

## 固定仕様

- Pair: AVAXUSDT / NEARUSDT、各50 USDTの等金額long/short。
- 過去720本のlog価格OLS。現在足を回帰推定から除外。
- 過去360本spreadのz-score。
- entry `|z| >= 2`、平均回帰exit `|z| <= 0.5`、stop `|z| >= 4`、168本時間切れ。
- 確定closeで判断し、次の2時間足openで両脚を同時に約定。
- parameter探索なし、1 variant。

## 結果

| 指標 | Development | Holdout base | Holdout stress |
|---|---:|---:|---:|
| net PnL | -3.69 USDT | -16.31 USDT | -26.40 USDT |
| return | -0.37% | -1.64% | -2.78% |
| max DD | -6.39% | -2.44% | -3.55% |
| leg entry / exit | 288 / 286 | 62 / 64 | 62 / 64 |
| Funding | -0.57 USDT | +0.34 USDT | +0.34 USDT |
| fee | 17.27 USDT | 3.78 USDT | 7.56 USDT |

holdoutのシグナルactionはAVAX short entry 16、AVAX long entry 15、z-stop exit 21、平均回帰exit 10、time stop 1だった。期間境界を跨ぐ建玉があるためholdout内のleg exitがentryより2多いが、全期間では350 entry / 350 exitで一致し、全entry/exit時刻で2銘柄が同時だった。

## 原因

主因は、想定したspreadの平均回帰より拡大が多かったことである。holdout exitの約3分の2が4標準偏差stopで、平均回帰exitは約3分の1に留まった。

固定シグナルをfee、spread、slippageすべて0として再会計しても、holdout net PnLは`-6.22 USDT`だった。基本費用との差約`-10.09 USDT`は費用影響だが、費用を完全に除いても価格・Funding収益源は負である。Fundingは`+0.34 USDT`で主因ではない。

## データ・未来参照

- ZOOMEX Global linear USDT perpetual、snapshot `DATA-2026-0005`。
- UTCの連続した同期2時間足と8時間Fundingを使用。
- OLSは時刻tより前720本、spread分布はtより前360本のみ。
- tのcloseはz-score観測だけに使用し、約定はt+1のopen。
- Fundingは決済時刻以前から保有する脚へ適用。
- 補間、欠測、重複、非正価格、時刻不一致を拒否。

## 会計・安全境界

- base: taker fee片道0.06%、round-trip spread 0.10%、slippage片道0.05%。
- stress: 上記をすべて2倍。
- pair gross 100 USDT、reserve cash 200 USDT、追加レバレッジなし。
- 両脚entry/exitの同期を成果物生成時に検査。
- 実注文、取引権限、秘密情報は使用していない。

## 制約

- AVAX/NEARは経済分類で固定した1 pairで、cointegrationを事前確認していない。
- holdoutは過去の別実験ですでに観測済みで、未観測OOSではない。
- 等金額はdollar-neutralだがβ-neutralではない。
- 公開Kline/Fundingは板、部分約定、証拠金、清算、注文拒否を表さない。
- ZOOMEX公開履歴上の研究結果であり、実約定の有効性は未検証。

## 結論

このpairと固定仕様をpaper/shadowへ進めない。AVAX/NEAR以外のpairをこのholdoutで探索すると多重比較になるため、同じ実験の修正としては行わない。別pairを検証するなら、選定規則、探索数、学習期間、未観測forward期間を別仮説として先に固定する。

## 再現

```bash
uv run python -m pytest tests/src/crypt_ai/test_pairs_mean_reversion.py tests/scripts/test_run_exp_2026_0050.py
uv run python scripts/run_exp_2026_0050.py
./scripts/verify.sh
```

成果物: `artifacts/EXP-2026-0050/summary.json`、両銘柄signals、base/stressのevents・equity。
