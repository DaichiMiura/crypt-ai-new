# Venue and Data Policy

## Purpose

現在の研究・paper/shadow対象をZOOMEX linear USDT perpetualとする。履歴バックテストと
リアルタイム観測で同じvenue・product・symbolを使い、venue proxyによる差を減らす。
ただしKline/Funding履歴と、実際の板・注文・口座・約定は同じ証拠ではないため、公開履歴で
得た結果と実運用で成立することを混同しない。

過去のBinance Global/Japan Spot実験は実験台帳に残すが、現在の標準経路ではない。
異なるvenue、Spot、perpetual、異なるquote assetの結果を同一系列として扱わない。

## Venue roles

| 役割 | Venue | 許される主張 |
|---|---|---|
| Historical research | ZOOMEX公開Kline/Funding履歴 | 値動き、戦略ロジック、Funding・費用耐性の候補を調べる |
| Target venue | ZOOMEX linear USDT perpetual | product、銘柄、板、注文制約、手数料、Funding、約定仕様を確認する |
| Paper / Shadow | ZOOMEX公開リアルタイムデータ | 実注文を送らず、シグナル、遅延、費用、想定約定を検証する |
| Live | 未承認 | 別方針、実装、検証、リスク設定、人間承認がそろうまで禁止する |

ZOOMEX履歴バックテストが良好でも、リアルタイムの利益や約定を意味しない。公開Klineは
板の厚さ、受信遅延、注文拒否、部分約定を表さない。低時間足、マーケットメイク、裁定など
microstructure依存の戦略はKlineだけで昇格させない。

## Pair and market mapping

実験ごとに、研究データのvenue・銘柄・quote assetと、運用対象のvenue・銘柄・quote assetを別々に記録する。

- `BTCUSDT`と`BTC/JPY`のような異なるペアを同一価格系列として扱わない。
- proxyを使う場合は、為替または他の変換系列、変換時点、変換式、残るbasis riskを記録する。
- Spot、Margin、Futuresを混在させない。混在させる場合は別実験として登録する。
- researchとtargetで同一contractが存在しない場合、proxyであることを仮説と検証報告の両方に明記する。
- シンボルの上場・廃止、最小数量、tick size、注文種別の差を都合よく除外しない。

## Data tiers

### Tier A: ZOOMEX historical research

ZOOMEXの公開APIを使う場合は、host、endpoint、取得時刻、対象期間、product category、
symbol、interval、ページング条件、タイムゾーン、timestamp単位をmanifestに保存する。
rawまたは再取得可能な取得条件と、変換後データのハッシュ・変換commitを追跡する。

KlineとFundingは別系列として取得し、Funding timestampをKline時刻へ暗黙に移動しない。
未確定Klineを除外し、内部UTCへ正規化する際にtimestamp単位を検査する。

参考:

- 各実験manifestに固定したZOOMEX API endpoint
- 取得時点のZOOMEX API・contract specification

### Tier B: ZOOMEX realtime calibration

同じZOOMEX productのリアルタイムデータを注文なしで収集し、履歴取得経路との重なりを
比較する。最低限、確定時刻、欠測、受信遅延、OHLC、Funding、可能ならbest bid/askと
spreadの差を記録する。

### Tier C: ZOOMEX shadow

ZOOMEXの実データを使って、承認済み戦略を注文直前まで実行する。注文は送信せず、
best bid/ask、板の厚さ、想定注文数量、価格刻み、最小数量・元本、データ遅延、想定約定、
手数料、Funding、未約定・拒否理由を記録する。

履歴バックテストとshadowの差は、利益だけでなく、シグナル時刻、spread、想定slippage、
fill rate、Funding、費用控除後PnL、drawdownで評価する。

ここでいうshadowは注文なしの実データ検証である。現在の許可は固定済みEXP-2026-0042
だけに適用し、他戦略のshadowやlive注文を包括的に許可するものではない。

## Fee and execution cost model

現行EXP-2026-0042は片道fee 0.06%を固定仮定として使うが、ZOOMEX全口座・全期間の
事実としてコードへ一般化しない。往復feeにspread、slippage、Fundingが別に加わる。

各実験では、少なくとも次を事前登録する。

- maker feeとtaker fee
- feeの支払通貨
- VIPレベル、割引、キャンペーンの有無
- fee scheduleの取得元と取得時刻
- spread、slippage、部分約定、注文拒否のモデル
- 不利なfee感度ケース

ZOOMEX shadowでは、取得時点の公開料金・contract specificationと、認証情報を使わずに
確認できる市場仕様をfee model versionとして固定する。実口座に依存するfee tierや割引を
一般値だけで確定しない。

参考:

- 各戦略設定に記録したfee model versionと取得元

## Promotion rule

履歴バックテストだけで`paper`から先へ昇格させない。少なくとも次を満たすまで、結果は
「ZOOMEX公開履歴上の研究結果」と表示する。

1. 仮説、pair mapping、データmanifest、fee modelを固定している。
2. fee、spread、slippage、Fundingを含むバックテストと独立検証が完了している。
3. realtime calibrationで履歴取得経路との差を確認している。
4. ZOOMEX shadowで実データ、遅延、想定約定、拒否条件、損益照合を確認している。
5. 未解決のvenue差と最悪ケースを検証報告に残している。

現時点ではliveを解禁しない。liveを検討する場合は、別変更として人間の明示承認、ロールバック手順、最小権限のAPI設定を追加する。
