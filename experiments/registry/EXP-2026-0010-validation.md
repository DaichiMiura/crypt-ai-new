# Validation Report: EXP-2026-0010

## Decision

- Research status: `REJECTED`
- Promotion status: `NOT_ELIGIBLE`
- Validator: `Independent Validation`（固定runnerの再実行結果）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: なし（paper・shadow・liveを許可しない）

既存のEXP-2026-0008ロングに、SMA200で方向を制限したDonchianショートを加え、
long-only、short-only、one-position combinedを同じデータと費用仮定で比較した。
ショートは現物価格から作った合成proxyであり、Binance Japanのmargin/futuresの
約定可能性や損失上限を検証していない。

base feeでは、combinedの最大ドローダウンがlong-onlyより改善した年は5年中0年、
改善幅の中央値は-4.36 percentage pointsだった。short-onlyは5年合計で5件のclosed
round tripsを持ったが、2022〜2024年のshort損失がcombinedのドローダウンを悪化させた。
事前登録した棄却条件に該当するため、研究上は`REJECTED`とする。

初回実行ではlong legがEXP-2026-0008のentry状態遷移と一致していなかった。検証時に
この差を検出し、long系列がEXP-2026-0008と完全一致する回帰テストを追加してから
再実行した。本レポートの数値は修正後runnerの結果である。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0010-hypothesis.yaml`
- Dataset: `DATA-2026-0003`（補間済みBinance Global Spot BTCUSDT日足）
- Implementation: `src/crypt_ai/research.py`
- Runner: `scripts/run_exp_2026_0010.py`
- Generated summary: `artifacts/EXP-2026-0010/summary.json`
- Reproduction: `uv run python scripts/run_exp_2026_0010.py`

## Data integrity and accounting

- 2,192本（2020-01-01〜2025-12-31）、重複0、欠損0、線形補間15本だった。
- SMA200とDonchian 55/20は全期間履歴で計算し、シグナルを次日始値へ遅延した。
- 年ごとに初期資金1,000 USDT相当へリセットし、年末未決済は終値mark-to-marketとした。
- base・adverse・stressは片道fee 0.1%、0.15%、0.2%、spread 0.05%、片道slippage 0.05%である。
- 約定は補間日では発生しなかった。

合成shortでは、初期資金と同額のnotionalを売り、終値で負債をmark-to-marketした。
借入金利、funding、mark/index価格差、maintenance margin、liquidation、borrow在庫、
最小数量、tick size、部分約定、API拒否は含まれない。したがって、shortの成績は
Binance Japanで実行できる戦略の証拠ではない。

## Base-fee results

| 年 | long-only最終資産 | short-only最終資産 | combined最終資産 | long DD | combined DD | DD差分 |
|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 1,537.38 | 1,000.00 | 1,537.38 | -33.64% | -33.64% | 0.00pt |
| 2022 | 1,000.00 | 960.31 | 960.31 | 0.00% | -35.10% | -35.10pt |
| 2023 | 1,029.64 | 944.94 | 972.95 | -20.88% | -25.24% | -4.36pt |
| 2024 | 1,513.15 | 861.65 | 1,303.80 | -20.89% | -31.83% | -10.95pt |
| 2025 | 885.16 | 1,134.80 | 1,004.48 | -21.83% | -25.44% | -3.62pt |

`DD差分`は`combined max drawdown - long-only max drawdown`で、正が改善を表す。
combinedのCAGRがlong-only以上だった年は2021年と2025年の2年だった。

## Sensitivity and preregistered decision

adverseとstressでも、combinedのDD改善年は各0年、改善幅中央値はそれぞれ
-4.43pt、-4.50ptだった。費用を厳しくしても判定は変わらない。

| 条件 | 結果 | 判定 |
|---|---:|---|
| combinedのDD改善が3年以上 | 0/5年 | 不合格 |
| combinedのDD改善幅中央値が正 | -4.36pt | 不合格 |
| combinedのCAGRがlong以上 | 2/5年 | 合格 |
| short-only closed round tripsが5件以上 | 5件 | 合格 |
| 棄却条件（DD改善が2年未満） | 該当 | 棄却 |

## Interpretation

今回の設定では、ショートは下落相場を安定して保護するヘッジにならなかった。
2025年には最終資産が改善した一方、最大DDは改善せず、2022〜2024年にはshortの
遅い発火と反発時の損失がロング単独より大きなDDを作った。したがって、
「売りに使えるかもしれない」という仮説は、少なくともこの固定条件・この
価格proxyでは支持されない。

これはショート全般の否定ではない。ショートを続けて研究するなら、まずshort専用の
損失上限、保有時間、反発時の退出、funding/borrowと清算を含むvenue-calibratedな
モデルを事前登録し、今回とは別実験として検証する必要がある。

## Required follow-up

1. EXP-2026-0010をpaper・shadow・liveへ昇格させない。
2. Binance Japanでshort可能な商品、API、手数料、funding/borrow、清算条件をread-onlyで確認する。
3. 条件を変更する場合は、今回の結果を見てからの後付け調整とならないよう新しい実験IDで事前登録する。
4. ロングの損失制御を目的にする場合は、ショート追加ではなくflat化・ポジション縮小を比較対象にする。
