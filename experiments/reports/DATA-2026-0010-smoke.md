# DATA-2026-0010 realtime collector smoke結果

## 結論

ZOOMEX公開WebSocketから、注文・認証なしで14銘柄のbest bid/ask、公開約定、全清算topicを
source、sealed target、controlへ分離保存できた。2回目の30秒sessionは`SMOKE_ONLY_COMPLETE`で、
90日forward coverageには算入しない。実注文は0である。

## Session履歴

| session | 結果 | 内容 |
|---|---|---|
| `20260818T031053Z-65671c39b132` | 不完全 | BTC・ETH・SOL orderbook payloadの内部symbol aliasを84件schema errorとして検出 |
| `20260818T031347Z-3e2447410873` | pass | 固定aliasを許可し、parse/schema/sequence error 0 |

最初の失敗sessionは削除・上書きせず、そのまま保持した。payload `s`はrawのまま保存し、topicの
BTCUSDT、ETHUSDT、SOLUSDTに対するBTC2USDT、ETH2USDT、SOL2USDTだけを固定aliasにした。

## Passing smoke

- 接続1、再接続0、application heartbeat 1。
- source market event 1,035件。
- sealed target market event 671件。
- control event 4件。
- parse error 0、schema error 0。
- snapshot前orderbook delta 0、非増加update 0、sequence gap 0。
- duplicate trade ID 0。
- 全14銘柄の`orderbook.1`を受信した。
- 30秒内にall-liquidation eventはなく、無イベントを合成していない。
- authentication used false、orders sent false、target content displayed false。

## 保存と完全性

session directory:
`data/raw/DATA-2026-0010/smoke/20260818T031347Z-3e2447410873`

| artifact | records | SHA-256 |
|---|---:|---|
| source gzip | 1,035 | `0ffcdde82279e94d3a94dd40bda199a1bc6109e132f4f366668d31bf69821701` |
| sealed target gzip | 671 | `f0775b221810fbf0ffe1ce2056356126fd1279899cad4303621e2503583f8715` |
| control gzip | 4 | `e88511c294ebbeed68153d25d5a1e7a460c6968bac4502939a79ff162520e555` |
| session manifest | — | `5f37d8f0545fd5a7c7859582a4c8b00a2bb28265845c97b34d7a20603988e4c8` |

全gzipの展開検査に成功した。directoryは0700、gzipとmanifestは0600。manifestのcollector commitは
`671ab2a0b52a0e4ba01c878a584a1dc4982cbcb6`、websocketsは16.1.1である。

## 未開始事項

90日forward collectionはまだ開始していない。smoke成功はデータ収集経路だけの証拠であり、
MLの有効性、paper、shadow、liveを承認しない。常時収集を開始する場合は、process監視、disk容量、
日次manifest監査、停止・再開手順を固定してからcollectorを有効化する。
