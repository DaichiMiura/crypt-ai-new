# Accounting Policy

## Current scope

現在の標準対象は、**ZOOMEXのUSDT建てlinear perpetual**である。許可される環境は
`research`、仮想約定だけを行う`paper`、注文を送信しない`shadow`に限る。実注文、
資金移動、取引権限を持つAPIキーの使用は許可しない。

現在paper/shadow承認されている`EXP-2026-0042`はlongのみ、初期equity 1000 USDT、
固定200 USDTロット、最大gross 400 USDT、追加レバレッジなしである。取引所商品が
レバレッジに対応していても、承認済み配分上限を証拠金倍率で拡大してはならない。

過去のBinance Global/Japan Spot実験は履歴として有効だが、現在のZOOMEX perpetualと
同じ会計系列へ混在させない。実験ごとにvenue、product、quote asset、会計モデルを
固定する。

## Time and precision

- 内部時刻はUTCとし、取引所timestampの単位と意味を取得記録へ残す。
- 金額、数量、価格、手数料、FundingはDecimalで扱い、丸め規則と桁数を設定に固定する。
- event time、receive time、ingest timeを区別し、損益計算の順序はevent timeを基本とする。
- 同一時刻はFunding、退出、追加注文、新規entry、markの順序を実験または固定戦略で明示する。
- 未確定足を確定足としてシグナルや約定判定に使わない。

## Perpetual position model

linear USDT perpetualの数量と損益はUSDT建てで扱う。

```text
entry notional       = quantity * entry execution price
current gross        = quantity * mark price
long unrealized PnL  = quantity * (mark price - entry execution price)
short unrealized PnL = quantity * (entry execution price - mark price)
```

複数回entryした建玉は数量と約定元本を加算し、平均取得価格を
`total entry notional / total quantity`で求める。決済時は実際の決済数量に対応する取得原価、
決済代金、手数料から実現損益を計算する。

研究用ポートフォリオは、割り当てた元本をcashから取り置く保守的なモデルを使える。
ただし、取引所固有のinitial margin、maintenance margin、清算価格、ADL、保険基金を
再現したことにはならない。追加レバレッジまたはshortをpaperへ昇格する場合は、それらを
扱う別の証拠と人間承認を必要とする。現行EXP-2026-0042はlong・追加レバレッジなしのため、
清算モデルを省略したまま実注文の根拠にはしない。

## Fees, spread, slippage and Funding

- entryとexitの各約定で、約定元本にfee rateを掛けてcashと損益から差し引く。
- spreadとslippageは売買方向に不利なexecution priceとして反映する。
- Fundingは取引所のFunding timestampごとに、その時点より前から保有する数量へ一度だけ適用する。
- 正のFunding rateではlongが支払い、shortが受け取る。負のrateでは逆とする。
- Funding notionalは登録した評価時点の価格と保有数量から計算し、新規entryより前にcashへ反映する。
- Funding履歴の欠測を暗黙に0とみなす場合は、データ仕様と実験の制約へ明記する。
- maker/taker、fee tier、Funding intervalの変更を固定値として一般化せず、実験または戦略版に記録する。

## Ledger events

最低限、次のイベントを追記型の監査記録として保存する。

- 入出金または初期paper資本
- 注文予約、注文拒否、取消
- entry、追加entry、exitの仮想約定
- fee、Funding
- markとequity

各イベントは、environment、venue、product、accountまたはpaper account、symbol、side、
event time、数量、execution/mark price、notional、feeまたはFunding、strategy versionを
追跡できなければならない。訂正は既存イベントの黙示的な上書きではなく、理由を記録する。

## Equity and PnL

- `equity = available cash + reserved position value + unrealized PnL`として、採用した担保モデルと整合させる。
- entry fee、exit fee、Fundingはnet PnLへ含める。
- 外部入出金またはpaper資本の変更は戦略損益として計上しない。
- 日次損益は日初equity、外部キャッシュフロー、日末equityから照合する。
- バックテスト終了時の未決済建玉はmark-to-marketとして明示し、決済済み損益と混同しない。

## Invariants

研究、paper、shadowのすべてで次を満たさない結果は無効とする。

- cash、建玉数量、取得原価、Funding、fee、equityが全イベントから再構築できる。
- 同一資金を複数建玉へ二重配分しない。
- 約定前の利益、同一足の未来価格、未発生Fundingを計上しない。
- feeとFundingを二重計上しない。
- Funding timestampの再処理で同じ支払いを重複させない。
- 追加entry後の全数量と平均取得原価が決済会計へ反映される。
- バックテストとpaper/shadowでシグナル時点、約定順序、費用、Funding、mark規則を共有する。
- 証拠金、清算、残高または価格が不明な状態では新規リスクを増やさない。

## Unsupported promotion claims

現在の会計実装とpaper/shadow結果は、ZOOMEXでの実約定、清算耐性、API注文の正確性、
税務処理を証明しない。実注文を検討する場合は、取引所仕様に基づく証拠金・清算・数量丸め・
部分約定・照合を別変更で実装し、人間の明示承認を得る。
