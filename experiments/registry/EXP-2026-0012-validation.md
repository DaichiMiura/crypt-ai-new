# Validation Report: EXP-2026-0012

## Decision

- Research status: `PASSED_RETROSPECTIVE_VALIDATION`
- Promotion status: `NEEDS_FORWARD_EVIDENCE`
- Validator: `Independent Validation`（固定runnerの再実行と成果物からの再計算）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: 過去データ上の候補認定のみ（paper・shadow・liveを許可しない）

EXP-2026-0008のSMA200付きDonchian entryを固定し、Donchian 20日安値exitだけを
20日単純平均ATRの3倍によるラチェット式trailing exitへ置き換えた。base feeでは
最大DDが5年中4年で改善し、改善幅中央値は4.84 percentage points、5年合算最終資産は
baselineの103.05%だった。全候補条件を満たし、棄却条件に該当しないため、既観測期間
に限る候補として`PASSED_RETROSPECTIVE_VALIDATION`とする。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0012-hypothesis.yaml`（`00dcb3c`）
- Dataset: `DATA-2026-0003`（補間済みBinance Global Spot BTCUSDT日足）
- Implementation: `src/crypt_ai/research.py`
- Runner: `scripts/run_exp_2026_0012.py`
- Generated summary: `artifacts/EXP-2026-0012/summary.json`
- Reproduction: `uv run python scripts/run_exp_2026_0012.py`

## Strategy and metric definitions

- baselineはSMA200付きDonchian 55日高値entry・20日安値exitである。
- ATR版はentryを変えず、実保有開始後の最高値から`3 × 20日ATR`を引く。
- ATRはTrue Rangeの20日単純移動平均で、Wilder smoothingやEMAではない。
- stopは前日値との最大値を採用して切り下げず、終値がstop未満なら次日始値で売る。
- ATR退出後は基礎Donchian状態がflatを経て新しいentryを出すまで再参入しない。
- `DD改善幅`は`ATR max drawdown - baseline max drawdown`で、正が改善を表す。
- 合算最終資産は各年を1,000 USDTで独立評価した合計で、5年連続複利ではない。

## Data integrity and accounting

- 2,192本（2020-01-01〜2025-12-31）、重複0、欠損0、線形補間15本だった。
- ATR exitは11件で、補間日上のexit判定と約定はともに0件だった。
- ATR stopの保有区間内の単調非減少、exit判定日の保有、翌日のflatを全11件で照合した。
- 生成equity CSVから最終資産と最大DDを別計算し、summaryと全20値が一致した。
- baselineの5年分equity CSVはEXP-2026-0008のfiltered系列と完全一致した。
- 年初は資金を1,000へリセットしつつ前年のシグナル状態を引き継ぎ、年末未決済は
  終値mark-to-marketした。

## Base-fee results

| 年 | baseline最終資産 | ATR最終資産 | baseline DD | ATR DD | DD改善幅 | 最終資産差 |
|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 1,537.38 | 1,269.25 | -33.64% | -18.71% | +14.94pt | -268.13 |
| 2022 | 1,000.00 | 1,000.00 | 0.00% | 0.00% | 0.00pt | 0.00 |
| 2023 | 1,029.64 | 1,164.16 | -20.88% | -11.93% | +8.95pt | +134.52 |
| 2024 | 1,513.15 | 1,763.02 | -20.89% | -16.05% | +4.84pt | +249.87 |
| 2025 | 885.16 | 950.92 | -21.83% | -17.71% | +4.12pt | +65.76 |

2021年はDDを大きく抑えた一方、上昇への再参入が遅れて最終資産を268.13減らした。
2023〜2025年はDDと最終資産を同時に改善した。2022年は両方式とも取引がない。

## Trading and sensitivity

- baselineは24 fills・11 closed round trips、ATR版は23 fills・11 closed round tripsだった。
- ATR版がbaselineのlong中にflatだった日は合計302日で、機会損失は無視できない。
- base feeの5年合算最終資産はbaseline 5,965.33、ATR 6,147.34だった。
- adverseとstressでもDD改善は4 / 5年、改善幅中央値は各4.84pt・4.83pt、
  合算資産維持率は各103.05%・103.04%で、方向性は変わらなかった。

## Preregistered decision

| 条件 | 結果 | 判定 |
|---|---:|---|
| 最大DD改善が3年以上 | 4/5年 | 合格 |
| DD改善幅中央値が3pt以上 | +4.84pt | 合格 |
| 5年合算最終資産維持率が90%以上 | 103.05% | 合格 |
| CAGRがbaseline以上の年が2年以上 | 4/5年 | 合格 |
| ATR exitが3件以上 | 11件 | 合格 |
| 棄却条件 | 該当なし | 棄却しない |

## Interpretation

この固定設定では、ATR exitは単なる損切りではなく、利益を残しながら高値からの反落を
早めに切る出口として機能した。ただし効果は均一ではなく、2021年にはDD改善と引き換えに
大きな上昇機会を失った。stop-lossの有効性は原戦略と価格過程に依存するため、DDだけで
採用せず、収益維持と再参入待ちの機会損失を同時に追跡する。

今回の3 ATR・20日窓を結果に合わせて変更すると後付け最適化になる。この実験では変更せず、
次段階は設定を固定した未観測forwardで、exit時点、翌日始値、baselineとの差、再参入待ち日数を
記録する。過去候補への認定はpaper投入の承認ではない。

## Limitations and required follow-up

1. 単一銘柄・既観測5暦年・11 exitsで、独立OOSではない。
2. Binance Global proxyであり、Binance Japanの価格basisや約定品質を示さない。
3. 終値判定・次日始値約定なので、急落時にstop水準で売れるとは仮定していない。
4. 年次独立評価は5年間の連続複利、税務、資本配分を表さない。
5. 固定設定の未観測forwardを完了するまでpaper・shadow・liveへ昇格させない。

## Research basis

- [When Do Stop-Loss Rules Stop Losses?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338): stop-lossの効果を原戦略と価格過程に依存するものとして扱う根拠。
- [The Significance of Trading Frequency and Stop Loss in Trend Following Strategies](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2349848): 損失抑制と収益・取引頻度を同時に評価する根拠。
