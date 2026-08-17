# EXP-2026-0054 Development Report

## 結論

2025 developmentでは、固定Ridgeが費用・Funding込み`+7.372397419117 USDT`、
固定XGBoostが`-15.641081969383 USDT`だった。事前登録した「cent丸めで高い方、
同額または両方非正なら終了」という規則によりRidgeを選んだ。

これは2026 sealed holdoutを一度だけ開く機械的条件を満たしたという意味に限る。
手法の有効性、paper・shadow・liveへの昇格、リスク上限変更を承認しない。

## 固定仕様と実行順序

- 仮説commit: `a693355`
- XGBoost依存・採用記録commit: `a643974`
- development runner commit: `e42c022`、Pandas警告だけの修正: `49f75ba`
- snapshot: `DATA-2026-0006`
- train: 2022-02-01以降、exitが2025-01-01より前の17,036銘柄行
- development: 2025-01-01以降、exitが2026-01-01より前の5,836銘柄行
- 除外判断時刻: 0
- model family: RidgeとXGBoostの2個、各1設定
- entry閾値: gross log return `> log(1.0064)`
- size: 100 USDT、最大1 long、固定6時間保有

依存と仮説をcommitし、runnerを結果実行前に別commitしてからdevelopmentを実行した。
2025結果に合わせた特徴量、閾値、ハイパーパラメータの変更はしていない。

## Development結果

| model | net PnL | 完了往復 | max DD | 選択 |
|---|---:|---:|---:|---|
| Ridge | +7.3724 USDT | 15 | -1.0898% | yes |
| XGBoost | -15.6411 USDT | 58 | -2.9094% | no |

Ridgeの取引数は15往復に限られる。事前登録の30往復条件はsealed holdoutのhard gateであり、
development model選択条件ではないため、ここで事後追加してholdoutを止めない。一方、この小標本は
Ridgeの優位性を主張できない重要な不確実性として残す。

## 封印と再現性

- `sealed_holdout_opened`: `false`
- runnerは全CSVのevent timeと既登録SHA-256を検査したが、価格・Funding値は
  `2026-01-01T00:00:00Z`より前の行数だけを読み込んだ。
- 同じコード・入力で2回実行し、summary SHA-256は両方
  `87f4e0fb642f533a5ee7b611cca07b58604a6cc56c394d08e967d667f52eff86`だった。
- 成果物: `artifacts/EXP-2026-0054-development/summary.json`（raw価格と同様にgit対象外）。

## 会計と制約

- taker fee片道0.06%、round-trip spread 0.10%、slippage片道0.05%。
- quantityは取得時instruments-infoのstepへ切り下げ、minimum quantity/notionalを検査した。
- 保有区間内部のFundingだけを実績rateとmark open代理値で計上した。
- 公開Klineは板、部分約定、注文拒否、時変spreadを再現しない。
- ZOOMEX公開履歴上のdevelopment結果であり、実約定での有効性は未検証。

## 独立性

本書は実装担当が作成したdevelopment成果物であり、独立検証の承認ではない。
sealed holdout実行後の最終statusは、固定コード・データ・会計を別の検証成果物で再実行・照合して決める。

## 再現

```bash
uv run --group dev --group research python -m pytest -q tests/scripts/test_run_exp_2026_0054_development.py
uv run --group research python scripts/run_exp_2026_0054_development.py
sha256sum artifacts/EXP-2026-0054-development/summary.json
./scripts/verify.sh
```
