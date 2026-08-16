# EXP-2026-0032 検証結果

## 結論

現物ロングと同額のZOOMEX linear perpetualショートを組み合わせるベーシス収束ペアを、
現物データが取得できた4銘柄で検証した。今回の固定条件では最終equityがcash controlを
下回り、paper・shadow・liveへの昇格根拠は得られなかった。

ただし、4年間の価格方向リスクはかなり抑えられ、最大DDは-0.19〜-0.22%に収まった。
「利益を大きく得る手法」ではなく、「価格方向を相殺しながらFunding・ベーシスを受け取る
ヘッジ候補」として、次の費用・執行検証へ進める余地は残る。

## データ条件

- 現物: `data/processed/EXP-2026-0032/{SYMBOL}/spot-2h.csv`
- 先物trade/mark/Funding: `data/processed/EXP-2026-0015/{SYMBOL}/...`
- 銘柄: LINKUSDT、UNIUSDT、AVAXUSDT、AAVEUSDT
- 期間: 2022-02-01 00:00 UTC 〜 2025-12-31 22:00 UTC
- 足: 2時間足、現物・先物・markの共通時刻のみ
- Funding: 8時間決済時刻の既存先物shortへ適用

ADAUSDTとNEARUSDTは現物銘柄情報には存在するものの、現物Kline APIが対応不可を返したため、
現物・先物の両脚を揃えられるユニバースから除外した。

## 固定条件

- Basis: `perp_mark_close / spot_close - 1`
- Entry: +0.50%以上
- Exit: +0.10%以下
- 最大保有: 360本（30日）
- 1ペア: 現物100 USDT long + 先物100 USDT short
- 初期equity: 1,000 USDT
- 予備資金: 200 USDT
- 複利: なし
- パラメータ探索: なし
- 現物手数料: 0.10%
- 先物テイカー手数料: 0.06%
- 各脚: 往復spread 0.10%、片道slippage 0.05%

## 結果

| arm | 最大ペア数 | 最終equity | 純損益 | 最大DD | 実現ペア損益 | Funding | 手数料 | entry | exit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cash_control | 0 | 1,000.00 | 0.00 | 0.00% | 0.00 | 0.00 | 0.00 | 0 | 0 |
| pair_1 | 1 | 999.41 | -0.59 | -0.189% | -0.59 | -0.44 | 7.69 | 24 | 24 |
| pair_2 | 2 | 999.19 | -0.81 | -0.224% | -0.81 | -0.43 | 8.64 | 27 | 27 |
| pair_4 | 4 | 999.19 | -0.81 | -0.224% | -0.81 | -0.43 | 8.64 | 27 | 27 |

4年間・年率10%複利の基準は1,464.10 USDTであり、全armが下回った。

## Basis観察

| 銘柄 | 平均basis | 最小basis | 最大basis | entryシグナル足 |
|---|---:|---:|---:|---:|
| LINKUSDT | -0.018% | -1.014% | +0.631% | 1 |
| UNIUSDT | -0.023% | -0.468% | +0.887% | 4 |
| AVAXUSDT | -0.023% | -2.427% | +0.728% | 7 |
| AAVEUSDT | -0.022% | -1.324% | +0.889% | 15 |

Entry条件を満たすプレミアムは少なく、取引回数も少なかった。pair_2とpair_4が同一結果なのは、
最大同時保有4枠を使い切るほど同時シグナルが発生しなかったためである。

## 判定

- `research_status`: `INCONCLUSIVE_FOR_METHOD_CLASS`
- `promotion_status`: `NOT_ELIGIBLE`
- 今回の固定閾値・固定費用条件は採用しない
- 方向性ショートより最大DDを大きく抑えられる点は記録する
- 本番運用前に、実際の現物手数料通貨、片脚約定、証拠金・清算、送金・借入コストを追加検証する

## 成果物

- `artifacts/EXP-2026-0032/summary.json`
- `artifacts/EXP-2026-0032/*-signals.csv`
- `artifacts/EXP-2026-0032/*-events.csv`
- `artifacts/EXP-2026-0032/*-equity.csv`

## 参考

- [ZOOMEX V3 Get Kline](https://zoomexglobal.github.io/docs/v3/market/kline)
- [ZOOMEX V3 Get Index Price Kline](https://zoomexglobal.github.io/docs/v3/market/index-kline)
- [ZOOMEX V3 Get Funding Rate History](https://zoomexglobal.github.io/docs/v3/market/history-fund-rate)
- [ZOOMEX Spot Trading Fees](https://support.zoomex.com/en-as/articles/34797370530713)
- [ZOOMEX Perpetual Taker Fee](https://support-testnet.zoomex.com/en-us/articles/34850942819225-Taker-s-Fee-and-Maker-s-Fee-Calculation)
