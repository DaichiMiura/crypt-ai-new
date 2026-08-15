# Research Policy

## Rule: hypothesis before results

各研究は一意な `EXP-YYYY-NNNN` を持ち、主要な結果を見る前に [templates/hypothesis.yaml](../templates/hypothesis.yaml) を埋める。

最低限、次を事前登録する。

- 検証可能な仮説
- 優位性が存在し得る経済的・市場構造上の理由
- 対象市場、銘柄、時間足、期間
- 使用可能な特徴量と意思決定時点
- ベースライン
- 主要評価指標と棄却条件
- 想定取引費用と流動性制約
- 失敗しそうな相場環境
- 許容する探索範囲と試行回数

## Methodology design

Penfold本の原則を、特定手法の採用理由ではなく設計チェックとして使う。

- セットアップとトレードプランを分ける。
- エントリー、損切り、決済、取引しない条件を機械的に記述する。
- 勝率ではなく、手数料・スプレッド・スリッページ控除後の期待値を評価する。
- パラメータ数、探索回数、最適化に使った期間を記録する。
- 資金管理を手法の後付けにせず、戦略登録時にリスク予算とサイズ計算を決める。
- 相場環境、流動性、注文失敗が期待値を壊す条件を明記する。

## Experiment ledger

成功・失敗を問わず、実行した実験を `experiments/registry/` に追記する。既存記録を成功例だけに選別、上書き、削除してはならない。訂正は元記録を残し、訂正理由と時刻を追加する。

記録には以下を含める。

- 仮説IDと親実験ID
- コードcommit
- データスナップショット
- 全パラメータと乱数seed
- 実行環境と再現コマンド
- 試したバリエーション数
- 費用・スリッページ・約定モデル
- 結果と棄却理由
- 既知の制約、欠測、例外

## Data rules

- データ取得元、取得時刻、ライセンス、タイムゾーン、変換を記録する。
- 1回のデータ利用に1つのmanifest IDを付け、[データmanifest](../templates/data-manifest.yaml)からrawと変換後データを再取得できるようにする。
- シグナル時点で利用不能だった値を使わない。
- ローソク足の終値でシグナルを作り、同じ終値で無条件に約定させない。
- 上場廃止、取扱終了、欠測期間を都合よく除外しない。
- 取引所、手数料、ティックサイズ、最小注文数量の履歴変更を扱う。
- データ修正は破壊的に行わず、原本と変換後の版を追跡する。

## Venue and proxy rules

- 実際の運用対象はBinance Japan Spotとして登録し、Binance Global Spotの履歴データは研究用proxyとして明示する。
- Global proxyの結果から、Binance Japanでの利益、流動性、約定率、spread、手数料を推定したことにしない。
- 研究venue、実行venue、market type、symbol、quote asset、pair mapping、proxyの限界を仮説とmanifestに記録する。
- `BTCUSDT`と`BTC/JPY`のような異なるpairは同一系列として扱わず、変換系列、変換時点、basis riskを記録する。
- 板、短期microstructure、裁定、market makingなどvenue依存性の強い手法は、Global proxyだけで採用候補にしない。
- Globalの候補は、Japanのリアルタイムデータを使うshadowで、注文なしのfill simulation、遅延、spread、流動性、拒否条件を検証する。
- 代理データを使った実験の結論には、必ず「Global proxy上の結果であり、Binance Japanでの有効性は未検証」と書く。

手数料は実験の一部である。片道のmaker/taker fee、feeの支払通貨、割引・キャンペーン、spread、slippage、部分約定、注文拒否を記録する。Binance Japanのshadow開始前に、アカウントに適用される料金と実約定のcommissionを確認し、fee model versionとして固定する。詳細なvenue、データ階層、昇格条件は [venue-data-policy.md](venue-data-policy.md) に従う。

## Research integrity

- アウトオブサンプル領域を見た後は、それをインサンプルに戻さない。
- パラメータ、銘柄、期間、指標の探索数を報告する。
- 最良結果だけでなく分布、感度、負の結果を示す。
- 論文や第三者結果の再現と、新規戦略の検証を区別する。
- AIが生成した説明を証拠として扱わず、コード、データ、実行結果で確認する。
