# OSS and Dependency Policy

## Principle

当社は、すべてを自作するのではなく、成熟したOSSを積極的に利用する。自作するのは、会社固有の研究台帳、会計の正本、戦略別リスク予算、承認・停止の境界、OSSを接続する薄いアダプターを優先する。

## Candidate roles

| 役割 | 候補 | 採用時の注意 |
|---|---|---|
| crypto bot / dry-run / backtest | [Freqtrade](https://github.com/freqtrade/freqtrade) | Python、GPL-3.0。最初のpaper候補だが、hyperoptの過学習に注意 |
| event-driven research-to-live | [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | Rust/Python、LGPL-3.0。強力だが導入コストが高い |
| market making / paper | [Hummingbot](https://github.com/hummingbot/hummingbot) | Apache-2.0。マーケットメイク向けで、一般戦略の基盤とは限らない |
| exchange API adapter | [CCXT](https://github.com/ccxt/ccxt) | MIT。リスク管理や会計を提供しない接続部品 |
| research / parameter comparison | [VectorBT](https://github.com/polakowo/vectorbt) | Apache-2.0 with Commons Clause。大量探索による過学習に注意 |
| execution semantics reference | [ml4t/backtest](https://github.com/ml4t/backtest) | MIT。費用・スリッページが既定で無効になり得るため明示設定が必要 |

候補は固定的な推奨ではない。採用時点のライセンス、対応venue、リリース、issue、テスト、既知の制約を再確認する。

## Adoption checklist

新しいOSSを追加する前に、次を記録する。

- 目的と自作との差分
- リポジトリ、commitまたはリリース、ライセンス
- 保守状況、テスト、セキュリティポリシー、既知の制約
- 依存する外部サービスとデータの扱い
- 研究、paper、liveのどの環境で使うか
- 必要な権限、ネットワーク、秘密情報
- 置き換え・停止・ロールバック方法

## Usage rules

- バージョンと依存関係をlockfileで固定する。
- 実験結果にはOSSのリリースまたはcommitを含める。
- 依存OSSを更新したら、代表的なバックテストと会計照合を再実行する。
- OSSのライセンス条件を満たし、必要なNOTICEや著作権表示を保持する。
- 研究・paper環境で先に使い、live権限を持つプロセスへ直接追加しない。
- OSSの最適化、AI、注文機能を有効にしても、当社のリスクエンジンと人間承認を省略しない。
- 脆弱性や保守停止が判明した依存は、更新・隔離・置換・停止のいずれかを決める。

## Ownership boundary

OSSに任せてよいものは、データ取得、計算、シミュレーション、注文の通信、可視化である。次は当社が所有し、OSSの内部状態だけを正本にしない。

- 実験の事前登録と全試行履歴
- 会計台帳と損益照合
- グローバル上限と戦略別リスク予算
- 本番昇格、停止、再開の判定
- 人間の承認記録
