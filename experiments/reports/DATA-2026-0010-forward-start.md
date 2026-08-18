# DATA-2026-0010 forward収集開始記録

## 結論

2026-08-18T05:06:59Zに、ZOOMEX公開WebSocketの90日forward収集を開始した。
ユーザーsystemd serviceはenabledかつactive/runningで、最初のsessionは
`20260818T050659Z-da24aa50694a`である。認証情報と注文endpointは使用していない。
開始確認時にuser lingerが無効だったため、ログアウト後の継続に必要な`miura`のlingerを有効化し、
`yes`を確認した。

## 固定版と安全境界

- 実行commit: `034e2f89230cfd61ff7f39bf8250627d86948751`。
- collector SHA-256: `f42097587d689d43d495dadcb12964c22c13e37bb6f008a02937eb25356af871`。
- `uv.lock` SHA-256: `47e5c858e3773bd3442628f19e701d4d88633f4cff43bfb3c545cd579155c279`。
- 24時間ごとにsessionを閉じ、manifestとgzip hashを確定して再起動する。
- 多重起動を`flock`で拒否し、空き容量20 GiB未満または固定hash不一致では開始しない。
- SIGINT・SIGTERMではgzipを閉じ、`shutdown_requested: true`の不完全manifestを残す。
- session directoryは0700、gzipは0600で生成された。

開始確認ではsource gzipが5秒間に0 byteから22,075 byteへ増加した。sealed targetの内容は開かず、
価格・数量・sideを表示していない。最初の24時間sessionが完了するまでmanifestは未確定であり、
この開始確認だけをcoverage達成やデータ品質合格とは扱わない。

## 起動失敗の保存

最初のsystemd起動は終了code 127で失敗した。原因は非対話systemd環境のPATHに`uv`がなく、
wrapperがcollectorを起動できなかったことである。forward session directoryは作られず、market dataも
保存されなかった。serviceを停止して`/home/miura/.local/bin/uv`へ固定し、commit `034e2f8`で修正後に
再起動した。失敗履歴はjournalと本記録から削除しない。

## 次回監査

最初の24時間session終了後に、status、全gzip展開、SHA-256、銘柄別message count、heartbeat coverage、
parse/schema/sequence error、切断・再接続、空き容量を確認する。sealed targetは許可済みの件数、端点、
byte数、hash、errorだけを確認し、featureやPnLは計算しない。
