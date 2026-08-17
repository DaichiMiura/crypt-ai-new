# Validation and Promotion Policy

## Independence

戦略の実装者は検証資料を準備できるが、最終判定を行わない。検証担当は実装者の要約ではなく、固定されたコード、設定、データから結果を再実行する。

## Required validation

各候補について最低限、以下を確認する。

### データ整合性

- 未来参照、サバイバーシップバイアス、欠損の不当補完がない
- タイムゾーン、意思決定時点、データ到着時点が整合する
- 原本から特徴量までの来歴を追跡できる
- 補間・推定で生成した行は実測行と区別され、合成行がシグナル・約定へ与えた影響を集計できる

### Venue transfer

- 研究venue、実行venue、market type、symbol、quote assetの対応が固定されている
- Spotとperpetual、contract category、Funding、pair変換、fee・slippage modelの前提が再現できる
- ZOOMEX履歴とrealtimeのendpoint、確定足、遅延、欠測、Fundingの対応を記録している
- 注文なしのZOOMEX shadowで、想定約定、未約定、拒否条件をバックテストと比較している
- 公開Kline/Fundingの結果を実際の板・約定やlive承認の証拠として扱っていない
- 過去のBinance proxy実験は当時のvenue差を維持し、ZOOMEXの証拠へ読み替えていない

### バックテスト会計

- 手数料、スプレッド、スリッページ、資金拘束を含む
- 注文拒否、部分約定、最小数量、丸めを扱う
- ポジション、現金、実現・未実現損益を独立に照合できる
- 同一資金の二重使用や約定前利益計上がない

### 統計と頑健性

- 単純なベースラインと費用控除後で比較する
- 勝率、平均利益、平均損失、取引費用から期待値を再計算する
- セットアップ、エントリー、損切り、決済、非取引条件が曖昧なく再現できる
- ウォークフォワードまたは時系列を守った検証を行う
- 探索回数、多重比較、パラメータ感度を考慮する
- 少数の取引、単一期間、単一銘柄への依存を報告する
- 最大ドローダウン、テール損失、連敗、回転率を評価する
- 期待値、損失分布、連敗、費用、約定失敗を使ってリスク・オブ・ルインを推定する
- リスク・オブ・ルインの推定結果を確実性や保証として表現しない

### 安全性

- 価格停止、重複イベント、API再試行、切断、再起動を試験する
- 制限超過時に新規注文が拒否される
- 状態復旧時に二重注文を起こさない
- 監視、照合、キルスイッチ、ロールバックを確認する

## Two-axis decision model

検証報告は、研究結果と運用許可を一つのstatusへ混在させず、次の二軸で記録する。

### Research status

`research_status`は、固定した仮説に対して得られた研究証拠を表す。

- `REJECTED`: データ品質、実装、会計、または事前登録した基準を満たさない。
- `INCONCLUSIVE`: 一部に有望な結果はあるが、固定した仮説を支持する証拠として一貫しない。
- `BACKTEST_CANDIDATE`: 事前登録した単一バックテストまたはOOS候補基準を満たした。
- `PASSED_RETROSPECTIVE_VALIDATION`: 固定条件のまま複数の過去期間・相場環境で候補基準を満たした。ただし未観測データの証拠ではない。
- `PASSED_FORWARD_TEST`: 事前に封印した未観測期間または開始後に取得したデータで、登録済みforward基準を満たした。

### Promotion status

`promotion_status`は、その成果物に許可された運用段階を表す。

- `NOT_ELIGIBLE`: 次の運用段階へ進めない。
- `NEEDS_FORWARD_EVIDENCE`: 研究候補だが運用段階の人間承認がまだない。未観測証拠は
  paper運用中に蓄積してよく、paper承認の必須前提とはしない。
- `PAPER_APPROVED`: 固定したpaper設定とリスク予算の範囲だけでpaper運用を許可する。
- `SHADOW_APPROVED`: 注文を送らないshadow運用だけを許可する。
- `LIMITED_LIVE_APPROVED`: 人間承認済みの少額・固定上限内だけでlive運用を許可する。
- `SCALED_APPROVED`: 別途承認された資本配分と上限内で増額を許可する。

高い`research_status`は、資金・API権限・デプロイの許可を意味しない。運用可能範囲は常に
`promotion_status`と人間の承認記録で決まる。`BACKTEST_CANDIDATE`または
`PASSED_RETROSPECTIVE_VALIDATION`は、固定設定、自動テスト、戦略別paper予算、人間承認が
そろえば`PAPER_APPROVED`へ進める。未観測データの性能検証はpaper運用の目的に含める。
`PASSED_FORWARD_TEST`はshadowまたはliveの検討材料であり、paper開始の必須条件ではない。

## Promotion stages

```text
idea -> registered -> implemented -> validated -> paper
     -> shadow -> live_candidate -> limited_live -> scaled
```

現段階で昇格できる上限は、実注文を送らない`shadow`とする。paperとshadowでは実資金、
取引権限、秘密情報を使わない。現在のshadow許可は固定済みEXP-2026-0042だけに適用する。
`live`以降を解禁するには、別PRで方針、実装、検証証拠、ロールバック手順、人間の
承認記録を追加する。

## Approval packet

昇格申請には次を含める。

- 完成した [検証報告](../templates/validation-report.md)
- 固定する成果物ID一式
- 事前登録と全実験履歴へのリンク
- 未解決リスクと最悪ケース
- 監視指標、停止条件、ロールバック方法
- 初期資金・注文上限と観測期間
- 戦略別リスク予算、サイズ計算、リスク・オブ・ルインの推定
- 開発者、独立検証者、リスク担当、人間承認者の記録

基準未達、証拠欠落、再現失敗は不合格である。判断保留を暗黙の合格として扱わない。
