# EXP-2026-0049 Validation Report

## 判定

- Research status: `REJECTED`
- Promotion status: `NOT_ELIGIBLE`
- 実注文、paper、shadow、liveへの変更: なし

選択的Fundingキャリーは全期間・OOSとも取引0だった。これは損失を回避したcash controlと同値であり、収益仮説を支持しない。事前登録した最低30往復、正のOOS純損益、費用2倍stressで正のOOS純損益をすべて満たさない。

## 事前登録条件

- 親実験: `EXP-2026-0048`
- 直前6回の確定Funding平均。現在Fundingは除外。
- 負Funding 2銘柄をlong、正Funding 2銘柄をshort。
- 予定6 Fundingの予測差が基本往復費用0.0032の1.5倍、`0.0048`以上。
- 過去30日・2時間returnによるlong/short平均β差が0.25以下。
- 6 Fundingイベント（48時間）ごとに更新し、次の2時間足openへ遅延。
- 固定50 USDT、最大2 long + 2 short、1 variant。

## 結果

| 指標 | 全期間 | OOS |
|---|---:|---:|
| 更新回数 | 715 | 92 |
| 正負候補不足 | 581 | 70 |
| 費用edge不足 | 134 | 22 |
| accepted | 0 | 0 |
| entry / exit | 0 / 0 | 0 / 0 |
| net PnL | 0 USDT | 0 USDT |
| max drawdown | 0% | 0% |

正負候補とβ条件が揃った時点でも、予定6 Fundingのprojected carryは全期間最大`0.00400269`で閾値`0.0048`未満だった。OOSでは平均`0.00052609`、最大`0.00097404`で、最大値でも閾値の20.3%に過ぎない。β差最大はOOSで0.2427であり、候補が揃った22回を止めた直接要因はβ制約ではなく費用edgeだった。

## 原因の解釈

固定6銘柄では、多くの更新時点で負Funding long 2銘柄と正Funding short 2銘柄が同時に存在しない。存在する場合でもFunding差は現実的な4脚往復費用を補える規模ではなかった。

したがってEXP-2026-0048の損失は、単にgateが不足していたのではなく、このvenue・固定universe・時間粒度ではFunding収益源そのものが費用に対して小さすぎた可能性が高い。閾値を事後的に下げれば取引は作れるが、breakeven未満の取引を許すため、本仮説の改善とは扱わない。

## データ整合性

- 研究・target venue: ZOOMEX Global linear USDT perpetual。
- データsnapshot: `DATA-2026-0005`。
- 内部時刻: UTC。2時間足と8時間Fundingの連続性・同期を検査。
- Funding時刻tのrateをtの判断へ使用していない。
- βはtの現在closeを除く過去360本のreturnだけで計算。
- シグナルは次の2時間足openへ1 bar遅延。
- 補間行、欠測、重複、非正価格、非有限Fundingは拒否。

## 会計・費用

baseは片道taker fee 0.0006、round-trip spread 0.001、各fill slippage 0.0005。stressはすべて2倍。accepted signalがなかったため両armとも取引、Funding、fee、価格損益は0でcash controlと一致した。取引がないため会計上の利益先取りや資金二重使用は発生していない。

## 独立性とOOS上の注意

実装者が結果資料を作成しており、独立検証者による承認ではない。またOOS 2025-07-01〜2025-12-31はEXP-2026-0048の診断後に再利用しているため未観測証拠ではない。ただし全期間でもprojected carryが事前閾値へ一度も届かないという棄却結果であり、昇格判断には使わない。

## 未解決リスク

- 6銘柄固定でpoint-in-time universeではない。
- より広いuniverse、異なるvenue、spot-perpetual basisではFunding差の規模が異なる可能性がある。
- 公開Kline/Fundingは板、部分約定、証拠金、清算、注文拒否を再現しない。
- ZOOMEX公開履歴上の研究結果であり、実約定での有効性は未検証。

## 再現

```bash
uv run python -m pytest tests/src/crypt_ai/test_funding_carry.py tests/scripts/test_run_exp_2026_0049.py
uv run python scripts/run_exp_2026_0049.py
./scripts/verify.sh
```

主要成果物:

- `artifacts/EXP-2026-0049/summary.json`
- `artifacts/EXP-2026-0049/base-events.csv`
- `artifacts/EXP-2026-0049/base-equity.csv`
- `artifacts/EXP-2026-0049/*-signals.csv`
