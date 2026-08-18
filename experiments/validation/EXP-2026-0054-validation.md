# EXP-2026-0054 Holdout Result Report

## 判定

- Provisional research status: `REJECTED`
- Promotion status: `NOT_ELIGIBLE`
- Independent validation: `PENDING`
- paper / shadow / live変更: なし

選択済みRidgeはsealed holdoutで`+12.7164 USDT`だったが、完了往復は事前登録最低30件に対して
4件だけだった。唯一の棄却条件は`holdout_completed_round_trips_below_30`であり、
少数取引の偶然を予測優位性として扱わず棄却する。

## 実行順序と封印

- hypothesis commit: `a693355`
- development gate記録: `2b66610`
- holdout runnerの結果前commit: `b6a21febd51bfcb92b984346fe9bfb415a6fd53d`
- snapshot: `DATA-2026-0006`
- sealed期間: 2026-01-01〜2026-08-01 exclusive
- refit train行: 22,876
- holdout行: 3,388（847判断時刻 x 4銘柄）
- train/holdoutの除外判断時刻: 0 / 0

runnerは成果物作成前に開封sentinelを排他的に作成した。完了後の二回目primary runが
`holdout output already exists`で拒否されることを確認した。

## Holdout結果

| arm | net PnL | 完了往復 | max DD | return |
|---|---:|---:|---:|---:|
| Ridge | +12.7164 USDT | 4 | -0.2228% | +1.2716% |
| Ridge・費用2倍 | +11.4174 USDT | 4 | -0.2540% | +1.1417% |
| 6h momentum | -191.7956 USDT | 545 | -21.0090% | -19.1796% |

Ridgeはcashとmomentumを上回り、費用2倍でも正、最大DDも-10%より良く、欠測・allocation
rejection・会計不一致は0だった。24/72/168時間block bootstrapのML-minus-momentum
95% CI lower boundもそれぞれ`0.0005387`、`0.0005725`、`0.0006245`で正だった。
それでも最低取引数を満たさないため、他条件を理由にtrade-count gateを変更しない。

## 費用と会計

Ridgeの内訳は次のとおり。

- gross price PnL: +14.0023 USDT
- Funding cash flow: +0.0131 USDT
- taker fee: -0.4871 USDT
- spread: -0.4059 USDT
- slippage: -0.4059 USDT
- turnover: 811.8641 USDT

各tradeについて`gross price PnL + Funding - fee - spread - slippage = net PnL`を
成果物から独立再計算し、arm合計と一致した。Ridgeと費用2倍armの取引時刻・銘柄も一致した。

## 少数取引と集中

4件のうち3件はAAVEUSDT、1件はUNIUSDTで、LINKUSDTとAVAXUSDTは0件だった。
さらに3件が2026-02-06の連続した6時間区間に集中し、この3件で`+14.9776 USDT`、
残る2026-06-25のAAVE 1件で`-2.2612 USDT`だった。観測された利益は独立した30件以上の
反復ではなく、単一日・2銘柄への集中が強い。

全銘柄行でのgross-return MAEは`0.01331`、directional accuracyは`53.04%`だった。
accuracyだけでは採用せず、取引規則を通過した標本数と費用込み損益を優先する。

## 制約と安全境界

- ZOOMEX公開Kline/Fundingは板、部分約定、注文拒否、時変spreadを再現しない。
- quantityは取得時instruments-infoのstepへ切り下げたが、過去仕様変更は完全再現していない。
- 固定4銘柄・7か月・4取引の結果であり、他期間や実約定へ一般化しない。
- thresholdを下げる、特徴量を変える、別modelへ戻す、期間を延ばす等の事後救済を本IDで行わない。
- 実注文、APIキー、資金移動、paper/shadow設定変更はない。
- ZOOMEX公開履歴上の結果であり、実約定での有効性は未検証。

## 独立性

本書は実装担当による結果整理であり、独立検証者の承認ではない。Research statusは固定gateから
機械的に`REJECTED`となるため運用昇格は行わない。独立検証では、固定commitからの再現、
時点整合性、数量丸め、Funding、費用分解、bootstrapを別成果物として照合する。

## 成果物

- primary summary SHA-256:
  `c9e5c93d2c6d5223ea4f0ac836f3ea391d53408b57fe9a150b71c594e80f49f7`
- opening sentinel SHA-256:
  `041ee3f02e800e900966424d2d2d6a1c6c81c11ee00d5a791ac301282a08cec1`
- local primary artifact: `artifacts/EXP-2026-0054-holdout/summary.json`
