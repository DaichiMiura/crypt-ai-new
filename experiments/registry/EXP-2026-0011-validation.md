# Validation Report: EXP-2026-0011

## Decision

- Research status: `INCONCLUSIVE`
- Promotion status: `NEEDS_FORWARD_EVIDENCE`
- Validator: `Independent Validation`（固定runnerの再実行と主要指標の再計算）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: なし（paper・shadow・liveを許可しない）

EXP-2026-0008の売買条件を変えず、新規entry時だけ20日実現ボラティリティに応じて
投資比率を縮小した。base feeでは最大DDが5年中4年で改善し、5年合算最終資産も
full-sizeの100.31%だった。しかしDD改善幅中央値は0.38 percentage pointsにとどまり、
事前登録した3 points以上を満たさなかった。棄却条件には該当しないため、採用でも
棄却でもなく`INCONCLUSIVE`とする。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0011-hypothesis.yaml`（`b1a71ab`）
- Dataset: `DATA-2026-0003`（補間済みBinance Global Spot BTCUSDT日足）
- Implementation: `src/crypt_ai/research.py`
- Runner: `scripts/run_exp_2026_0011.py`
- Generated summary: `artifacts/EXP-2026-0011/summary.json`
- Reproduction: `uv run python scripts/run_exp_2026_0011.py`

## Strategy and metric definitions

- full-sizeはEXP-2026-0008のSMA200付きDonchian 55/20を現金の100%で取引する。
- scaledは同じentry・exitを使い、entry時の投資比率を
  `min(1, 40% / 20日年率実現ボラティリティ)`とする。
- 実現ボラティリティはclose-to-close単純リターンの20日母標準偏差を
  `sqrt(365)`倍した。日tの値を日t+1始値のentryに使った。
- entry時に決めた投資比率はexitまで固定し、日次リバランスとレバレッジは使わない。
- `DD改善幅`は`scaled max drawdown - full-size max drawdown`で、正が改善を表す。
- `合算最終資産維持率`は、各年を1,000 USDTで独立評価した5つの最終資産合計を
  scaled / full-sizeで割った値であり、5年間の連続複利成績ではない。

## Data integrity and accounting

- 2,192本（2020-01-01〜2025-12-31）、重複0、欠損0、線形補間15本だった。
- 補間日上の約定は0件だった。
- 各年は初期資金1,000 USDT相当にリセットし、年末未決済は終値mark-to-marketした。
- 部分投資ではentry予算からfeeを引いて数量を計算し、未投資分を現金で保持した。
- 保有中に投資比率が変化する入力は会計エラーとして拒否するテストを追加した。
- full-size系列はEXP-2026-0008と同じシグナル生成関数から作成した。

## Base-fee results

| 年 | full最終資産 | scaled最終資産 | full DD | scaled DD | DD改善幅 | 最終資産差 |
|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 1,537.38 | 1,517.32 | -33.64% | -32.57% | +1.08pt | -20.06 |
| 2022 | 1,000.00 | 1,000.00 | 0.00% | 0.00% | 0.00pt | 0.00 |
| 2023 | 1,029.64 | 1,059.24 | -20.88% | -14.36% | +6.51pt | +29.60 |
| 2024 | 1,513.15 | 1,521.10 | -20.89% | -20.87% | +0.02pt | +7.95 |
| 2025 | 885.16 | 885.93 | -21.83% | -21.45% | +0.38pt | +0.77 |

2023年の改善は明確だったが、2021・2024・2025年の改善は小さく、結果の大部分が
一つの年に集中している。2022年は両方とも取引がなく、改善年には数えていない。

## Exposure and sensitivity

- scaled entryは13件で、そのうち8件が100%未満だった。
- entry投資比率は最小46.32%、中央値96.63%、最大100%だった。
- full-sizeが損失だった年は2025年の1年で、scaledは損失額を0.77 USDT縮小した。
- 5年合算最終資産はfull-size 5,965.33、scaled 5,983.58だった。
- adverseとstressでもDD改善年は4 / 5、改善幅中央値は各0.38pt、合算資産維持率は
  100.35%、100.39%で、方向性は変わらなかった。

## Preregistered decision

| 条件 | 結果 | 判定 |
|---|---:|---|
| 最大DD改善が3年以上 | 4/5年 | 合格 |
| DD改善幅中央値が3pt以上 | +0.38pt | 不合格 |
| 5年合算最終資産維持率が90%以上 | 100.31% | 合格 |
| full-size損失年を悪化させない | 0/1年で悪化 | 合格 |
| 100%未満のentryが5件以上 | 8件 | 合格 |
| 棄却条件 | 該当なし | 棄却しない |

## Interpretation

entry時のvolatility sizingは、今回の範囲では収益を犠牲にするだけの仕組みには
ならず、2023年には損失取引を小さくしてDDと最終資産の両方を改善した。一方、
投資比率中央値が96.63%だったため、多くのentryではfull-sizeとほぼ同じであり、
年次DDの典型的な改善幅は会社が事前に求めた水準より小さい。

この結果は「方向性は有望だが効果量がまだ不足」という位置づけである。40%目標や
20日窓を今回の結果に合わせて変更すると後付け最適化になるため、この実験内では
調整しない。次に進めるなら、この設定を固定した未観測forwardで、entry投資比率、
損失額、DD、現金保有による機会損失を記録する。

## Limitations and required follow-up

1. 単一銘柄・5暦年・13 entriesで、2023年への結果集中が大きい。
2. 既観測Global proxyであり、独立forwardやBinance Japanの約定証拠ではない。
3. 年次独立評価のため、連続運用時の複利と年境界の資本推移を表さない。
4. entry時だけの縮小であり、保有後にボラティリティが急上昇しても数量を変えない。
5. EXP-2026-0011をpaper・shadow・liveへ昇格させない。
6. 次回はパラメータを固定し、新たに到着する未観測データでforward評価する。

## Research basis

- [Volatility Managed Portfolios](https://www.nber.org/papers/w22208): 高ボラティリティ時にリスク量を落とす仮説の参考。
- [Time series momentum and volatility scaling](https://www.sciencedirect.com/science/article/pii/S1386418116301379): モメンタム効果とvolatility scalingの寄与を分離して解釈する必要性の参考。
