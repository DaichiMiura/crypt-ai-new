# EXP-2026-0051 Validation Report

## 判定

- Research status: `REJECTED`
- Promotion status: `NOT_ELIGIBLE`
- paper / shadow / live変更: なし

低ボラティリティ週次longはdevelopmentでは正だったが、retrospective holdoutで負となり、最低取引数、正の純損益、費用2倍stressの3基準を満たさなかった。良好な全期間損益を理由にholdout失敗を上書きしない。

## 固定仕様

- ZOOMEX linear perpetual固定6銘柄。
- 過去360本（30日）の2時間return標準偏差を年率化し、低い2銘柄を選択。
- 6銘柄の過去2160本（180日）return中央値が正の週だけlong。
- 84本（7日）ごとに更新し、次の2時間足openで各100 USDTを売買。
- 最大2 long、gross 200 USDT、reserve cash 200 USDT、1 variant。

## 結果

| 指標 | Development | Holdout base | Holdout stress |
|---|---:|---:|---:|
| net PnL | +205.93 USDT | -22.26 USDT | -24.45 USDT |
| return | +20.59% | -1.85% | -2.05% |
| max DD | -14.42% | -7.42% | -7.57% |
| entry / exit | 40 / 40 | 7 / 7 | 7 / 7 |
| Funding | -33.12 USDT | -2.24 USDT | -2.23 USDT |
| fee | 4.95 USDT | 0.83 USDT | 1.66 USDT |

holdout 26回の週次判定はaccepted 13回、market regime非正13回だった。選択回数はAAVE 11、ADA 7、AVAX 3、LINK 3、NEAR 2、UNI 0で、AAVEへの集中が強い。

## 損失原因

固定シグナルをfee、spread、slippageすべて0で再会計してもholdoutは`-20.07 USDT`だった。基本費用との差は約`-2.19 USDT`であり、主因は売買費用ではない。

費用ゼロ診断の内訳ではFundingが約`-2.24 USDT`、それ以外の価格・評価損益が約`-17.83 USDT`だった。イベント監査ではADAの100 USDT entryに対するexit notionalが約57.24 USDTまで減少し、NEARの利益などを相殺した。過去の低volは将来の急落耐性を保証しなかった。

developmentの利益が大きい一方でholdoutが負であるため、効果は期間依存またはregime依存で安定していない。180日filterもholdoutの半分をcashにしたが、保有を許した期間の銘柄固有下落を防げなかった。

## データ・時点整合性

- source: ZOOMEX Global public V3 REST API、snapshot `DATA-2026-0005`。
- 内部時刻はUTC。6銘柄の2時間足・Funding時刻を同期検査。
- volとregimeは時刻tの確定closeまでで計算し、約定はt+1 open。
- Fundingは決済時刻以前から保有する建玉だけへ適用。
- 補間、欠損、重複、非正価格、非有限Fundingを拒否。

## 会計・安全境界

- base: taker fee片道0.06%、round-trip spread 0.10%、slippage片道0.05%。
- stress: 上記をすべて2倍。
- 固定100 USDT/銘柄、最大2銘柄、追加レバレッジなし。
- allocation rejectionは0。実注文・認証情報・取引権限は使用していない。

## 制約

- holdoutは既存研究で観測済みで未観測OOSではない。
- 2026年固定6銘柄を過去へ適用し、point-in-time universeではない。
- 6銘柄の順位を一般的なlow-vol anomalyの証拠とは解釈できない。
- ZOOMEX公開履歴上の研究結果であり、実約定の有効性は未検証。

## 結論

この固定仕様をpaper/shadowへ進めない。AAVEやADAを事後除外すること、vol・regime窓をholdoutに合わせて変更することは行わない。次にlow-volを再検証する場合は、point-in-time universeと銘柄上限、独立したforward期間を別仮説で固定する必要がある。

## 再現

```bash
uv run python -m pytest tests/src/crypt_ai/test_low_volatility.py tests/scripts/test_run_exp_2026_0051.py
uv run python scripts/run_exp_2026_0051.py
./scripts/verify.sh
```

成果物: `artifacts/EXP-2026-0051/summary.json`、6銘柄signals、base/stress events・equity。
