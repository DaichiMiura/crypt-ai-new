#!/usr/bin/env python3
"""EXP-2026-0049の選択的Fundingキャリー追試を実行する。"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.funding_carry import (  # noqa: E402
    SelectiveFundingCarrySignalConfig,
    prepare_selective_funding_carry_signals,
)
from scripts.run_exp_2026_0048 import (  # noqa: E402
    BASE_COST_MODEL,
    BAR_INTERVAL,
    EVALUATION_END,
    EVALUATION_START,
    FUNDING_INTERVAL,
    INITIAL_EQUITY,
    LONG_CAP,
    LOT_NOTIONAL,
    OOS_START,
    PER_SYMBOL_CAP,
    RESERVE_CASH,
    SHORT_CAP,
    STRESS_COST_MODEL,
    SYMBOLS,
    TOTAL_CAP,
    _bool_value,
    _read_funding_frame,
    _read_trade_frame,
    _run_arm,
    _segment_summary,
    _validate_continuous,
    _validate_funding_events,
)


EXPERIMENT_ID = "EXP-2026-0049"
SIGNAL_START = EVALUATION_START - pd.Timedelta(days=31)
SIGNAL_CONFIG = SelectiveFundingCarrySignalConfig(
    lookback_events=6,
    holding_events=6,
    rebalance_events=6,
    long_count=2,
    short_count=2,
    signal_delay_bars=1,
    beta_window_bars=360,
    max_beta_gap=0.25,
    minimum_projected_carry=0.0048,
)


def _prepare_frames(data_dir: Path) -> dict[str, pd.DataFrame]:
    """原データを検査し、事前登録済み選択シグナルを生成する。

    Args:
        data_dir: EXP-2026-0015の銘柄別データディレクトリ。

    Returns:
        評価期間の銘柄別シグナルDataFrame。

    Raises:
        ValueError: データ品質、時刻整合、warm-upが不正な場合。
    """

    source_frames: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        trade = _read_trade_frame(data_dir / symbol / "trade-2h.csv")
        trade = trade[
            (trade["event_time"] >= SIGNAL_START)
            & (trade["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        if trade.empty or trade["event_time"].min() > SIGNAL_START:
            raise ValueError(f"insufficient beta warmup data: {symbol}")
        _validate_continuous(trade, symbol)

        funding = _read_funding_frame(data_dir / symbol / "funding-rate.csv")
        funding = funding[
            (funding["event_time"] >= SIGNAL_START)
            & (funding["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        _validate_funding_events(funding, symbol)
        if not set(funding["event_time"]).issubset(set(trade["event_time"])):
            raise ValueError(f"funding event is not aligned to trade bars: {symbol}")

        merged = trade.merge(funding, on="event_time", how="left", validate="one_to_one")
        merged["funding_event"] = merged["funding_event"].fillna(False).map(_bool_value)
        merged["funding_rate"] = merged["funding_rate"].fillna(0.0)
        source_frames[symbol] = merged

    signals = prepare_selective_funding_carry_signals(
        source_frames,
        SIGNAL_CONFIG,
        start_trading_at=EVALUATION_START,
    )
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, frame in signals.items():
        evaluation = frame[
            (frame["event_time"] >= EVALUATION_START)
            & (frame["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        if evaluation.empty:
            raise ValueError(f"empty evaluation period: {symbol}")
        _validate_continuous(evaluation, symbol)
        if (
            evaluation.iloc[0]["desired_long_position"] != 0
            or evaluation.iloc[0]["desired_short_position"] != 0
        ):
            raise ValueError(f"evaluation must start flat: {symbol}")
        prepared[symbol] = evaluation
    return prepared


def _gate_counts(frames: dict[str, pd.DataFrame], start: pd.Timestamp) -> dict[str, int]:
    """指定期間の更新時点をgate理由別に数える。

    Args:
        frames: 銘柄別シグナルDataFrame。
        start: 集計開始UTC時刻。

    Returns:
        gate理由から更新回数へのマッピング。
    """

    frame = frames[sorted(frames)[0]]
    rows = frame[
        (frame["event_time"] >= start)
        & frame["funding_signal_event"].map(_bool_value)
    ]
    return {
        str(reason): int(count)
        for reason, count in rows["funding_gate_reason"].value_counts().items()
    }


def _decision(
    base_oos: dict[str, object],
    stress_oos: dict[str, object],
) -> tuple[str, list[str]]:
    """事前登録済みOOS棄却条件を評価する。

    Args:
        base_oos: 基本費用armのOOS指標。
        stress_oos: 費用2倍armのOOS指標。

    Returns:
        research statusと棄却理由。
    """

    reasons: list[str] = []
    completed_round_trips = min(
        int(base_oos["entry_count"]), int(base_oos["exit_count"])
    )
    if completed_round_trips < 30:
        reasons.append("oos_completed_round_trips_below_30")
    if Decimal(str(base_oos["net_pnl"])) <= 0:
        reasons.append("oos_net_pnl_nonpositive")
    if Decimal(str(base_oos["max_drawdown"])) <= Decimal("-0.10"):
        reasons.append("oos_max_drawdown_below_minus_10pct")
    if Decimal(str(stress_oos["net_pnl"])) <= 0:
        reasons.append("stress_oos_net_pnl_nonpositive")
    return ("BACKTEST_CANDIDATE" if not reasons else "REJECTED", reasons)


def main() -> None:
    """基本費用と費用2倍stressを実行して監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0049")
    )
    args = parser.parse_args()

    frames = _prepare_frames(args.data_dir)
    results = {
        "base": _run_arm(frames, BASE_COST_MODEL),
        "stress_2x_cost": _run_arm(frames, STRESS_COST_MODEL),
    }
    summaries = {
        arm: {
            "full": result.metrics,
            "development": _segment_summary(result, EVALUATION_START, OOS_START),
            "oos": _segment_summary(result, OOS_START, EVALUATION_END),
        }
        for arm, result in results.items()
    }
    decision, reasons = _decision(
        summaries["base"]["oos"], summaries["stress_2x_cost"]["oos"]
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    signal_columns = [
        "event_time",
        "funding_rate",
        "funding_event",
        "funding_lookback_mean",
        "trailing_market_beta",
        "projected_funding_carry",
        "selected_beta_gap",
        "funding_gate_reason",
        "funding_signal_event",
        "desired_long_position",
        "desired_short_position",
    ]
    for symbol, frame in frames.items():
        frame[signal_columns].to_csv(
            args.output_dir / f"{symbol}-signals.csv", index=False
        )
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(
            args.output_dir / f"{arm}-events.csv", index=False
        )
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )

    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "research_decision": decision,
        "rejection_reasons": reasons,
        "promotion_status": "NOT_ELIGIBLE",
        "parameters": {
            "symbols": SYMBOLS,
            "evaluation_start": EVALUATION_START.isoformat(),
            "evaluation_end": EVALUATION_END.isoformat(),
            "oos_start": OOS_START.isoformat(),
            "signal_start": SIGNAL_START.isoformat(),
            **SIGNAL_CONFIG.__dict__,
            "initial_equity": str(INITIAL_EQUITY),
            "reserve_cash": str(RESERVE_CASH),
            "lot_notional": str(LOT_NOTIONAL),
            "long_cap": str(LONG_CAP),
            "short_cap": str(SHORT_CAP),
            "total_cap": str(TOTAL_CAP),
            "per_symbol_cap": str(PER_SYMBOL_CAP),
        },
        "cost_models": {
            "base": BASE_COST_MODEL.__dict__,
            "stress_2x_cost": STRESS_COST_MODEL.__dict__,
        },
        "gate_counts": {
            "full": _gate_counts(frames, EVALUATION_START),
            "oos": _gate_counts(frames, OOS_START),
        },
        "arms": summaries,
        "limitations": [
            "EXP-2026-0048で観測済みのOOSを診断後追試へ再利用しており、未観測証拠ではない。",
            "6銘柄固定でpoint-in-time universeではない。",
            "過去30日βは将来βまたは銘柄固有riskの抑制を保証しない。",
            "ZOOMEX公開履歴上の研究であり、実約定の有効性は未検証。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
