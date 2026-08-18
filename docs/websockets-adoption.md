# websockets採用記録

## 採用範囲

`websockets` 16.1.1をresearch dependencyとして、ZOOMEX公開WebSocket市場データの受信だけに使う。
WebSocket framing、TLS接続、keepalive、backpressure、正常close処理を自作せず、当リポジトリは
topic固定、event検査、受信時刻、永続化、hash、封印を所有する。

## 確認事項

- project: [python-websockets/websockets](https://github.com/python-websockets/websockets)
- release: 16.1.1
- license: BSD-3-Clause
- Python: 3.10以上。リポジトリの3.12要件を満たす。
- maintenance: 2026-07-17 release、公式documentationとsecurity policyがある。
- quality: RFC 6455/7692準拠を目的とし、projectは継続的testとbranch coverageを掲げる。
- permission: 公開market endpointへのoutbound TLSだけ。秘密情報・注文・口座権限なし。

参照:

- [PyPI release and license](https://pypi.org/project/websockets/)
- [asyncio client API](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html)

## 制約と停止

libraryはZOOMEX payloadの意味、sequence完全性、disk durability、封印、研究妥当性を保証しない。
受信JSONは未信頼入力として検査し、size上限を設ける。proxy自動検出は使わず、OS標準TLS検証を
無効化しない。

問題時はcollectorを停止し、raw sessionを`INCOMPLETE`として残す。依存の置換または撤去はcollector
adapterとresearch dependencyだけを戻し、会計・risk・executionへ影響させない。liveプロセスには
この依存を追加しない。
