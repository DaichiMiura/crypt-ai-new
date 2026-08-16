# 資金配分方針

`src/crypt_ai/allocation.py` は、複数銘柄を同時に扱うときの資金配分を戦略ロジックから分離する。戦略が出した新規シグナルは、必ずこの層で資産、ロット、スリーブ上限、口座上限を確認してから注文候補にする。

## 設定

[config/allocation.yaml](../config/allocation.yaml) は変更しやすい配分プロファイルの例である。次の項目をYAMLで変更できる。

- `allowed_symbols`: 配分対象の資産一覧
- `initial_equity`: 初期equity
- `reserve_cash`: 常に残す予備資金
- `max_long_gross_notional` / `max_short_gross_notional`: ロング・ショート各スリーブの上限
- `max_total_gross_notional`: 両スリーブ合計の上限
- `per_symbol_max_notional`: 1銘柄のロング・ショート合算上限
- `lot_notional`: 1ロットの固定想定元本
- `max_concurrent_*_positions`: 同時保有する銘柄数の上限

例えば初期equityが100,000 JPYで`lot_notional: 10,000`なら、1ロットは10,000 JPY相当である。資金が増えても、この層は自動複利にせず、設定した固定ロットを使い続ける。ロットを変更する場合は、設定変更として再検証する。

## 判定の順序

`PortfolioAllocator.try_open` は承認時だけ状態を更新する。次のどれかに該当する新規配分は拒否する。

1. 未登録の資産または不正なside・ロット数
2. 同時保有銘柄数の上限超過
3. 1銘柄の元本上限超過
4. ロング・ショート各スリーブの上限超過
5. 口座全体の元本上限超過
6. 予備資金を残せない配分

決済時は`PortfolioAllocator.release`で元本を解放し、次の新規配分に再利用する。

複数銘柄のシグナルを実際に同時会計する場合は、`run_allocated_portfolio`へ銘柄別の
DataFrameを渡す。`desired_position`または`desired_long_position`がlong、
`desired_short_position`がshortのシグナルになる。銘柄を昇順、longを先に処理しながら
新規注文を配分層で承認し、拒否理由を監査イベントへ残す。

ショート会計は、1ロット元本を担保相当額として取り置き、entry価格とmark価格の差を
含み損益として計算する研究用モデルである。Funding、清算、取引所固有の証拠金や数量
刻みは含まれないため、paper・shadowへ進める前に別途実行モデルへ接続する。

## 境界

この層が扱うのは目標gross notionalの配分であり、取引所の数量刻み、手数料、スリッページ、証拠金、清算、日次損失、データ鮮度を代替しない。注文前には、既存のpaperリスク上限と決定論的リスクエンジンを必ず通す。レバレッジを使う場合も、配分上限を自動で緩和しない。

現在の実験結果は銘柄ごとの個別仮想口座であり、この配分層を通した同時運用ポートフォリオの結果ではない。複数銘柄のpaperへ進む前に、各シグナルをこの層へ接続した再現可能なバックテストを追加する。
