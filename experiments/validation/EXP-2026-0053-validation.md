# EXP-2026-0053 Validation Report

## 判定

- Research status: `REJECTED`
- Promotion status: `NOT_ELIGIBLE`
- paper / shadow / live変更: なし

walk-forward ridge回帰によるentry時volatility sizingは、2025 retrospective evaluationで利益と取引数を維持したが、主要目的の最大drawdownを改善できなかった。固定した3 percentage points改善条件に対して0.63 points悪化したため棄却する。

## 固定仕様

- EXP-2026-0042と同じZOOMEX linear perpetual固定4銘柄、30日相対momentum、週次top2、最下位転落退出。
- 特徴量は30日momentum、市場中央値momentum、cross-sectional rank、過去84本volatilityの4個。
- targetは判断時点closeから次の84本close-to-close returnの母標準偏差。
- 標準化ridge回帰、800 iterations、learning rate 0.05、L2 0.01、最低80標本、1 variant。
- train targetのnearest-rank 75th percentile以上をhigh riskと予測。
- 通常は100 USDT x 2 lots、high riskまたは学習不足は100 USDT x 1 lot。保有中はresizeしない。
- 各判断時点以前にtarget終端closeが到来した標本だけで再学習。

## 2025 retrospective結果

| 指標 | EXP-0042 baseline | Volatility sizing | 費用2倍 |
|---|---:|---:|---:|
| net PnL | +235.34 USDT | +238.44 USDT | +226.05 USDT |
| baseline PnL retention | 100.00% | 101.32% | 96.05% |
| return | +17.38% | +19.07% | +18.57% |
| max DD | -10.11% | -10.74% | -11.11% |
| entry / exit | 19 / 21 | 19 / 21 | 19 / 21 |
| Funding | -7.75 USDT | -7.34 USDT | -7.34 USDT |
| fee | 4.97 USDT | 4.56 USDT | 9.12 USDT |
| mean gross | 135.57 USDT | 129.52 USDT | 129.52 USDT |

取引数、90%利益維持、費用2倍で正という3条件は満たした。最大DD改善だけが未達で、改善値は`-0.00632`、すなわち0.63 percentage pointsの悪化だった。

## 失敗原因

2025年のentry候補40件のうちhigh risk判定は4件（10%）だけだった。予測MAEは2時間return volatility単位で0.00327だった。

最大DDはbaseline、候補とも2025-10-10 20:00 UTCに発生した。直前の10月7日判断でモデルはAVAXを0.01186、LINKを0.01166と予測し、閾値0.01587未満のlow riskとして両方を2 lotsでentryした。しかし実現targetはAVAX 0.02883、LINK 0.02786で、急なvolatility上昇を大幅に過小予測した。このfalse negativeにより、改善対象だった最大下落で元本を縮小できなかった。

一方、1 lotへ縮小したentryなどにより2025絶対PnLはbaselineを3.10 USDT上回った。ただしdevelopment PnLはbaseline +354.02 USDTに対して+250.24 USDTまで低下しており、安定した改善とはいえない。利益差だけを理由にDD条件を事後変更しない。

候補の2025開始equityは過去の縮小によりbaselineより低い。10月の下落額が十分に減らなかったため、相対DDはbaselineより悪化した。単に平均grossを下げても、最大損失局面を正しく特定できなければ目的を達成できない。

## データ・時点整合性

- source: ZOOMEX Global public V3 REST API、snapshot `DATA-2026-0004`。
- 内部時刻はUTC。4銘柄の確定2時間足とFundingを同期検査。
- 特徴量は判断足closeまで、約定は次足open。
- target終端indexが現在の判断index以下の標本だけをtrainへ投入。
- 標準化、ridge係数、risk thresholdは各時点のtrainだけから計算。
- 予測値、閾値、train標本数、lot数、実現targetを監査CSVへ保存。

## 会計・安全境界

- base: taker fee片道0.06%、round-trip spread 0.10%、slippage片道0.05%。
- stress: 上記をすべて2倍。
- 100 USDT固定lot、1銘柄最大2 lots、最大2 long、gross 400 USDT、reserve 200 USDT。
- 既存portfolio会計の`desired_long_lot_count`を利用し、新しい会計経路を追加していない。
- allocation rejectionは0。実注文、認証情報、取引権限は使用していない。
- 学習不能または特徴量検査不能時は安全側の1 lotとする。

## 制約

- 2025年は既存研究で観測済みで、未観測OOSではない。
- 固定4銘柄の少数標本で、他銘柄・他期間への一般化を示さない。
- targetは価格volatilityで、Funding、途中退出、約定品質を直接表さない。
- entry時だけサイズを決め、保有中のvolatility変化ではresizeしない。
- ZOOMEX公開履歴上の研究結果であり、実約定の有効性は未検証。

## 結論

このsizingをEXP-2026-0042のpaper/shadow経路へ追加しない。10月の失敗を見た後にpercentile、特徴量、更新頻度を変更しない。次のML研究では価格volatility単独ではなく、固定損失上限に直接対応する将来最大逆行幅（maximum adverse excursion）をtarget候補にできるが、別IDで事前登録し、可能なら新しいforward期間を待つ必要がある。

## 再現

```bash
uv run python -m pytest tests/scripts/test_run_exp_2026_0053.py tests/scripts/test_run_exp_2026_0052.py tests/scripts/test_run_exp_2026_0042.py
uv run python scripts/run_exp_2026_0053.py
./scripts/verify.sh
```

成果物: `artifacts/EXP-2026-0053/summary.json`、walk-forward volatility predictions、4銘柄sizing signals、baseline・candidate・stress events/equity。
