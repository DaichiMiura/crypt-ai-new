#!/usr/bin/env python3
"""EXP-2026-0051の週次低ボラティリティlongを実行する。"""

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
from crypt_ai.low_volatility import (  # noqa: E402
    LowVolatilitySignalConfig,
    prepare_low_volatility_signals,
)
from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from scripts.run_exp_2026_0048 import (  # noqa: E402
    BASE_COST_MODEL,
    EVALUATION_END,
    FUNDING_INTERVAL,
    INITIAL_EQUITY,
    OOS_START,
    RESERVE_CASH,
    STRESS_COST_MODEL,
    SYMBOLS,
    _bool_value,
    _read_funding_frame,
    _read_trade_frame,
    _segment_summary,
    _validate_continuous,
    _validate_funding_events,
)


EXPERIMENT_ID = "EXP-2026-0051"
SIGNAL_START = pd.Timestamp("2022-01-01T00:00:00Z")
EVALUATION_START = pd.Timestamp("2022-07-01T00:00:00Z")
LOT_NOTIONAL = Decimal("100")
LONG_CAP = Decimal("200")
TOTAL_CAP = Decimal("400")
SIGNAL_CONFIG = LowVolatilitySignalConfig(
    volatility_window_bars=360,
    regime_window_bars=2160,
    rebalance_bars=84,
    selected_count=2,
    signal_delay_bars=1,
    annualization_bars=4380,
)


def _prepare_frames(data_dir: Path) -> dict[str, pd.DataFrame]:
    """価格とFundingを検査し、固定低volシグナルを生成する。

    Args:
        data_dir: EXP-2026-0015の銘柄別データディレクトリ。

    Returns:
        評価期間の銘柄別低volシグナル。

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
            raise ValueError(f"insufficient low-vol warmup: {symbol}")
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

    signals = prepare_low_volatility_signals(
        source,
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
        if evaluation.empty or evaluation.iloc[0]["desired_long_position"] != 0:
            raise ValueError(f"low-vol evaluation must start flat: {symbol}")
        prepared[symbol] = evaluation
    return prepared


def _allocation_config() -> AllocationConfig:
    """固定100 USDT・最大2 longの配分設定を返す。

    Returns:
        EXP-2026-0051専用配分設定。
    """

    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=LONG_CAP,
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=TOTAL_CAP,
        per_symbol_max_notional=LOT_NOTIONAL,
        lot_notional=LOT_NOTIONAL,
        max_concurrent_long_positions=2,
        max_concurrent_short_positions=1,
    )


def _run_arm(frames: dict[str, pd.DataFrame], cost_model: object) -> AllocatedPortfolioResult:
    """固定低volシグナルを指定費用モデルで会計する。

    Args:
        frames: 銘柄別固定シグナル。
        cost_model: portfolio会計へ渡す費用モデル。

    Returns:
        配分・会計結果。
    """

    return run_allocated_portfolio(frames, _allocation_config(), cost_model)


def _decision(base_oos: dict[str, object], stress_oos: dict[str, object]) -> tuple[str, list[str]]:
    """事前登録済みholdout棄却条件を評価する。

    Args:
        base_oos: 基本費用holdout指標。
        stress_oos: 費用2倍holdout指標。

    Returns:
        research statusと棄却理由。
    """

    reasons: list[str] = []
    completed = min(int(base_oos["entry_count"]), int(base_oos["exit_count"]))
    if completed < 10:
        reasons.append("holdout_completed_leg_round_trips_below_10")
    if Decimal(str(base_oos["net_pnl"])) <= 0:
        reasons.append("holdout_net_pnl_nonpositive")
    if Decimal(str(base_oos["max_drawdown"])) <= Decimal("-0.10"):
        reasons.append("holdout_max_drawdown_below_minus_10pct")
    if Decimal(str(stress_oos["net_pnl"])) <= 0:
        reasons.append("stress_holdout_net_pnl_nonpositive")
    return ("BACKTEST_CANDIDATE" if not reasons else "REJECTED", reasons)


def _signal_diagnostics(
    frames: dict[str, pd.DataFrame], start: pd.Timestamp
) -> dict[str, object]:
    """週次regimeと銘柄選択回数を集計する。

    Args:
        frames: 銘柄別低volシグナル。
        start: 集計開始UTC時刻。

    Returns:
        更新理由と銘柄選択回数。
    """

    first = frames[sorted(frames)[0]]
    rebalances = first[(first["event_time"] >= start) & first["low_vol_rebalance"]]
    reason_counts = {
        str(key): int(value)
        for key, value in rebalances["low_vol_reason"].value_counts().items()
    }
    selections = {
        symbol: int(
            frame[(frame["event_time"] >= start) & frame["low_vol_rebalance"]][
                "low_vol_selected"
            ].sum()
        )
        for symbol, frame in frames.items()
    }
    return {"reason_counts": reason_counts, "selection_counts": selections}


def main() -> None:
    """EXP-2026-0051のbase・stressを実行し成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/EXP-2026-0051"))
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
            "holdout": _segment_summary(result, OOS_START, EVALUATION_END),
        }
        for arm, result in results.items()
    }
    decision, reasons = _decision(
        summaries["base"]["holdout"], summaries["stress_2x_cost"]["holdout"]
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    signal_columns = [
        "event_time", "funding_rate", "funding_event", "realized_volatility",
        "regime_return_180d", "market_regime_median", "volatility_rank",
        "low_vol_rebalance", "low_vol_selected", "low_vol_reason",
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
            "lot_notional": str(LOT_NOTIONAL),
            "long_cap": str(LONG_CAP),
            "total_cap": str(TOTAL_CAP),
        },
        "cost_models": {
            "base": BASE_COST_MODEL.__dict__,
            "stress_2x_cost": STRESS_COST_MODEL.__dict__,
        },
        "signal_diagnostics": {
            "full": _signal_diagnostics(frames, EVALUATION_START),
            "holdout": _signal_diagnostics(frames, OOS_START),
        },
        "arms": summaries,
        "limitations": [
            "holdoutは既存研究で観測済みで未観測OOSではない。",
            "6銘柄固定でpoint-in-time universeではない。",
            "低vol順位の銘柄固有集中を一般的anomalyと解釈できない。",
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
