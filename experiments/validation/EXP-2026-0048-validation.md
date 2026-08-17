# EXP-2026-0048 validation report

## 判定

`REJECTED`。この実験結果でpaper、shadow、live設定は変更しない。

これは研究runnerの独立した再実行結果を記録する検証報告であり、ZOOMEXの実約定、
証拠金、清算耐性、注文成立を承認するものではない。作成者による最終昇格承認ではない。

## 固定仕様

- 6銘柄: LINKUSDT、UNIUSDT、ADAUSDT、AVAXUSDT、NEARUSDT、AAVEUSDT
- 入力: `DATA-2026-0005`、ZOOMEX linear USDT perpetualの2時間足と8時間Funding
- 直前3回のFunding平均を銘柄間で順位付け
- 低い2銘柄をlong、高い2銘柄をshort
- 3 Fundingイベントごとに更新し、次の2時間足openへ遅延
- 1銘柄1側50 USDT、最大2 long・2 short、初期1000 USDT、reserve 200 USDT
- 基本費用: fee 0.06%、round-trip spread 0.10%、片道slippage 0.05%
- OOS: 2025-07-01〜2025-12-31 UTC

現在のFunding率を同じFunding時刻の順位計算へ使わないこと、評価期間開始時にflatであること、
Fundingを既存建玉へ一度だけ適用することをテストした。

## 結果

| ケース | 全期間 final equity | 全期間最大DD | OOS net PnL | OOS Funding | OOS fee | OOS最大DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 基本費用 | 432.96 | -57.74% | -77.09 | +0.27 | 22.93 | -17.31% |
| 費用2倍 | 242.60 | -75.85% | 0.00 | 0.00 | 0.00 | 0.00% |

基本費用でもFunding受取は価格損益と売買費用を補えず、cash controlを下回った。費用2倍では
開発期間中に資産が減少し、OOS期間に新規建玉が成立しなかったため、OOSのゼロ損益を優位性とは扱わない。

## 再現コマンド

```bash
PYTHONPATH=.:src uv run python scripts/run_exp_2026_0048.py
PYTHONPATH=.:src uv run pytest -q tests/src/crypt_ai/test_funding_carry.py tests/scripts/test_run_exp_2026_0048.py
```

成果物はGit管理外の `artifacts/EXP-2026-0048/summary.json` と監査CSVへ保存される。

## 未解決リスク

- 6銘柄は2026-08-15時点の固定選定で、point-in-time universeではない。
- Fundingの受信時刻、板、部分約定、証拠金、清算、注文拒否を再現していない。
- gross-neutralは市場β中立ではなく、共通価格変動と銘柄固有リスクが残る。
- 研究結果はZOOMEX公開履歴上の結果であり、実約定での有効性を示さない。
