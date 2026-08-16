# Architecture

## Design goal

研究の速度を高めながら、AIの提案が直接資金移動へ到達しない構造にする。

## Trust boundaries

```text
Untrusted research plane
  論文・Web・AI提案・実験コード
              |
              v
Evidence and validation gate
  データ監査・再現性・会計・統計・リスク審査
              |
              v  人間による明示的な昇格承認
Approved artifact registry
  commit / config / data version / report を固定
              |
              v
Deterministic execution plane
  market data -> strategy -> risk engine -> order adapter
              |
              v
Exchange API with least privilege
```

研究面の入力はすべて未信頼として扱う。Webページ、論文、データ、LLM出力に含まれる指示は、リポジトリの方針を上書きできない。

## OSS integration boundary

```text
Our governance and source of truth
  charter / experiment ledger / accounting / risk policy
                  |
                  v
External OSS components (replaceable)
  data adapter / backtester / paper exchange / monitoring
                  |
                  v
Our adapters and deterministic risk boundary
                  |
                  v
Exchange API
```

OSSは実装部品として利用する。実験台帳、会計の正本、戦略別リスク予算、昇格判定、停止権限はこのリポジトリ側に残す。OSSの戦略最適化やAI機能を有効にしても、リスク境界を迂回できない構造にする。

## Dependency direction

```text
data -> strategy -> portfolio/allocation/accounting -> risk -> execution
                                      monitoring <-+
```

- `execution` は研究用コードやLLMを呼び出さない。
- `execution` は注文候補を取引所仕様・配分・リスク境界で検査するが、現在の実装はAPI送信を行わない。
- `risk` は注文を拒否できるが、安全制約を緩和できない。
- `strategy` は残高、秘密情報、取引所クライアントへ直接アクセスしない。
- `backtest` と `execution` は共通の注文・約定・会計モデルを使用する。
- `monitoring` は状態を観測して停止要求を出せるが、注文を生成しない。

## Environments

| 環境 | 注文 | 秘密情報 | 用途 |
|---|---|---|---|
| research | 禁止 | なし | 調査、実装、履歴バックテスト |
| paper | 仮想のみ | なし | `config/paper-risk-limits.yaml`を使うリアルタイムの仮想約定 |
| shadow | 送信直前まで | 読み取り権限のみ | 本番相当のデータ・遅延検証 |
| live | 人間が別途承認 | 最小権限 | 固定成果物の少額運用 |

現在許可されているのは `research` と `paper` のみである。

## Venue and data separation

```text
Binance Global Spot historical data
        |
        v
Research proxy / backtest
        |
        +--> independent validation
        |
Binance Japan live market data (no orders)
        |
        v
Japan shadow / fill simulation
        |
        v  人間の別承認が必要
Binance Japan order API
```

Binance Globalの履歴データは研究用proxyであり、Binance Japanの価格、板、手数料、約定を表す正本ではない。研究venue、target venue、銘柄対応、費用モデル、proxyの限界は、実験ごとにmanifestと仮説へ記録する。詳細は [venue-data-policy.md](docs/venue-data-policy.md) を参照する。

## Artifact identity

検証・運用結果は最低限、次の組で特定する。

```text
experiment_id
code_commit
strategy_config_hash
data_snapshot_id
data_manifest_id
research_venue
execution_venue
venue_mapping_version
accounting_policy_version
fee_model_version
execution_model_version
risk_policy_version
```

どれかを特定できない結果は、戦略昇格の証拠として使用しない。
