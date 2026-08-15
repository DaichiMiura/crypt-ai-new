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
  config/strategies/exp-0001-risk.yaml
  config/strategies/exp-2026-0012-paper.yaml
  docs/accounting-policy.md
  docs/dependency-policy.md
  docs/references/penfold-universal-principles.md
  docs/charter.md
  docs/operations.md
  docs/paper-exp-2026-0012.md
  docs/research-policy.md
  docs/risk-policy.md
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
  experiments/approvals/EXP-2026-0012-paper.yaml
  scripts/download_binance_global_data.py
  scripts/download_binance_btcjpy_daily.py
  scripts/download_exp_2026_0014_data.py
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
  scripts/run_exp_2026_0012_paper.py
  scripts/run_paper_daily.sh
  src/crypt_ai/paper.py
  src/crypt_ai/research.py
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
