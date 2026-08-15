# Validation Report: EXP-2026-0006

## Decision

- Research status: `PASSED_RETROSPECTIVE_VALIDATION`
- Promotion status: `NEEDS_FORWARD_EVIDENCE`
- Validator: `research` quality gate（自動計算。paper/live承認ではない）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: none

固定したexit overlayは、base feeで最大DDを5年中4年改善し、改善幅中央値6.54 percentage points、CAGRは5年すべてでDonchian単独以上、closed round tripsは合計12件となり、事前登録した候補基準を満たした。adverse・stress feeでも同じ方向だった。

ただし、2022年は最大DDが3.86 points悪化した。また、対象期間はEXP-2026-0005の全期間集計で既に観測済みで、未観測OOSではない。したがって研究上は`PASSED_RETROSPECTIVE_VALIDATION`とするが、運用上は`NEEDS_FORWARD_EVIDENCE`に留め、paper・shadow・liveへの昇格は行わない。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0006-hypothesis.yaml`（`e0d1fac`）
- Runner: `scripts/run_exp_2026_0006.py`（`e0d1fac`）
- Frozen strategy implementation: `src/crypt_ai/research.py`（EXP-2026-0005）
- Data snapshot: `DATA-2026-0003`
- Evaluation windows: UTC暦年2021、2022、2023、2024、2025
- Reproduction: `uv run python scripts/build_exp_2026_0003_dataset.py` then `uv run python scripts/run_exp_2026_0006.py`
- Research venue: Binance Global Spot `BTCUSDT`
- Target venue: Binance Japan Spot（今回の結果はshadow/liveではない）

## Evaluation semantics

- Donchian単独は55日entry・20日exit、overlayは同じ55日entryと20日・2σのBollinger平均回帰exitを使用した。
- シグナル状態は2020年からの全履歴で計算し、各年だけを切り出して初期資金1,000 USDT相当にリセットした。
- 年初の`desired_position=1`は、年初始値で新しいBUYとして処理した。2021、2024、2025が該当した。
- 年末の未決済ポジションは強制決済せず、終値mark-to-marketとした。2023、2024は年末未決済だった。
- この評価方法は前年からの状態を使う一方、資金を年ごとにリセットする診断であり、連続運用損益や独立した資本曲線ではない。

## Data integrity and accounting

- 日足2,192本、欠損0、重複0、日足間隔の欠損0だった。
- 線形補間時間足を含む日足は15本（0.6843%）。年次評価で合成日足上の約定は0件だった。
- 確定日足のシグナルを次日始値へ遅延させた。
- base feeは片道0.1%、adverse 0.15%、stress 0.2%、往復spread 0.05%、片道slippage 0.05%である。
- Binance Japan固有のtick size、最小数量、最小notional、部分約定、注文拒否は未実装である。

## Results

Base feeの年次結果は次のとおり。

| 年 | Donchian CAGR | Overlay CAGR | Donchian最大DD | Overlay最大DD | DD改善幅 | Overlay往復数 |
|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 46.24% | 104.92% | -38.22% | -31.68% | +6.54 points | 3 |
| 2022 | -15.93% | -15.59% | -16.81% | -20.67% | -3.86 points | 1 |
| 2023 | 17.80% | 61.08% | -26.65% | -15.55% | +11.10 points | 3（年末未決済あり） |
| 2024 | 45.11% | 110.37% | -20.89% | -16.14% | +4.75 points | 1（年末未決済あり） |
| 2025 | -12.32% | 7.99% | -21.83% | -14.33% | +7.50 points | 4 |

### Preregistered scorecard

| 条件 | 結果 | 判定 |
|---|---:|---|
| 最大DD改善年数 | 4 / 5 | 合格（3年以上） |
| 最大DD改善幅中央値 | +6.54 points | 合格（正） |
| CAGRがDonchian以上の年数 | 5 / 5 | 合格（3年以上） |
| Overlay closed round trips | 12 | 合格（10以上） |

Fee感度でも、最大DD改善年数4 / 5、CAGR優位年数5 / 5、closed round trips 12は変わらなかった。最大DD改善幅中央値はadverseで6.60 points、stressで6.66 pointsだった。

## Interpretation

EXP-2026-0005の改善は2025年だけには限定されず、2021、2023、2024でも再現した。Donchianの20日exitを平均回帰exitへ置き換えることで、短い押し目で退出せず、上昇トレンドへの滞在時間が延びた可能性が高い。

一方、2022年の下落相場では、overlayはDonchian単独より最大DDが悪化した。overlayは下側band割れで即時損切りせず、中心線への戻りを待つため、継続的な下落では損失抑制にならない。この結果から「常に安全なexit」とは評価しない。

2021〜2025年は既に全期間結果を見たデータであるため、今回の合格を独立OOSの再現成功とは扱わない。年次リセット、年初の状態引継ぎ、年末未決済も結果解釈の制約である。

## Required follow-up

1. パラメータを変更せず、EXP-2026-0005以降の未観測データを新しいスナップショットとして固定し、真のforward testを行う。
2. 2022年型の持続的下落で最大DDが悪化する条件を、結果後のパラメータ調整なしで失敗モードとして分析する。
3. 年末未決済を含む期間について、連続資本曲線、保有率、time-under-water、回復時間、未実現損益を追加評価する。
4. Binance Japanのread-onlyデータを使うshadow設計と、公式fee・注文制約のスナップショットを準備する。
5. 独立したforward/shadow証拠が揃うまで、paper・shadow・liveへの昇格申請を行わない。
