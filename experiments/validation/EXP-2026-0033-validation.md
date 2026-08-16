# EXP-2026-0033 検証レポート

## 結論

EXP-2026-0023で使った2時間足ロングを、現物データが取得できた4銘柄へ均等配分し、EXP-2026-0032のベーシス・ヘッジを初期資産の5%、10%、20%相当で加えた。今回の単一期間バックテストでは、ヘッジ枠を増やすほど `long_only` より最終資産と最大ドローダウンが改善した。

一方、最も良かった `hedge_20pct` でも最終equityは **952.10 USDT** にとどまり、4年間・年率10%複利の基準 **1464.10 USDT** を大きく下回った。ベーシス・スリーブ自体も費用とFundingを差し引くと小幅なマイナスである。この結果だけでpaper、shadow、liveへ昇格させない。

判定は **INCONCLUSIVE / NOT_ELIGIBLE** とする。ヘッジがドローダウンを緩和する補助部品として機能する可能性はあるが、収益戦略としての有効性は示されていない。

## 固定した条件

- 評価期間: 2022-02-01〜2025-12-31 UTC、2時間足
- 共通ユニバース: LINKUSDT、UNIUSDT、AVAXUSDT、AAVEUSDT
- 初期資産: 1000 USDT、予備資金: 200 USDT
- ロング: EXP-0012由来のEXP-0023条件（entry 660本、exit 240本、regime 2400本、ATR 240本、倍率3.0）
- ベーシス: `perp_mark / spot_close - 1` が +0.50%以上でentry、+0.10%以下または360本経過でexit
- ベーシス1ペアの片脚元本: 24 USDT。費用込みで各armの枠内に収めるため固定した
- arm: `long_only`、`hedge_5pct`（basis枠50、最大1ペア）、`hedge_10pct`（100、最大2ペア）、`hedge_20pct`（200、最大4ペア）
- ロングとbasisは固定スリーブで会計し、スリーブ間の利益・損失再配分と複利は行わない
- spot手数料0.1%、perpetual手数料0.06%、spread・slippageはEXP-0032と同じ仮定

## 結果

| arm | 最終equity | 純損益 | 最大DD | long_onlyとの差 | basis純損益 |
| --- | ---: | ---: | ---: | ---: | ---: |
| long_only | 936.39 | -63.61 | -6.361% | — | — |
| hedge_5pct | 940.22 | -59.78 | -6.006% | +3.83 | -0.14 |
| hedge_10pct | 944.15 | -55.85 | -5.616% | +7.76 | -0.19 |
| hedge_20pct | 952.10 | -47.90 | -4.821% | +15.71 | -0.19 |

`max_drawdown` の差は、たとえば `hedge_20pct` で `long_only` より **+1.540ポイント**（DDが浅くなる方向）だった。これはbasisの収益性を意味せず、ロング資金を減らしたこと、basisスリーブの値動きが相対的に小さいこと、予備資金を固定したことの複合結果である。

## 監査上の確認

- ロングは4銘柄へarmごとに均等配分した。
- 現物・perpetual・mark・Fundingの共通期間と時刻を検査した。
- 補間行はentryに使わない既存データ品質条件を継承した。
- ベーシススリーブのentry/exitは、`hedge_5pct` が24/24、`hedge_10pct` と `hedge_20pct` が27/27だった。
- `hedge_20pct` のベーシスFundingは -0.10 USDT、手数料は2.07 USDT、最大両脚元本は96 USDTだった。
- 最終時点に未決済のロング・basis建玉はなかった。

成果物は `artifacts/EXP-2026-0033/summary.json` と各armのevents/equity CSVである。実行コードは `scripts/run_exp_2026_0033.py`、事前登録は `experiments/registry/EXP-2026-0033-hypothesis.yaml` に保存した。

## 次の候補

1. ヘッジを収益源とみなさず、ロングのドローダウン抑制部品として、別期間・別銘柄で再現性を確認する。
2. basis entryが費用を上回る条件か、保有期間・Funding・約定可能性を事前登録して追加検証する。
3. paperへ進める場合も、まず `hedge_20pct` をそのまま採用せず、実データのspot/perpetual同時約定とFunding・手数料をshadowで確認する。

## 昇格判定

- research_status: `INCONCLUSIVE`
- promotion_status: `NOT_ELIGIBLE`
- paper: 未承認
- shadow: 未承認
- live: 未承認
