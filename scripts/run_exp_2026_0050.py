#!/usr/bin/env python3
"""EXP-2026-0050のAVAX/NEARペア平均回帰を実行する。"""

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

from crypt_ai.allocation import AllocationConfig  # noqa: E402
from crypt_ai.pairs_mean_reversion import (  # noqa: E402
    PairMeanReversionConfig,
    prepare_pair_mean_reversion_signals,
)
from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from scripts.run_exp_2026_0048 import (  # noqa: E402
    BASE_COST_MODEL,
    BAR_INTERVAL,
    EVALUATION_END,
    FUNDING_INTERVAL,
    INITIAL_EQUITY,
    LOT_NOTIONAL,
    OOS_START,
    RESERVE_CASH,
    STRESS_COST_MODEL,
    _bool_value,
    _read_funding_frame,
    _read_trade_frame,
    _segment_summary,
    _validate_continuous,
    _validate_funding_events,
)


EXPERIMENT_ID = "EXP-2026-0050"
SYMBOLS = ("AVAXUSDT", "NEARUSDT")
SIGNAL_START = pd.Timestamp("2022-01-01T00:00:00Z")
EVALUATION_START = pd.Timestamp("2022-03-02T00:00:00Z")
PAIR_GROSS = Decimal("100")
SIGNAL_CONFIG = PairMeanReversionConfig(
    regression_window_bars=720,
    spread_window_bars=360,
    entry_z=2.0,
    exit_z=0.5,
    stop_z=4.0,
    max_holding_bars=168,
    signal_delay_bars=1,
)


def _prepare_frames(data_dir: Path) -> dict[str, pd.DataFrame]:
    """価格とFundingを検査・同期し、ペアシグナルを生成する。

    Args:
        data_dir: EXP-2026-0015の銘柄別データディレクトリ。

    Returns:
        評価期間のAVAX/NEARシグナルDataFrame。

    Raises:
        ValueError: データ品質、時刻、warm-up、同期が不正な場合。
    """

    source: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        trade = _read_trade_frame(data_dir / symbol / "trade-2h.csv")
        trade = trade[
            (trade["event_time"] >= SIGNAL_START)
            & (trade["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        if trade.empty or trade["event_time"].min() > SIGNAL_START:
            raise ValueError(f"insufficient pair warmup: {symbol}")
        _validate_continuous(trade, symbol)
        funding = _read_funding_frame(data_dir / symbol / "funding-rate.csv")
        funding = funding[
            (funding["event_time"] >= SIGNAL_START)
            & (funding["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        _validate_funding_events(funding, symbol)
        if not set(funding["event_time"]).issubset(set(trade["event_time"])):
            raise ValueError(f"funding event is not aligned: {symbol}")
        merged = trade.merge(funding, on="event_time", how="left", validate="one_to_one")
        merged["funding_event"] = merged["funding_event"].fillna(False).map(_bool_value)
        merged["funding_rate"] = merged["funding_rate"].fillna(0.0)
        source[symbol] = merged

    signals = prepare_pair_mean_reversion_signals(
        source["AVAXUSDT"],
        source["NEARUSDT"],
        SIGNAL_CONFIG,
        start_trading_at=EVALUATION_START,
    )
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, frame in signals.items():
        evaluation = frame[
            (frame["event_time"] >= EVALUATION_START)
            & (frame["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        _validate_continuous(evaluation, symbol)
        if evaluation.empty or (
            evaluation.iloc[0]["desired_long_position"] != 0
            or evaluation.iloc[0]["desired_short_position"] != 0
        ):
            raise ValueError(f"pair evaluation must start flat: {symbol}")
        prepared[symbol] = evaluation
    if not prepared[SYMBOLS[0]]["event_time"].equals(prepared[SYMBOLS[1]]["event_time"]):
        raise ValueError("prepared pair timestamps differ")
    return prepared


def _allocation_config() -> AllocationConfig:
    """固定50 USDT/脚のペア配分設定を返す。

    Returns:
        EXP-2026-0050専用の配分設定。
    """

    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=LOT_NOTIONAL,
        max_short_gross_notional=LOT_NOTIONAL,
        max_total_gross_notional=PAIR_GROSS,
        per_symbol_max_notional=LOT_NOTIONAL,
        lot_notional=LOT_NOTIONAL,
        max_concurrent_long_positions=1,
        max_concurrent_short_positions=1,
    )


def _run_arm(frames: dict[str, pd.DataFrame], cost_model: object) -> AllocatedPortfolioResult:
    """固定シグナルを指定費用で会計する。

    Args:
        frames: AVAX/NEARの固定シグナル。
        cost_model: portfolio会計へ渡す費用モデル。

    Returns:
        ペアの配分・会計結果。
    """

    return run_allocated_portfolio(frames, _allocation_config(), cost_model)


def _assert_paired_events(result: AllocatedPortfolioResult) -> None:
    """entryとexitが各時刻2脚同時であることを検査する。

    Args:
        result: 検査するportfolio結果。

    Raises:
        ValueError: 片脚だけのentryまたはexitがある場合。
    """

    events = pd.DataFrame(result.events)
    for event_type in ("ENTRY", "EXIT"):
        selected = events[events["event_type"] == event_type]
        if selected.empty:
            continue
        counts = selected.groupby("event_time")["symbol"].nunique()
        if not counts.eq(2).all():
            raise ValueError(f"unpaired {event_type.lower()} event")


def _decision(base_oos: dict[str, object], stress_oos: dict[str, object]) -> tuple[str, list[str]]:
    """事前登録済みholdout棄却条件を評価する。

    Args:
        base_oos: 基本費用holdout指標。
        stress_oos: 費用2倍holdout指標。

    Returns:
        research statusと棄却理由。
    """

    reasons: list[str] = []
    pair_round_trips = min(int(base_oos["entry_count"]), int(base_oos["exit_count"])) // 2
    if pair_round_trips < 20:
        reasons.append("holdout_pair_round_trips_below_20")
    if Decimal(str(base_oos["net_pnl"])) <= 0:
        reasons.append("holdout_net_pnl_nonpositive")
    if Decimal(str(base_oos["max_drawdown"])) <= Decimal("-0.10"):
        reasons.append("holdout_max_drawdown_below_minus_10pct")
    if Decimal(str(stress_oos["net_pnl"])) <= 0:
        reasons.append("stress_holdout_net_pnl_nonpositive")
    return ("BACKTEST_CANDIDATE" if not reasons else "REJECTED", reasons)


def _action_counts(frames: dict[str, pd.DataFrame], start: pd.Timestamp) -> dict[str, int]:
    """指定期間のシグナルactionを集計する。

    Args:
        frames: ペアシグナル。
        start: 集計開始UTC時刻。

    Returns:
        actionから回数へのマッピング。
    """

    frame = frames["AVAXUSDT"]
    selected = frame[
        (frame["event_time"] >= start) & (frame["pair_signal_action"] != "hold")
    ]
    labels = selected["pair_signal_action"].astype(str)
    exits = selected.loc[labels == "exit", "pair_exit_reason"].map(lambda x: f"exit_{x}")
    labels.loc[labels == "exit"] = exits
    return {str(key): int(value) for key, value in labels.value_counts().items()}


def main() -> None:
    """EXP-2026-0050のbase・stressを実行して成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/EXP-2026-0050"))
    args = parser.parse_args()

    frames = _prepare_frames(args.data_dir)
    results = {
        "base": _run_arm(frames, BASE_COST_MODEL),
        "stress_2x_cost": _run_arm(frames, STRESS_COST_MODEL),
    }
    for result in results.values():
        _assert_paired_events(result)
    summaries = {
        arm: {
            "full": result.metrics,
            "development": _segment_summary(result, EVALUATION_START, OOS_START),
            "holdout": _segment_summary(result, OOS_START, EVALUATION_END),
        }
        for arm, result in results.items()
    }
    decision, reasons = _decision(
        summaries["base"]["holdout"], summaries["stress_2x_cost"]["holdout"]
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    signal_columns = [
        "event_time", "funding_rate", "funding_event", "pair_alpha", "pair_beta",
        "pair_spread", "pair_spread_mean", "pair_spread_std", "pair_z_score",
        "pair_position", "pair_signal_action", "pair_exit_reason",
        "desired_long_position", "desired_short_position",
    ]
    for symbol, frame in frames.items():
        frame[signal_columns].to_csv(args.output_dir / f"{symbol}-signals.csv", index=False)
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(args.output_dir / f"{arm}-events.csv", index=False)
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
            "signal_start": SIGNAL_START.isoformat(),
            "evaluation_start": EVALUATION_START.isoformat(),
            "evaluation_end": EVALUATION_END.isoformat(),
            "holdout_start": OOS_START.isoformat(),
            **SIGNAL_CONFIG.__dict__,
            "lot_notional_per_leg": str(LOT_NOTIONAL),
            "pair_gross": str(PAIR_GROSS),
        },
        "cost_models": {
            "base": BASE_COST_MODEL.__dict__,
            "stress_2x_cost": STRESS_COST_MODEL.__dict__,
        },
        "action_counts": {
            "full": _action_counts(frames, EVALUATION_START),
            "holdout": _action_counts(frames, OOS_START),
        },
        "arms": summaries,
        "limitations": [
            "retrospective holdoutは既存研究で観測済みで、未観測OOSではない。",
            "AVAX/NEAR固定1 pairでpoint-in-time universeではない。",
            "等金額はdollar-neutralだが市場β-neutralを保証しない。",
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
