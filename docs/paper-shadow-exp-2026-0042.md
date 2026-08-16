# EXP-2026-0042 Paper / Shadow Runbook

固定仕様`1.0.0-frozen`をZOOMEX公開2時間足で観測する。paperとshadowはいずれも実注文を送らず、APIキーも使用しない。

## 初回開始

```bash
PYTHONPATH=. uv run python scripts/capture_exp_2026_0042_paper_shadow.py
```

初回は最新確定足からtargetを計算するが、過去へ遡って約定しない。`paper-state.json`へtargetを予約し、次の確定2時間足openでpaper約定する。以後は同じコマンドを各2時間足の確定後に実行する。

paper約定は片道spread 5bpとslippage 5bpを不利側へ加え、約定notionalへ0.06%の手数料を適用する。保有中はZOOMEX公開Funding履歴を時刻順に一度だけ反映する。

## 保存先

- shadow観測: `var/paper-shadow/EXP-2026-0042/shadow-snapshots.jsonl`
- paper状態: `var/paper-shadow/EXP-2026-0042/paper-state.json`
- paper追記専用台帳: `var/paper-shadow/EXP-2026-0042/paper-ledger.jsonl`

## 安全境界

- 公開market dataだけを使用する
- APIキー、注文endpoint、資金移動endpointを使用しない
- 仕様、銘柄、順位条件、ロットを自動変更しない
- 不完全足、時刻不一致、warm-up不足では処理を拒否する

同じ確定足で再実行した場合は予約を維持し、重複約定しない。次の足が確定すると前回予約をその足のopenで処理し、新しいtargetを次足用に予約する。状態更新は一時ファイルからの置換、売買・Fundingは追記専用台帳で保持する。

現在のrunnerは公開REST KlineとFundingを利用するため、常駐WebSocketの遅延測定や板の実spread・部分約定はまだ扱わない。これらはshadow観測の次段階とする。

## 定期実行

ユーザーsystemd timer `crypt-ai-exp-0042-paper-shadow.timer`が、UTC偶数時の5分後にcycle wrapperを起動する。`Persistent=true`のためPC停止中に逃した周期は、次回ユーザーsystemd起動時に1回実行する。

```bash
systemctl --user status crypt-ai-exp-0042-paper-shadow.timer
systemctl --user list-timers crypt-ai-exp-0042-paper-shadow.timer
```

wrapperは`flock`で多重起動を拒否し、`logs/paper-shadow/YYYY-MM-DD.log`へ標準出力とエラーを保存する。停止する場合は次を実行する。

```bash
systemctl --user disable --now crypt-ai-exp-0042-paper-shadow.timer
```
