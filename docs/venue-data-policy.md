# Venue and Data Policy

## Purpose

実際の運用対象を Binance Japan Spot とし、履歴の短さを補うために Binance Global Spot の公開データを研究用の代理データとして利用する。代理データで得た結果と、運用対象の取引所で成立することを混同しない。

この文書でいう「Binance Global」は、利用したデータの正確なホスト、API、データセットを実験のmanifestに記録したうえでの略称である。同じブランド名であっても、取引所、通貨ペア、板、手数料、注文制約が同一であることを仮定しない。

## Venue roles

| 役割 | Venue | 許される主張 |
|---|---|---|
| Research proxy | Binance Global Spotの履歴データ | 値動きの仮説、戦略ロジック、費用耐性の候補を調べる |
| Target venue | Binance Japan Spot | 実際の銘柄、板、注文制約、手数料、約定を確認する |
| Shadow | Binance Japanのリアルタイムデータ | 注文を送信せず、遅延・スプレッド・流動性・想定約定を検証する |
| Live | Binance Japan | 本方針、リスク設定、検証証拠、人間承認が別途そろった場合だけ検討する |

Globalのバックテストが良好でも、Binance Japanでの利益や約定を意味しない。特に板情報、短期の価格形成、マーケットメイク、裁定、低時間足の戦略はvenue固有性が強いため、Globalを代理にした結果だけで候補昇格してはならない。

## Pair and market mapping

実験ごとに、研究データのvenue・銘柄・quote assetと、運用対象のvenue・銘柄・quote assetを別々に記録する。

- `BTCUSDT`と`BTC/JPY`のような異なるペアを同一価格系列として扱わない。
- proxyを使う場合は、為替または他の変換系列、変換時点、変換式、残るbasis riskを記録する。
- Spot、Margin、Futuresを混在させない。混在させる場合は別実験として登録する。
- Japan側に同一ペアが存在しない場合、proxyであることを仮説と検証報告の両方に明記する。
- シンボルの上場・廃止、最小数量、tick size、注文種別の差を都合よく除外しない。

## Data tiers

### Tier A: Global historical research

Binanceの公式公開データを使う場合は、取得URL、データセット名、取得時刻、対象期間、チェックサム、タイムゾーン、タイムスタンプ単位をmanifestに保存する。rawデータを保存し、変換後データとのハッシュと変換commitを追跡する。

公式の公開データは日次・月次ファイルで提供され、SpotのKlineやaggTradeを含む。Spotデータは2025年以降にマイクロ秒のタイムスタンプを含み得るため、内部UTC時刻へ正規化する際に単位を検査する。

参考:

- [Binance Public Data](https://github.com/binance/binance-public-data)
- [Binance Spot API documentation](https://developers.binance.com/en/docs/products/spot/rest-api)

### Tier B: Japan calibration

Japanの公開市場データを注文なしで収集し、Global proxyとの重なりを比較する。最低限、リターン、ボラティリティ、価格basis、出来高、スプレッド、データ欠損、受信遅延を比較し、proxyの差を定量化する。

### Tier C: Japan shadow

Japanの実データを使って、承認済み戦略を注文直前まで実行する。注文は送信せず、best bid/ask、板の厚さ、想定注文数量、価格刻み、最小注文数量、データ遅延、想定約定、手数料、未約定理由を記録する。

GlobalのバックテストとJapan shadowの差は、利益だけでなく、シグナル時刻、価格basis、spread、想定slippage、fill rate、cost-adjusted PnL、drawdownで評価する。

ここでいうshadowは、研究段階で行う注文なしの実データ検証である。`validation-policy.md`の昇格段階としての`shadow`を解禁したことや、live注文を許可したことを意味しない。

## Fee and execution cost model

買い・売りともに0.1%という値は、最初の実験で使う仮定値としては妥当だが、固定された事実としてコードへ埋め込まない。片道0.1%なら、単純な往復でも手数料だけで約0.2%となり、spreadとslippageが別に加わる。

各実験では、少なくとも次を事前登録する。

- maker feeとtaker fee
- feeの支払通貨
- BNB割引、VIPレベル、キャンペーンの有無
- fee scheduleの取得元と取得時刻
- spread、slippage、部分約定、注文拒否のモデル
- 不利なfee感度ケース

Binance Japanのshadow開始前には、アカウントに適用される料金表と実約定のcommissionを確認し、確認結果をfee model versionとして固定する。公式の手数料ページはログイン後の料金・履歴確認を案内しており、Spot APIでもアカウントや約定に関する手数料情報を扱うため、公開記事の一般値だけを正本にしない。

参考:

- [Binance Spot Trading Fee Rate](https://www.binance.com/en/fee/schedule)
- [Binance Spot API repository](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md)

## Promotion rule

Global proxyの結果だけで`paper`から先へ昇格させない。少なくとも次を満たすまで、結果は「Global proxy上の研究結果」と表示する。

1. 仮説、pair mapping、データmanifest、fee modelを固定している。
2. 費用・spread・slippageを含むGlobalバックテストと独立検証が完了している。
3. Japan calibrationでproxyとの差を確認している。
4. Japan shadowで実データ、遅延、想定約定、拒否条件、損益照合を確認している。
5. 未解決のvenue差と最悪ケースを検証報告に残している。

現時点ではliveを解禁しない。liveを検討する場合は、別変更として人間の明示承認、ロールバック手順、最小権限のAPI設定を追加する。
