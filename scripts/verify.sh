#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

required_files=(
  .gitignore
  AGENTS.md
  ARCHITECTURE.md
  README.md
  pyproject.toml
  config/risk-limits.yaml
  config/paper-risk-limits.yaml
  config/allocation.yaml
  config/strategies/exp-0001-risk.yaml
  config/strategies/exp-2026-0012-paper.yaml
  config/strategies/exp-2026-0042-paper-shadow.yaml
  docs/accounting-policy.md
  docs/dependency-policy.md
  docs/references/penfold-universal-principles.md
  docs/charter.md
  docs/operations.md
  docs/paper-exp-2026-0012.md
  docs/paper-shadow-exp-2026-0042.md
  deploy/systemd/crypt-ai-exp-0042-paper-shadow.service
  deploy/systemd/crypt-ai-exp-0042-paper-shadow.timer
  docs/research-policy.md
  docs/risk-policy.md
  docs/allocation-policy.md
  docs/venue-data-policy.md
  docs/validation-policy.md
  experiments/registry/EXP-2026-0001-hypothesis.yaml
  experiments/registry/DATA-2026-0001-manifest.yaml
  experiments/registry/EXP-2026-0001-validation.md
  experiments/registry/EXP-2026-0002-hypothesis.yaml
  experiments/registry/DATA-2026-0002-manifest.yaml
  experiments/registry/EXP-2026-0002-validation.md
  experiments/registry/EXP-2026-0003-hypothesis.yaml
  experiments/registry/DATA-2026-0003-manifest.yaml
  experiments/registry/EXP-2026-0003-validation.md
  experiments/registry/EXP-2026-0004-hypothesis.yaml
  experiments/registry/EXP-2026-0004-validation.md
  experiments/registry/EXP-2026-0005-hypothesis.yaml
  experiments/registry/EXP-2026-0005-validation.md
  experiments/registry/EXP-2026-0006-hypothesis.yaml
  experiments/registry/EXP-2026-0006-validation.md
  experiments/registry/EXP-2026-0007-hypothesis.yaml
  experiments/registry/EXP-2026-0007-validation.md
  experiments/registry/DATA-2026-0004-manifest.yaml
  experiments/registry/EXP-2026-0008-hypothesis.yaml
  experiments/registry/EXP-2026-0008-validation.md
  experiments/registry/EXP-2026-0009-hypothesis.yaml
  experiments/registry/EXP-2026-0009-validation.md
  experiments/registry/EXP-2026-0010-hypothesis.yaml
  experiments/registry/EXP-2026-0010-validation.md
  experiments/registry/EXP-2026-0011-hypothesis.yaml
  experiments/registry/EXP-2026-0011-validation.md
  experiments/registry/EXP-2026-0012-hypothesis.yaml
  experiments/registry/EXP-2026-0012-validation.md
  experiments/registry/EXP-2026-0013-hypothesis.yaml
  experiments/registry/EXP-2026-0013-validation.md
  experiments/registry/EXP-2026-0014-hypothesis.yaml
  experiments/registry/EXP-2026-0014-validation.md
  experiments/registry/EXP-2026-0015-hypothesis.yaml
  experiments/registry/EXP-2026-0015-strategy.yaml
  experiments/registry/EXP-2026-0031-hypothesis.yaml
  experiments/registry/EXP-2026-0031-validation.md
  experiments/registry/EXP-2026-0032-hypothesis.yaml
  experiments/registry/EXP-2026-0032-validation.md
  experiments/registry/EXP-2026-0033-hypothesis.yaml
  experiments/validation/EXP-2026-0033-validation.md
  experiments/registry/EXP-2026-0034-hypothesis.yaml
  experiments/validation/EXP-2026-0034-validation.md
  experiments/registry/EXP-2026-0035-hypothesis.yaml
  experiments/validation/EXP-2026-0035-validation.md
  experiments/registry/EXP-2026-0036-hypothesis.yaml
  experiments/validation/EXP-2026-0036-validation.md
  experiments/registry/EXP-2026-0037-hypothesis.yaml
  experiments/validation/EXP-2026-0037-validation.md
  experiments/registry/EXP-2026-0038-hypothesis.yaml
  experiments/validation/EXP-2026-0038-validation.md
  experiments/registry/EXP-2026-0039-hypothesis.yaml
  experiments/validation/EXP-2026-0039-validation.md
  experiments/registry/EXP-2026-0040-hypothesis.yaml
  experiments/registry/EXP-2026-0041-hypothesis.yaml
  experiments/registry/EXP-2026-0042-hypothesis.yaml
  experiments/registry/EXP-2026-0043-hypothesis.yaml
  experiments/registry/EXP-2026-0044-hypothesis.yaml
  experiments/registry/EXP-2026-0045-hypothesis.yaml
  experiments/registry/EXP-2026-0046-hypothesis.yaml
  experiments/registry/EXP-2026-0047-hypothesis.yaml
  experiments/registry/EXP-2026-0048-hypothesis.yaml
  experiments/registry/EXP-2026-0049-hypothesis.yaml
  experiments/registry/EXP-2026-0050-hypothesis.yaml
  experiments/registry/EXP-2026-0051-hypothesis.yaml
  experiments/validation/EXP-2026-0040-validation.md
  experiments/validation/EXP-2026-0041-validation.md
  experiments/validation/EXP-2026-0042-validation.md
  experiments/validation/EXP-2026-0043-validation.md
  experiments/validation/EXP-2026-0044-validation.md
  experiments/validation/EXP-2026-0045-validation.md
  experiments/validation/EXP-2026-0046-validation.md
  experiments/validation/EXP-2026-0047-validation.md
  experiments/validation/EXP-2026-0048-validation.md
  experiments/validation/EXP-2026-0048-diagnostic.md
  experiments/validation/EXP-2026-0049-validation.md
  experiments/validation/EXP-2026-0050-validation.md
  experiments/validation/EXP-2026-0051-validation.md
  experiments/validation/EXP-2026-0035-drawdown-diagnostic.md
  experiments/registry/EXP-2026-0030-hypothesis.yaml
  experiments/registry/EXP-2026-0030-validation.md
  experiments/registry/DATA-2026-0005-manifest.yaml
  experiments/approvals/EXP-2026-0012-paper.yaml
  experiments/approvals/EXP-2026-0042-paper-shadow.yaml
  scripts/download_binance_global_data.py
  scripts/download_binance_btcjpy_daily.py
  scripts/download_exp_2026_0014_data.py
  scripts/download_zoomex_exp_2026_0015_data.py
  scripts/build_exp_2026_0001_dataset.py
  scripts/build_exp_2026_0002_dataset.py
  scripts/build_exp_2026_0003_dataset.py
  scripts/build_exp_2026_0007_dataset.py
  scripts/run_exp_2026_0008.py
  scripts/run_exp_2026_0009.py
  scripts/run_exp_2026_0001.py
  scripts/run_exp_2026_0002.py
  scripts/run_exp_2026_0003.py
  scripts/run_exp_2026_0004.py
  scripts/run_exp_2026_0005.py
  scripts/run_exp_2026_0006.py
  scripts/run_exp_2026_0007.py
  scripts/run_exp_2026_0010.py
  scripts/run_exp_2026_0011.py
  scripts/run_exp_2026_0012.py
  scripts/run_exp_2026_0013.py
  scripts/run_exp_2026_0014.py
  scripts/run_exp_2026_0031.py
  scripts/run_exp_2026_0032.py
  scripts/run_exp_2026_0033.py
  scripts/run_exp_2026_0034.py
  scripts/run_exp_2026_0035.py
  scripts/run_exp_2026_0036.py
  scripts/run_exp_2026_0037.py
  scripts/run_exp_2026_0038.py
  scripts/run_exp_2026_0039.py
  scripts/run_exp_2026_0040.py
  scripts/run_exp_2026_0041.py
  scripts/run_exp_2026_0042.py
  scripts/run_exp_2026_0043.py
  scripts/run_exp_2026_0044.py
  scripts/run_exp_2026_0045.py
  scripts/run_exp_2026_0046.py
  scripts/run_exp_2026_0047.py
  scripts/run_exp_2026_0048.py
  scripts/run_exp_2026_0049.py
  scripts/run_exp_2026_0050.py
  scripts/run_exp_2026_0051.py
  scripts/capture_exp_2026_0042_paper_shadow.py
  scripts/run_exp_2026_0042_paper_shadow_cycle.sh
  scripts/diagnose_exp_2026_0035_drawdown.py
  scripts/download_zoomex_exp_2026_0032_spot_data.py
  scripts/run_exp_2026_0030.py
  scripts/run_exp_2026_0012_paper.py
  scripts/run_paper_daily.sh
  src/crypt_ai/paper.py
  src/crypt_ai/allocation.py
  src/crypt_ai/portfolio.py
  src/crypt_ai/long_ladder.py
  src/crypt_ai/execution.py
  src/crypt_ai/research.py
  src/crypt_ai/void_short.py
  src/crypt_ai/basis.py
  src/crypt_ai/basis_backtest.py
  src/crypt_ai/funding_carry.py
  src/crypt_ai/low_volatility.py
  src/crypt_ai/pairs_mean_reversion.py
  tests/scripts/test_run_exp_2026_0031.py
  tests/scripts/test_run_exp_2026_0032.py
  tests/scripts/test_run_exp_2026_0033.py
  tests/scripts/test_run_exp_2026_0034.py
  tests/scripts/test_run_exp_2026_0035.py
  tests/scripts/test_run_exp_2026_0036.py
  tests/scripts/test_run_exp_2026_0037.py
  tests/scripts/test_run_exp_2026_0038.py
  tests/scripts/test_run_exp_2026_0039.py
  tests/scripts/test_run_exp_2026_0040.py
  tests/scripts/test_run_exp_2026_0041.py
  tests/scripts/test_run_exp_2026_0042.py
  tests/scripts/test_run_exp_2026_0043.py
  tests/scripts/test_run_exp_2026_0044.py
  tests/scripts/test_run_exp_2026_0045.py
  tests/scripts/test_run_exp_2026_0046.py
  tests/scripts/test_run_exp_2026_0047.py
  tests/scripts/test_run_exp_2026_0048.py
  tests/scripts/test_run_exp_2026_0049.py
  tests/scripts/test_run_exp_2026_0050.py
  tests/scripts/test_run_exp_2026_0051.py
  tests/scripts/test_capture_exp_2026_0042_paper_shadow.py
  tests/scripts/test_diagnose_exp_2026_0035_drawdown.py
  tests/scripts/test_download_zoomex_exp_2026_0032_spot_data.py
  tests/src/crypt_ai/test_basis.py
  tests/src/crypt_ai/test_basis_backtest.py
  tests/src/crypt_ai/test_funding_carry.py
  tests/src/crypt_ai/test_low_volatility.py
  tests/src/crypt_ai/test_pairs_mean_reversion.py
  tests/src/crypt_ai/test_long_ladder.py
  templates/hypothesis.yaml
  templates/data-manifest.yaml
  templates/validation-report.md
)

for file in "${required_files[@]}"; do
  if [[ ! -s "$file" ]]; then
    echo "missing or empty required file: $file" >&2
    exit 1
  fi
done

assert_exact_setting() {
  local pattern="$1"
  local description="$2"
  if ! grep -Eq "$pattern" config/risk-limits.yaml; then
    echo "unsafe bootstrap setting: $description" >&2
    exit 1
  fi
}

assert_exact_setting '^environment: paper$' 'environment must be paper'
assert_exact_setting '^live_trading_enabled: false$' 'live trading must be disabled'
assert_exact_setting '^  kill_switch_engaged: true$' 'kill switch must be engaged'
assert_exact_setting '^  reject_on_unknown_state: true$' 'unknown states must be rejected'
assert_exact_setting '^  reject_on_reconciliation_failure: true$' 'reconciliation failures must be rejected'
assert_exact_setting '^  reject_on_risk_engine_failure: true$' 'risk-engine failures must be rejected'

assert_paper_setting() {
  local pattern="$1"
  local description="$2"
  if ! grep -Eq "$pattern" config/paper-risk-limits.yaml; then
    echo "invalid paper setting: $description" >&2
    exit 1
  fi
}

assert_paper_setting '^environment: paper$' 'paper environment must be explicit'
assert_paper_setting '^live_trading_enabled: false$' 'paper config must not enable live trading'
assert_paper_setting '^allowed_exchanges: \[paper\]$' 'paper must use the synthetic exchange only'
assert_paper_setting '^  reject_on_unknown_state: true$' 'paper must reject unknown states'
assert_paper_setting '^  reject_on_reconciliation_failure: true$' 'paper must reject reconciliation failures'
assert_paper_setting '^  reject_on_risk_engine_failure: true$' 'paper must reject risk-engine failures'

if ! grep -Eq '^environment: paper$' config/allocation.yaml; then
  echo 'invalid allocation setting: allocation must be paper-only' >&2
  exit 1
fi
if ! grep -Eq '^schema_version: 1$' config/allocation.yaml; then
  echo 'invalid allocation setting: schema version is missing' >&2
  exit 1
fi

if ! grep -Eq '^strategy_id: EXP-0001$' config/strategies/exp-0001-risk.yaml; then
  echo 'invalid strategy setting: strategy ID is missing' >&2
  exit 1
fi
if ! grep -Eq '^environment: paper$' config/strategies/exp-0001-risk.yaml; then
  echo 'invalid strategy setting: strategy must be paper-only' >&2
  exit 1
fi

if grep -Eq '^  max_[a-z_]+: ([1-9][0-9]*|0\.[0-9]*[1-9][0-9]*)$' config/risk-limits.yaml; then
  echo 'unsafe bootstrap setting: all numeric risk limits must remain zero' >&2
  exit 1
fi

echo 'bootstrap governance checks passed'
