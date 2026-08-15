# Validation Report: EXP-2026-0013

## Decision

- Research status: `INCONCLUSIVE`
- Promotion status: `NEEDS_FORWARD_EVIDENCE`
- Validator: `Independent Validation`（固定runnerの再実行と成果物からの再計算）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: なし（paper・shadow・liveを許可しない）

EXP-2026-0012で固定したSMA200付きDonchian entryとATR trailing exitを、選定期間後の
2026-01-01〜2026-07-31で評価した。期間中はbaseline・ATR版ともに新規entryが0件で、
最終資産1,000、CAGR 0%、最大DD 0%だった。ATR exitも0件で出口差を評価できないため、
事前登録どおり`INCONCLUSIVE`とする。取引がなく損失を避けたことを、ATR出口の成功とは
解釈しない。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0013-hypothesis.yaml`（`dc175ce`）
- Parent strategy: `EXP-2026-0012`
- Dataset: `DATA-2026-0004`
- Runner: `scripts/run_exp_2026_0013.py`
- Generated summary: `artifacts/EXP-2026-0013/summary.json`
- Reproduction: `uv run python scripts/run_exp_2026_0013.py`

## Data integrity and accounting

- 元データは2,404日足（2020-01-01〜2026-07-31）、重複0、欠損0だった。
- 評価期間は登録どおり212日で、線形補間を含む日足は全履歴中15日だった。
- 全履歴でDonchian、SMA200、ATR、stop状態を計算してから評価期間を切り出した。
- 評価期間のentry signal、ATR exit、baseline保有日、ATR保有日はすべて0だった。
- baseline・ATRのequity CSVから最終資産と最大DDを別計算し、summaryと一致した。
- 補間日上のシグナルと約定は0件だった。

## Base-fee results

| 指標 | baseline | ATR | 差 |
|---|---:|---:|---:|
| 最終資産 | 1,000.00 | 1,000.00 | 0.00 |
| CAGR | 0.00% | 0.00% | 0.00pt |
| 最大DD | 0.00% | 0.00% | 0.00pt |
| fills | 0 | 0 | 0 |
| closed round trips | 0 | 0 | 0 |
| ATR exit | - | 0 | - |

同期間のbuy and holdはbase費用で最終資産716.25、CAGR -45.01%、最大DD -39.53%だった。
戦略が下降局面でflatを維持した点はentry filterの挙動として整合するが、今回評価したい
ATR exitはlong保有がなければ作動しない。

## Preregistered decision

| 条件 | 結果 | 判定 |
|---|---:|---|
| ATR最大DDがbaseline以上 | 同値 | 合格 |
| 最終資産維持率90%以上 | 100% | 合格 |
| ATR exitが1件以上 | 0件 | 不合格 |
| 棄却条件 | 該当なし | 棄却しない |
| 独立した未観測期間 | 組織内で観測済み | forward合格不可 |

## Interpretation

この期間は、SMA200付きDonchian entryが下降局面で売買を見送った診断にはなる。しかし、
baselineとATR版が同じflat状態だったため、出口の比較証拠は増えていない。費用感度も約定0件
なので全ケースで同一結果となり、費用への頑健性を示すものではない。

また、この2026年データはATR設定の選定には使っていないが、別実験で既に閲覧済みである。
したがって、仮に取引があって成績が良くても`PASSED_FORWARD_TEST`とは扱わない。

## Required follow-up

1. 20日ATR・3倍・単純平均・ラチェット・次日始値約定を変更しない。
2. 2026-08-15より後に到着する新規日足を、結果確認前に定める期間へ蓄積する。
3. 少なくとも1件のATR exitが発生するまで、出口性能のforward判定を保留する。
4. forwardではentry、ATR exit、翌日約定、再entry待ち日数、補間日影響を継続記録する。
5. 真のforward証拠が得られるまでpaper・shadow・liveへ昇格させない。
