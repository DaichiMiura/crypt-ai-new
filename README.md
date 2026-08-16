# crypt-ai

AIを活用して仮想通貨取引戦略を研究・検証・運用するための、架空会社形式のリポジトリです。

目的は「AIに自由に売買させること」ではありません。仮説、実装、独立検証、リスク審査、運用、振り返りを再現可能な工程にし、人間が承認した固定済みの成果物だけを段階的に昇格させることです。

## 現在の段階

**Phase 0: ペーパー運用**

- 本番取引は禁止
- APIキーや秘密情報をリポジトリに保存しない
- 仮想注文は口座全体のハード上限と戦略別リスク予算の小さい方で制限する
- 本番用のfail-closed設定は [risk-limits.yaml](config/risk-limits.yaml)、ペーパー用設定は [paper-risk-limits.yaml](config/paper-risk-limits.yaml) に分ける
- 戦略別の例は [exp-0001-risk.yaml](config/strategies/exp-0001-risk.yaml) に置く
- データ取得、バックテスト、取引所接続、監視は成熟したOSSを積極的に利用する
- 履歴の短いBinance Japanを補うため、Binance Global Spotを研究用proxyとして使い、Binance Japanのshadowでvenue差を検証する
- 研究・検証・運用の責任を分離する

最初のpaper承認戦略は`EXP-2026-0012`である。実行範囲と手順は
[EXP-2026-0012 Paper Runbook](docs/paper-exp-2026-0012.md)を参照する。

収益性、安全性、法令適合性はまだ確認されていません。このリポジトリの存在やバックテスト結果は、実運用の承認を意味しません。

## 会社の構成

| 部門 | 責任 | 自分で承認できないもの |
|---|---|---|
| Research | 仮説と経済的根拠の事前登録 | 戦略の採用 |
| Data | データ来歴、品質、時点整合性 | 不備のあるデータの例外使用 |
| Strategy Engineering | 戦略とテストの実装 | 自作戦略の昇格 |
| Independent Validation | 会計、未来参照、統計、再現性の監査 | リスク上限の緩和 |
| Risk & Operations | 昇格審査、監視、停止、損益照合 | 人間承認の代行 |
| CEO（人間） | リスク許容度、資本配分、最終承認 | 機械的な安全装置の黙示的な迂回 |

## 文書の読み順

1. [AGENTS.md](AGENTS.md)
2. [会社憲章](docs/charter.md)
3. [アーキテクチャ](ARCHITECTURE.md)
4. [取引所・データ方針](docs/venue-data-policy.md)
5. [研究方針](docs/research-policy.md)
6. [検証・昇格方針](docs/validation-policy.md)
7. [リスク方針](docs/risk-policy.md)
8. [運用方針](docs/operations.md)
9. [会計方針](docs/accounting-policy.md)
10. [Penfold本からの設計原則](docs/references/penfold-universal-principles.md)
11. [OSS・依存関係ポリシー](docs/dependency-policy.md)
12. [資金配分方針](docs/allocation-policy.md)

## 最初のマイルストーン

単純な戦略を1つだけ選び、次をペーパー環境で一周させます。

```text
仮説登録 → Global proxyデータ検査 → 実装 → バックテスト → 独立検証
         → Japanデータ校正・注文なしshadow → ペーパー運用 → 日次照合 → 振り返り
```

実験は [仮説テンプレート](templates/hypothesis.yaml) から始め、検証は [検証報告テンプレート](templates/validation-report.md) に記録します。
データ取得は [データmanifest](templates/data-manifest.yaml) に記録し、損益は [会計方針](docs/accounting-policy.md) に従います。

最初の登録済み実験は [EXP-2026-0001](experiments/registry/EXP-2026-0001-hypothesis.yaml) です。結果を確認する前に仮説、費用条件、OOS期間、Binance GlobalからBinance Japanへのvenue移管上の限界を固定しています。対応するデータmanifestは [DATA-2026-0001](experiments/registry/DATA-2026-0001-manifest.yaml) です。

## 最小検証

```bash
./scripts/verify.sh
```
