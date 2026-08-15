# Validation Report: EXP-2026-0014

## Decision

- Research status: `PASSED_RETROSPECTIVE_VALIDATION`
- Promotion status: `NEEDS_FORWARD_EVIDENCE`
- Validator: `Independent Validation`（固定runner再実行、equity再計算、stop不変条件照合）
- Reviewed at (UTC): 2026-08-15
- Scope of approval: 研究上の銘柄横展開候補のみ。paper銘柄追加・shadow・liveは未承認

EXP-2026-0012の固定パラメータをETH・SOL・XRPへそのまま適用した。2022〜2025年の
共通評価期間で、ETHとSOLが銘柄別候補基準を満たし、XRPはDD改善があったものの合算
最終資産維持率が89.22%で90%基準を下回った。3銘柄中2銘柄が候補となったため、事前登録
どおり過去データ上の一般化候補とする。

これは「アルトコイン全般で有効」という証明ではない。USDTのGlobal proxy、4年、3銘柄、
少数取引による診断であり、Binance JapanのJPY価格・約定・paper銘柄追加を承認するものではない。

## Frozen artifacts

- Preregistration: `experiments/registry/EXP-2026-0014-hypothesis.yaml`（`e281c47`）
- Dataset metadata: `var/exp-2026-0014-data.json`（ローカル生成物）
- Runner: `scripts/run_exp_2026_0014.py`
- Downloader: `scripts/download_exp_2026_0014_data.py`
- Generated summary: `artifacts/EXP-2026-0014/summary.json`
- Reproduction: `uv run python scripts/download_exp_2026_0014_data.py` then `uv run python scripts/run_exp_2026_0014.py`

## Data and methodology

- Binance Global Spot公開REST APIから、ETHUSDT・SOLUSDT・XRPUSDTの日足を取得した。
- 評価期間は全銘柄共通で2022-01-01〜2025-12-31 UTC、各年を1,000 USDTで独立評価した。
- ETH/XRPは2020-01-01開始、SOLは2020-08-11開始で、2022年開始前に200日超のwarm-upを確保した。
- 3銘柄とも重複0、欠損0、補間0だった。
- baselineはSMA200付きDonchian 55/20、ATR版は同じentryと20日単純ATR×3のラチェットexitである。
- ATRの終値判定、次日始値約定、fee、spread、slippageはEXP-2026-0012から変更していない。

## Base-fee results

| 銘柄 | DD改善年 | DD改善中央値 | CAGRがbaseline以上 | 合算資産維持率 | ATR exits | 銘柄判定 |
|---|---:|---:|---:|---:|---:|---|
| ETHUSDT | 2/4 | +0.84pt | 3/4 | 97.88% | 4 | 候補 |
| SOLUSDT | 2/4 | +6.41pt | 4/4 | 113.60% | 7 | 候補 |
| XRPUSDT | 3/4 | +9.79pt | 3/4 | 89.22% | 8 | 基準未達 |

XRPはDD改善が最も大きかったが、2024年のbaseline最終資産2,101.44に対してATR版は
1,252.21となり、上昇局面の取り逃しが合算維持率を押し下げた。ETHは効果量が小さく、
2024年はbaselineと同値だった。SOLは2024〜2025年にDDと最終資産を改善した。

## Cost sensitivity

adverse・stressでも方向性は変わらなかった。

| 費用 | ETH維持率 / DD中央値 | SOL維持率 / DD中央値 | XRP維持率 / DD中央値 |
|---|---:|---:|---:|
| base | 97.88% / +0.84pt | 113.60% / +6.41pt | 89.22% / +9.79pt |
| adverse | 97.88% / +0.82pt | 113.59% / +6.38pt | 89.24% / +9.77pt |
| stress | 97.87% / +0.80pt | 113.58% / +6.35pt | 89.25% / +9.75pt |

## Independent validation

- 3銘柄すべてのequity CSVから最終資産・最大DDを再計算し、summaryと一致した。
- 全ATR exitについて、exit判定日は保有中、翌日はdesired positionが0であることを確認した。
- 保有区間のtrailing stopが単調非減少であることを確認した。
- 3銘柄のデータに補間行がなく、補間日約定も0件だった。
- `pytest` 41件、`compileall`、`scripts/verify.sh`、`git diff --check`を通過した。

## Preregistered decision

| 条件 | 結果 | 判定 |
|---|---:|---|
| 3銘柄中2銘柄以上が銘柄候補基準を満たす | ETH・SOLの2銘柄 | 合格 |
| データ品質・会計・stop不変条件 | 全検査通過 | 合格 |
| 3銘柄中2銘柄以上でDD中央値が非正 | 0銘柄 | 棄却条件なし |
| 3銘柄中2銘柄以上で維持率80%未満 | 0銘柄 | 棄却条件なし |

## Interpretation and follow-up

今回の結果は、EXP-2026-0012をBTC以外へ横展開する根拠を強める。ただし銘柄差が明確で、
XRPのようにDDを抑えても収益を取り逃す場合がある。したがって3銘柄を一括でpaperへ
追加せず、次の順序にする。

1. ETH・SOLを追加候補として個別にJPYデータ校正する。
2. XRPはpaper追加を保留し、別の期間または新しいデータで再評価する。
3. Global proxyの結果だけで資金配分を決めない。
4. paper追加は銘柄ごとの設定、最小注文、流動性、監視、JPY価格basisを別承認する。

## Limitations

1. USDT建てGlobal proxyであり、Binance JapanのJPY価格basis・spread・約定可能性を表さない。
2. 2022〜2025年はBTC研究と同じ市場環境を共有し、独立OOSではない。
3. 銘柄数3、年数4、取引数も少なく、暗号資産全体への一般化はできない。
4. 結果を見てATR倍率、窓、銘柄を変更していないが、銘柄選定ルール自体は3銘柄に限定されている。
5. EXP-2026-0014の結果だけでpaper・shadow・liveを承認しない。
