# ZOOMEX realtime microstructure前向き収集

## 目的

EXP-2026-0058ではpremium・Fundingに正の横断rank ICが見えたが、top1の費用控除後損益へ
変換できなかった。過去Klineへ現在の板を混ぜず、新しい未観測期間としてbest bid/ask、
公開約定、清算を注文なしで収集する。

ZOOMEX公式WebSocketはUSDT perpetual用の公開endpointを持ち、認証なしでtopic購読できる。
`orderbook.1`はbest bid/ask、`publicTrade.{symbol}`は約定時刻・taker side・数量・価格・trade ID、
`allLiquidation.{symbol}`は500ms間隔で全清算を配信する。

- [WebSocket接続](https://zoomexglobal.github.io/docs/v3/ws/connect)
- [Orderbook](https://zoomexglobal.github.io/docs/v3/websocket/public/orderbook)
- [Public Trade](https://zoomexglobal.github.io/docs/v3/websocket/public/trade)
- [All Liquidation](https://zoomexglobal.github.io/docs/v3/websocket/public/all-liquidation)

## 収集境界

- endpointは`wss://stream.zoomex.com/v5/public/linear`へ固定する。
- API key、cookie、private topic、注文・口座endpointを使用しない。
- source/context 10銘柄とsealed target 4銘柄のraw eventを別gzip NDJSONへ保存する。
- 各eventへUTC受信時刻、`monotonic_ns`、connection ID、受信したJSON payloadを付ける。
- subscribe応答、pong、未知control messageはmarket eventと分けてcontrol fileへ保存する。
- 20秒ごとにapplication heartbeatを送り、切断時はconnection IDを変えて再購読する。
- gzip書込みでbackpressureを掛け、メモリ上でmarket eventを捨てない。切断・再接続・sequenceの
  逆行またはgapはmanifestへ残し、欠測を補間しない。

板50段は14銘柄の長期保存量が大きいため使わず、`orderbook.1`へ固定する。これはspreadと
best-size imbalanceを観測できるが、50段のmarket impactやqueue positionは表さない。

## 封印と使用条件

ETC、FIL、TRX、XLMのraw eventは`sealed-target-events.jsonl.gz`へ分離する。収集中に表示するのは
message数、端点、byte数、hash、再接続、parse errorだけで、価格・数量・sideは表示しない。

DATA-2026-0010は最低90 calendar daysを収集し、次の全条件を満たすまでML featureやPnLを作らない。

- source/context全銘柄で日次heartbeat coverage 95%以上。
- 受信時計がUTCで、exchange timestampとreceive timestampの差を計算可能。
- orderbook snapshot後のdeltaだけを有効とし、再接続後は新snapshotまで状態を使わない。
- duplicate trade ID、sequence gap、parse error、切断期間を日別・銘柄別に集計する。
- 収集コードcommit、session manifest、raw hash、websockets版を固定する。

最初の短時間接続はschema・購読・書込み確認用`SMOKE_ONLY`で、研究結果や90日coverageへ含めない。

## 将来の実験候補

90日収集後に別IDで、premium crowding signalにspread、best-size imbalance、aggressive buy/sell
flow、liquidation imbalanceを追加する仮説を事前登録する。期間やfeatureはtarget値を開く前に固定し、
DATA-2026-0010自体は戦略・paper・shadow・liveを承認しない。
