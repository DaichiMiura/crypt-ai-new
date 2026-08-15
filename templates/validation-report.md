# Validation Report: EXP-YYYY-NNNN

## Decision

- Status: `REJECTED | NEEDS_EVIDENCE | PAPER_APPROVED`
- Validator:
- Reviewed at (UTC):
- Scope of approval:
- Expiry or review date:

`NEEDS_EVIDENCE` は承認ではない。現段階では `PAPER_APPROVED` より先へ昇格できない。

## Frozen artifacts

- Code commit:
- Strategy config hash:
- Data snapshot ID:
- Research venue:
- Execution venue:
- Venue/pair mapping:
- Fee model version:
- Execution model version:
- Risk policy version:
- Reproduction command:

## Independence statement

- Strategy implementer:
- Validator:
- Conflicts or shared context:

## Findings

### Data integrity

- Decision-time availability:
- Research proxy limitations and target-venue differences:
- Symbol, quote asset, and market-type mapping:
- Look-ahead checks:
- Missing/duplicate data:
- Timezone and timestamp semantics:
- Dataset selection bias:

### Backtest accounting

- Fees, spread, slippage:
- Order and fill semantics:
- Position and cash reconciliation:
- Rejected/partial fills and rounding:
- Capital and leverage constraints:

### Statistical robustness

- Baseline comparison:
- Win rate, average win, average loss, and cost-adjusted expectancy:
- Walk-forward/OOS result:
- Number of variants tried:
- Parameter sensitivity:
- Regime and concentration risk:
- Tail risk and drawdown:
- Risk-of-ruin assumptions and estimate:

### Methodology and execution

- Setup definition:
- Entry, stop, exit, and no-trade rules:
- Simplicity and parameter-count assessment:
- Money-management and sizing method:
- Size increase/decrease conditions:
- Shadow feed, latency, spread, and simulated fill result:

### Safety and operations

- Limit rejection tests:
- Stale/invalid data tests:
- Restart and duplicate-order tests:
- Reconciliation and kill-switch tests:
- Monitoring and rollback:

## Results

主要指標だけでなく、全期間・OOS・相場環境別の結果、取引数、回転率、費用、最大ドローダウンを記載する。

## Unresolved risks

-
## Rejection or approval rationale


## Required follow-up

-
