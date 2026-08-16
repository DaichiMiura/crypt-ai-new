#!/usr/bin/env python3
"""EXP-2026-0039の実現ボラティリティ二段階ロットを比較する。"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.allocation import AllocationConfig  # noqa: E402
from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from scripts.run_exp_2026_0035 import (  # noqa: E402
    COST_MODEL,
    INITIAL_EQUITY,
    RESERVE_CASH,
    SYMBOLS,
    _benchmark,
    _decimal,
    _diagnostics,
    _load_raw_frames,
    _prepare_momentum_frames,
)


EXPERIMENT_ID = "EXP-2026-0039"
VOLATILITY_WINDOW_BARS = 360
BARS_PER_YEAR = 12 * 365
HIGH_VOLATILITY_THRESHOLD = 1.0
BASE_LOT_NOTIONAL = Decimal("100")
MAX_LOT_COUNT = 2
TOTAL_CAP = Decimal("400")
ARMS = ("cash_control", "fixed_lot_100", "fixed_lot_200", "volatility_lot")


def _allocation_config(arm: str) -> AllocationConfig:
    """100 USDT基準ロットで最大2ロット・2銘柄の配分設定を作る。

    Args:
        arm: `ARMS`へ登録されたarm名。

    Returns:
        entry行のdesired lot countを許容する共通配分設定。

    Raises:
        ValueError: arm名が未登録の場合。
    """

    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm}")
    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=(TOTAL_CAP if arm != "cash_control" else Decimal("0")),
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=TOTAL_CAP,
        per_symbol_max_notional=BASE_LOT_NOTIONAL * MAX_LOT_COUNT,
        lot_notional=BASE_LOT_NOTIONAL,
        max_concurrent_long_positions=2,
        max_concurrent_short_positions=len(SYMBOLS),
    )


def _realized_volatility(frame: pd.DataFrame) -> pd.DataFrame:
    """2時間足終値から30日年率実現ボラティリティを計算する。

    Args:
        frame: `event_time`と正の`close`を含むDataFrame。

    Returns:
        event_timeと母標準偏差ベースの`realized_volatility`を含むDataFrame。

    Raises:
        ValueError: 必須列、時刻、または終値が不正な場合。
    """

    required = {"event_time", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing volatility columns: {sorted(missing)}")
    result = frame[["event_time", "close"]].copy()
    result["event_time"] = pd.to_datetime(
        result["event_time"], utc=True, errors="coerce"
    )
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    if result["event_time"].isna().any() or result["close"].isna().any():
        raise ValueError("invalid volatility input")
    if not result["close"].gt(0).all():
        raise ValueError("volatility close must be positive")
    log_return = np.log(result["close"] / result["close"].shift(1))
    result["realized_volatility"] = (
        log_return.rolling(VOLATILITY_WINDOW_BARS).std(ddof=0)
        * math.sqrt(BARS_PER_YEAR)
    )
    return result[["event_time", "realized_volatility"]]


def _attach_volatility_lots(
    signal_frames: dict[str, pd.DataFrame],
    raw_frames: dict[str, pd.DataFrame],
) -> dict[str, dict[str, pd.DataFrame]]:
    """共通信号へ固定100・固定200・ボラ二段階のロット列を追加する。

    Args:
        signal_frames: EXP-0035の上位2銘柄シグナル。
        raw_frames: ウォームアップを含む銘柄別tradeデータ。

    Returns:
        arm名から銘柄別ロット付きDataFrameへのマッピング。

    Raises:
        ValueError: 評価期間の実現ボラティリティが欠損・非有限の場合。
    """

    fixed_100: dict[str, pd.DataFrame] = {}
    fixed_200: dict[str, pd.DataFrame] = {}
    volatility: dict[str, pd.DataFrame] = {}
    for symbol, signal in signal_frames.items():
        realized = _realized_volatility(raw_frames[symbol])
        base = signal.merge(realized, on="event_time", how="left", validate="one_to_one")
        values = pd.to_numeric(base["realized_volatility"], errors="coerce")
        if values.isna().any() or not values.map(math.isfinite).all():
            raise ValueError(f"invalid evaluation volatility: {symbol}")
        base["volatility_regime"] = np.where(
            values >= HIGH_VOLATILITY_THRESHOLD, "HIGH", "NORMAL"
        )
        fixed_100[symbol] = base.assign(desired_long_lot_count=1)
        fixed_200[symbol] = base.assign(desired_long_lot_count=2)
        volatility[symbol] = base.assign(
            desired_long_lot_count=np.where(
                values >= HIGH_VOLATILITY_THRESHOLD, 1, 2
            )
        )
    return {
        "fixed_lot_100": fixed_100,
        "fixed_lot_200": fixed_200,
        "volatility_lot": volatility,
    }


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """指定armのentry時固定ロットポートフォリオを実行する。"""

    source = (
        frames
        if arm != "cash_control"
        else {
            symbol: frame.assign(desired_long_position=0)
            for symbol, frame in frames.items()
        }
    )
    return run_allocated_portfolio(source, _allocation_config(arm), COST_MODEL)


def _trade_signature(result: AllocatedPortfolioResult) -> list[tuple[str, str, str]]:
    """ロット額に依存しない売買時刻・種別・銘柄の署名を返す。"""

    return [
        (
            str(event["event_time"]),
            str(event["event_type"]),
            str(event["symbol"]),
        )
        for event in result.events
        if event["event_type"] in {"ENTRY", "EXIT"}
    ]


def _lot_diagnostics(result: AllocatedPortfolioResult) -> dict[str, object]:
    """entryイベントから100・200 USDTロットの利用件数を集計する。"""

    entries = [event for event in result.events if event["event_type"] == "ENTRY"]
    return {
        "one_lot_entry_count": sum(int(event["lot_count"]) == 1 for event in entries),
        "two_lot_entry_count": sum(int(event["lot_count"]) == 2 for event in entries),
        "average_entry_notional": str(
            sum((_decimal(event["notional"]) for event in entries), Decimal("0"))
            / Decimal(len(entries))
            if entries
            else Decimal("0")
        ),
    }


def _compare(
    dynamic: AllocatedPortfolioResult,
    fixed_100: AllocatedPortfolioResult,
    fixed_200: AllocatedPortfolioResult,
) -> dict[str, object]:
    """二段階ロットと固定100・200ロットの主要指標差を計算する。"""

    dynamic_metrics = dynamic.metrics
    return {
        "trade_signature_matches_fixed_100": _trade_signature(dynamic)
        == _trade_signature(fixed_100),
        "trade_signature_matches_fixed_200": _trade_signature(dynamic)
        == _trade_signature(fixed_200),
        "vs_fixed_100": {
            "final_equity_delta": str(
                _decimal(dynamic_metrics["final_equity"])
                - _decimal(fixed_100.metrics["final_equity"])
            ),
            "max_drawdown_delta": str(
                _decimal(dynamic_metrics["max_drawdown"])
                - _decimal(fixed_100.metrics["max_drawdown"])
            ),
        },
        "vs_fixed_200": {
            "final_equity_delta": str(
                _decimal(dynamic_metrics["final_equity"])
                - _decimal(fixed_200.metrics["final_equity"])
            ),
            "max_drawdown_delta": str(
                _decimal(dynamic_metrics["max_drawdown"])
                - _decimal(fixed_200.metrics["max_drawdown"])
            ),
        },
    }


def main() -> None:
    """固定ロットと実現ボラ二段階ロットを実行し、監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0039")
    )
    args = parser.parse_args()

    raw_frames = _load_raw_frames(args.data_dir)
    signals = _prepare_momentum_frames(raw_frames, args.data_dir, long_count=2)
    arm_frames = _attach_volatility_lots(signals, raw_frames)
    results = {
        "cash_control": _run_arm(arm_frames["fixed_lot_100"], "cash_control"),
        **{
            arm: _run_arm(frames, arm) for arm, frames in arm_frames.items()
        },
    }
    comparison = _compare(
        results["volatility_lot"],
        results["fixed_lot_100"],
        results["fixed_lot_200"],
    )
    if not all(
        comparison[key]
        for key in (
            "trade_signature_matches_fixed_100",
            "trade_signature_matches_fixed_200",
        )
    ):
        raise ValueError("dynamic and fixed lot trade signatures differ")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for symbol, frame in arm_frames["volatility_lot"].items():
        frame[
            [
                "event_time",
                "close",
                "momentum_return",
                "cross_sectional_rank",
                "market_regime_ok",
                "rebalance_signal",
                "desired_long_position",
                "realized_volatility",
                "volatility_regime",
                "desired_long_lot_count",
                "funding_rate",
            ]
        ].to_csv(args.output_dir / f"{symbol}-signals.csv", index=False)
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
        "parameters": {
            "symbols": SYMBOLS,
            "signal_source": "EXP-2026-0035 momentum_top2 weekly exit",
            "volatility_window_bars": VOLATILITY_WINDOW_BARS,
            "bars_per_year": BARS_PER_YEAR,
            "high_volatility_threshold": str(HIGH_VOLATILITY_THRESHOLD),
            "base_lot_notional": str(BASE_LOT_NOTIONAL),
            "normal_lot_count": 2,
            "high_volatility_lot_count": 1,
            "initial_equity": str(INITIAL_EQUITY),
            "reserve_cash": str(RESERVE_CASH),
            "max_long_gross_notional": str(TOTAL_CAP),
            "compounding": False,
            "cost_model": {
                "fee_rate": str(COST_MODEL.fee_rate),
                "round_trip_spread": str(COST_MODEL.round_trip_spread),
                "slippage_per_fill": str(COST_MODEL.slippage_per_fill),
            },
        },
        "arms": {
            arm: {
                "metrics": result.metrics,
                "diagnostics": _diagnostics(result),
                "lot_diagnostics": _lot_diagnostics(result),
                "benchmark": _benchmark(_decimal(result.metrics["final_equity"])),
            }
            for arm, result in results.items()
        },
        "dynamic_comparison": comparison,
        "research_status": "REJECTED",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "年率100%は事前固定した単一閾値であり、他閾値を探索していない。",
            "entry時だけロットを決め、保有中のボラティリティ変化ではサイズを変更しない。",
            "単一過去期間の配分比較であり、paper・shadow・live運用を承認しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
