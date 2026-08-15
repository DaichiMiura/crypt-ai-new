# Validation Report: EXP-2026-0001

## Decision

- Status: `REJECTED`
- Validator: `research` quality gate (automated checks; human approval not granted)
- Reviewed at (UTC): 2026-08-15
- Scope of approval: none. This report does not approve paper or live trading.

`REJECTED` は戦略の損益が悪いという意味ではなく、予約したデータ品質条件を満たさず、性能評価を有効な証拠として採用しなかったことを意味する。

## Frozen artifacts

- Code commit: `8efae22`
- Strategy config: `experiments/registry/EXP-2026-0001-hypothesis.yaml`
- Data snapshot ID: `DATA-2026-0001`
- Research venue: Binance Global Spot `BTCUSDT`
- Execution venue: Binance Japan Spot（shadow対象、今回のバックテストには不使用）
- Reproduction command: `uv run python scripts/run_exp_2026_0001.py`

## Dataset and grain

対象はBinance Public DataのBTCUSDT・1時間足で、1行を1つのkline open timeとする。対象期間は2020-01-01 00:00 UTCから2025-12-31 23:00 UTC、期待行数は52,608行である。

取得した月次アーカイブ72個はSHA-256を検証した。欠損区間については、公式日次アーカイブを15日分取得して月次データと照合し、さらに公開APIで周辺時刻を確認した。日次アーカイブでも補える行が存在しない時間は、補間・前値埋め・ゼロvolume bar生成を行っていない。

## Checks performed

- SHA-256: 月次raw aggregateとprocessed outputのハッシュをmanifestへ記録
- Uniqueness: `event_time`の重複
- Completeness: 1時間間隔の欠損segmentと欠損bar数
- Validity: timestampのUTC変換、価格・出来高の数値変換、正値価格
- Temporal semantics: 確定足終値から次バー始値へのシグナル遅延
- Reproducibility: 固定された依存関係、取得メタデータ、決定論的会計コード

## Findings

### Data integrity — High severity

- 行数は52,577行で、期待値52,608行から31時間分不足している。
- 重複は0件。
- 欠損は15区間に分散している（2020-02-09、2020-02-19、2020-03-04など）。全区間の詳細は`DATA-2026-0001-manifest.yaml`に固定した。
- 月次アーカイブ、欠損日の公式日次アーカイブ、公開APIの照合後も31時間が埋まらなかった。

この欠損をそのまま含むと、シグナル計算と保有期間が実時間の連続系列を表さない。値を推測して埋めると、実在しない価格・出来高をバックテストへ投入するため、EXP-2026-0001の事前登録にある「欠損を検出したバーでは売買しない」という条件に反する。したがって、データ品質ゲートはバックテストを拒否する。

### Backtest accounting — Not evaluated

実装には片道fee、往復spread、fillごとのslippage、次バー始値、現金・数量のDecimal計算を含めた。ただし、データ品質ゲートを通過していないため、全期間・OOS・ベースライン比較の数値は研究結果として生成していない。

ゲート導入前に実行された試算値は、欠損データを含むため成果物・証拠として採用しない。特に、CAGR、最大ドローダウン、取引数を戦略性能の根拠として引用してはならない。

### Statistical robustness — Not evaluated

買い持ちベースライン、2025年OOS、費用感度、相場環境別集計、最小数量・tick sizeを含む完全な約定制約の検証は、連続した正本データを確定した後に実施する。

## Rejection rationale

EXP-2026-0001は、事前登録したデータ完全性条件を満たさないため`REJECTED`とする。期間を短縮する、欠損を補間する、別ソースを混ぜる、といった変更をこの実験へ後付けで行ってはならない。変更する場合は、新しい実験IDを予約登録し、変更理由と比較可能性を記録する。

## Required follow-up

1. 連続した期間だけを使う新しい実験を作るか、欠損を含む期間を扱う明示的な欠損ポリシーを新規実験として事前登録する。
2. `exchangeInfo`の`PRICE_FILTER`、`LOT_SIZE`、最小notionalを約定モデルへ取り込み、丸めと注文拒否をテストする。
3. 有効なデータが確定した後に、買い持ち、OOS、費用感度、回転率、期待値、ドローダウンを一度だけ予約条件どおり計算する。
4. 有効な結果が得られるまで、paper・shadow・liveへの昇格申請を行わない。
