# XGBoost adoption record

## Decision

- 対象: EXP-2026-0054の表形式・短期リターン回帰候補
- 採用物: XGBoost 3.4系列（初回lockは3.4.1）
- upstream: <https://github.com/dmlc/xgboost>
- package: <https://pypi.org/project/xgboost/>
- license: Apache-2.0
- scope: researchのみ。paper、shadow、live、注文経路では使用しない

成熟した勾配ブースティング実装、CPU向けhistアルゴリズム、回帰APIを利用するため採用する。
木学習器を独自実装せず、このリポジトリは特徴量の時点整合性、費用込み会計、実験台帳、
昇格判定を所有する。線形Ridgeを依存しない比較基準として残す。

## Maintenance and security review

- 2026-08-17時点でPyPIの最新リリースは3.4.1（2026-08-15公開）であり、Python 3.12以上を要求する。
- upstreamはリリース手順とセキュリティポリシーを公開している。
- upstreamのセキュリティ方針上、サポート対象は最新リリースである。このため3.4系列内でlockし、
  更新時には本実験の再現テストと費用込みバックテストを再実行する。
- Python wheelはネイティブライブラリを含み容量が大きい。LinuxではXGBoost 3.4.1の依存として
  `nvidia-nccl-cu13` 2.31.2（取得量約241 MB）もlockされた。実験ではGPU・NCCLを使わないが、
  最新版のsecurity supportを優先して標準packageを採用する。供給元を公式PyPIに限定し、`uv.lock`のhashで固定する。
- XGBoostのモデルファイルは一般的な入力データではない。未信頼・外部取得のモデルを読み込まず、
  当該実験が同一プロセス内で学習したモデルだけを推論に使う。

参照:

- <https://xgboost.readthedocs.io/en/latest/security.html>
- <https://xgboost.readthedocs.io/en/stable/contrib/release.html>

## Runtime boundary

- `research` dependency groupからだけ導入する。
- scikit-learnを要求する`XGBRegressor`は使わず、`xgboost.DMatrix`と`xgboost.train`のnative APIを使う。
- CPUだけを使い、GPU、分散学習、外部サービス、ネットワークを要求しない。
- APIキー、取引所権限、秘密情報、注文権限を与えない。
- native APIの`seed`を固定し、`nthread=1`とする。学習時のライブラリ版と設定を実験結果に記録する。
- 学習済みモデルを本番注文判断へ直接配置しない。EXP-2026-0054は研究ゲートの評価だけを行う。

## Known limitations

- native wheelと追加依存により、研究環境の容量と更新面が増える。
- floating-point計算や将来の実装変更により、異なるCPU・バージョン間のbit単位一致は保証しない。
- 非線形モデルは少標本で過学習しやすい。モデル族とハイパーパラメータを結果確認前に固定し、
  開発期間で一つを選んだ後に封印holdoutを一度だけ開く。
- 予測精度が良くても、費用・約定・資金制約を含む損益が正であるとは限らない。

## Removal and rollback

不具合、脆弱性、保守停止、再現性不足が判明した場合は、XGBoost候補を不採用として実験台帳に残し、
`research` dependency groupから削除してlockfileを再生成する。Ridge比較基準とcash基準は維持でき、
削除してもpaper・shadow・liveの挙動は変わらない。
