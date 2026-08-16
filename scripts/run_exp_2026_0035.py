#!/usr/bin/env python3
"""EXP-2026-0035の相対モメンタム上位ロングを比較する。"""

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
from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from crypt_ai.research import (  # noqa: E402
    CostModel,
    prepare_atr_trailing_exit_signals,
    prepare_cross_sectional_momentum_long_signals,
)
from scripts.run_exp_2026_0031 import (  # noqa: E402
    _read_funding_frame,
    _read_trade_frame,
    _validate_continuous,
)


EXPERIMENT_ID = "EXP-2026-0035"
SYMBOLS = ("LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT")
EVALUATION_START = pd.Timestamp("2022-02-01T00:00:00Z")
EVALUATION_END = pd.Timestamp("2026-01-01T00:00:00Z")
LOOKBACK_BARS = 360
REBALANCE_BARS = 84
BAR_INTERVAL = pd.Timedelta(hours=2)
SIGNAL_START = EVALUATION_START - LOOKBACK_BARS * BAR_INTERVAL
LONG_ENTRY_BARS = 660
LONG_EXIT_BARS = 240
LONG_REGIME_BARS = 2400
LONG_ATR_BARS = 240
LONG_ATR_MULTIPLIER = 3.0
INITIAL_EQUITY = Decimal("1000")
RESERVE_CASH = Decimal("200")
LOT_NOTIONAL = Decimal("200")
TOTAL_CAP = Decimal("800")
PER_SYMBOL_CAP = Decimal("200")
ARM_LONG_CAPS = {
    "cash_control": Decimal("0"),
    "current_equal_long": Decimal("800"),
    "momentum_top1": Decimal("200"),
    "momentum_top2": Decimal("400"),
}
ARM_MAX_POSITIONS = {
    "cash_control": 4,
    "current_equal_long": 4,
    "momentum_top1": 1,
    "momentum_top2": 2,
}
COST_MODEL = CostModel(
    fee_rate=Decimal("0.0006"),
    round_trip_spread=Decimal("0.001"),
    slippage_per_fill=Decimal("0.0005"),
)


def _decimal(value: object) -> Decimal:
    """入力値を二進浮動小数点を経由せずDecimalへ変換する。"""

    return Decimal(str(value))


def _load_raw_frames(data_dir: Path) -> dict[str, pd.DataFrame]:
    """4銘柄のtradeデータを読み込み、評価期間の連続性を検査する。

    Args:
        data_dir: EXP-2026-0015の銘柄別データディレクトリ。

    Returns:
        銘柄別の検証済みtrade DataFrame。

    Raises:
        ValueError: 評価期間が空、2時間間隔でない、または共通時刻でない場合。
    """

    frames = {
        symbol: _read_trade_frame(data_dir / symbol / "trade-2h.csv")
        for symbol in SYMBOLS
    }
    evaluation_times: set[frozenset[pd.Timestamp]] = set()
    for symbol, frame in frames.items():
        evaluation = frame[
            (frame["event_time"] >= EVALUATION_START)
            & (frame["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        if evaluation.empty:
            raise ValueError(f"empty evaluation period: {symbol}")
        _validate_continuous(evaluation, symbol)
        evaluation_times.add(frozenset(evaluation["event_time"]))
    if len(evaluation_times) != 1:
        raise ValueError("evaluation timestamps differ across symbols")
    return frames


def _attach_funding(
    frame: pd.DataFrame,
    data_dir: Path,
    symbol: str,
) -> pd.DataFrame:
    """シグナルDataFrameへFundingを結合して評価期間へ切り出す。

    Args:
        frame: シグナル列を含む価格DataFrame。
        data_dir: Fundingファイルを含むデータディレクトリ。
        symbol: 対象銘柄。

    Returns:
        評価期間のFunding付きDataFrame。
    """

    funding = _read_funding_frame(data_dir / symbol / "funding-rate.csv")
    result = frame.merge(funding, on="event_time", how="left")
    result["funding_rate"] = result["funding_rate"].fillna(0.0)
    result = result[
        (result["event_time"] >= EVALUATION_START)
        & (result["event_time"] < EVALUATION_END)
    ].reset_index(drop=True)
    _validate_continuous(result, symbol)
    return result


def _prepare_current_frames(
    raw_frames: dict[str, pd.DataFrame], data_dir: Path
) -> dict[str, pd.DataFrame]:
    """EXP-0023/0033由来のATRロングを資金配分層向けに作る。

    Args:
        raw_frames: 銘柄別trade DataFrame。
        data_dir: Fundingファイルを含むデータディレクトリ。

    Returns:
        現行ロングシグナルとFundingを含む銘柄別DataFrame。
    """

    prepared: dict[str, pd.DataFrame] = {}
    for symbol, frame in raw_frames.items():
        signal = prepare_atr_trailing_exit_signals(
            frame[["event_time", "open", "high", "low", "close"]].copy(),
            entry_window=LONG_ENTRY_BARS,
            baseline_exit_window=LONG_EXIT_BARS,
            regime_window=LONG_REGIME_BARS,
            atr_window=LONG_ATR_BARS,
            atr_multiplier=LONG_ATR_MULTIPLIER,
        )
        signal["is_interpolated"] = frame["is_interpolated"].to_numpy()
        signal["desired_long_position"] = signal["desired_atr_position"].astype(int)
        signal["desired_short_position"] = 0
        prepared[symbol] = _attach_funding(signal, data_dir, symbol)[
            [
                "event_time",
                "open",
                "high",
                "low",
                "close",
                "is_interpolated",
                "desired_long_position",
                "desired_short_position",
                "funding_rate",
            ]
        ]
    return prepared


def _prepare_momentum_frames(
    raw_frames: dict[str, pd.DataFrame],
    data_dir: Path,
    *,
    long_count: int,
    early_exit_on_nonpositive: bool = False,
    early_exit_on_nonpositive_median: bool = False,
) -> dict[str, pd.DataFrame]:
    """30日相対モメンタム上位ロングを資金配分層向けに作る。

    Args:
        raw_frames: 銘柄別trade DataFrame。
        data_dir: Fundingファイルを含むデータディレクトリ。
        long_count: リバランスごとに選ぶ上位銘柄数。
        early_exit_on_nonpositive: 選定銘柄のモメンタムが0以下なら次の
            リバランスを待たず退出するか。
        early_exit_on_nonpositive_median: 市場中央値モメンタムが週の途中で
            0以下なら全銘柄を退出するか。

    Returns:
        順位、市場regime、次足用ロング状態、Fundingを含む銘柄別DataFrame。

    Raises:
        ValueError: ウォームアップまたは銘柄間時刻が不足する場合。
    """

    ranking_input: dict[str, pd.DataFrame] = {}
    for symbol, frame in raw_frames.items():
        source = frame[
            (frame["event_time"] >= SIGNAL_START)
            & (frame["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        if source.empty or source["event_time"].min() > SIGNAL_START:
            raise ValueError(f"insufficient momentum warmup: {symbol}")
        _validate_continuous(source, symbol)
        ranking_input[symbol] = source
    if len({frozenset(frame["event_time"]) for frame in ranking_input.values()}) != 1:
        raise ValueError("momentum timestamps differ across symbols")

    signals = prepare_cross_sectional_momentum_long_signals(
        ranking_input,
        lookback_bars=LOOKBACK_BARS,
        rebalance_bars=REBALANCE_BARS,
        long_count=long_count,
        require_positive_median=True,
        early_exit_on_nonpositive=early_exit_on_nonpositive,
        early_exit_on_nonpositive_median=early_exit_on_nonpositive_median,
    )
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, signal in signals.items():
        prepared[symbol] = _attach_funding(signal, data_dir, symbol)[
            [
                "event_time",
                "open",
                "high",
                "low",
                "close",
                "is_interpolated",
                "momentum_return",
                "cross_sectional_rank",
                "market_median_momentum",
                "live_market_median_momentum",
                "market_regime_ok",
                "rebalance_signal",
                "momentum_early_exit_signal",
                "market_early_exit_signal",
                "desired_long_position",
                "desired_short_position",
                "funding_rate",
            ]
        ]
    return prepared


def _allocation_config(arm: str) -> AllocationConfig:
    """arm名から固定ロットのロング配分設定を作る。

    Args:
        arm: `ARM_LONG_CAPS`へ登録されたarm名。

    Returns:
        指定armのgross上限と同時保有上限を持つ設定。

    Raises:
        ValueError: arm名が未登録の場合。
    """

    if arm not in ARM_LONG_CAPS:
        raise ValueError(f"unknown arm: {arm}")
    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=ARM_LONG_CAPS[arm],
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=TOTAL_CAP,
        per_symbol_max_notional=PER_SYMBOL_CAP,
        lot_notional=LOT_NOTIONAL,
        max_concurrent_long_positions=ARM_MAX_POSITIONS[arm],
        max_concurrent_short_positions=len(SYMBOLS),
    )


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """指定armのロング配分バックテストを実行する。"""

    return run_allocated_portfolio(frames, _allocation_config(arm), COST_MODEL)


def _benchmark(final_equity: Decimal) -> dict[str, object]:
    """4年間・年率10%複利の基準と比較する。"""

    benchmark = INITIAL_EQUITY * (Decimal("1.10") ** 4)
    return {
        "annual_return": "0.10",
        "years": 4,
        "benchmark_final_equity": str(benchmark),
        "excess_equity": str(final_equity - benchmark),
        "beats_benchmark": final_equity >= benchmark,
    }


def _diagnostics(result: AllocatedPortfolioResult) -> dict[str, object]:
    """equity曲線とイベントから資金使用率・銘柄別entryを集計する。"""

    gross_values = [
        _decimal(row["allocated_gross_notional"]) for row in result.equity_curve
    ]
    symbol_entries = {
        symbol: sum(
            event["event_type"] == "ENTRY"
            and event["side"] == "long"
            and event["symbol"] == symbol
            for event in result.events
        )
        for symbol in SYMBOLS
    }
    return {
        "average_allocated_gross_notional": str(
            sum(gross_values, Decimal("0")) / Decimal(len(gross_values))
        ),
        "invested_bar_fraction": str(
            Decimal(sum(value > 0 for value in gross_values))
            / Decimal(len(gross_values))
        ),
        "symbol_entry_count": symbol_entries,
    }


def _compare(
    baseline: dict[str, object], candidate: dict[str, object]
) -> dict[str, object]:
    """候補armと現行ロングの最終資産・最大DD差を計算する。"""

    return {
        "final_equity_delta": str(
            _decimal(candidate["final_equity"]) - _decimal(baseline["final_equity"])
        ),
        "max_drawdown_delta": str(
            _decimal(candidate["max_drawdown"])
            - _decimal(baseline["max_drawdown"])
        ),
        "final_equity_improved": _decimal(candidate["final_equity"])
        > _decimal(baseline["final_equity"]),
        "max_drawdown_improved": _decimal(candidate["max_drawdown"])
        > _decimal(baseline["max_drawdown"]),
    }


def main() -> None:
    """4つのロング銘柄選択armを実行し、監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0035")
    )
    args = parser.parse_args()

    raw_frames = _load_raw_frames(args.data_dir)
    current_frames = _prepare_current_frames(raw_frames, args.data_dir)
    top1_frames = _prepare_momentum_frames(raw_frames, args.data_dir, long_count=1)
    top2_frames = _prepare_momentum_frames(raw_frames, args.data_dir, long_count=2)
    cash_frames = {
        symbol: frame.assign(desired_long_position=0)
        for symbol, frame in current_frames.items()
    }
    arm_frames = {
        "cash_control": cash_frames,
        "current_equal_long": current_frames,
        "momentum_top1": top1_frames,
        "momentum_top2": top2_frames,
    }
    results = {
        arm: _run_arm(frames, arm) for arm, frames in arm_frames.items()
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for arm, frames in arm_frames.items():
        for symbol, frame in frames.items():
            signal_columns = [
                column
                for column in (
                    "event_time",
                    "close",
                    "momentum_return",
                    "cross_sectional_rank",
                    "market_median_momentum",
                    "market_regime_ok",
                    "rebalance_signal",
                    "desired_long_position",
                    "funding_rate",
                )
                if column in frame.columns
            ]
            frame[signal_columns].to_csv(
                args.output_dir / f"{arm}-{symbol}-signals.csv", index=False
            )
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(
            args.output_dir / f"{arm}-events.csv", index=False
        )
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )

    current_metrics = results["current_equal_long"].metrics
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "parameters": {
            "symbols": SYMBOLS,
            "evaluation_start": EVALUATION_START.isoformat(),
            "evaluation_end": EVALUATION_END.isoformat(),
            "lookback_bars": LOOKBACK_BARS,
            "rebalance_bars": REBALANCE_BARS,
            "market_regime": "cross-sectional median 30-day return > 0",
            "initial_equity": str(INITIAL_EQUITY),
            "reserve_cash": str(RESERVE_CASH),
            "lot_notional": str(LOT_NOTIONAL),
            "arm_long_caps": {
                arm: str(value) for arm, value in ARM_LONG_CAPS.items()
            },
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
                "benchmark": _benchmark(_decimal(result.metrics["final_equity"])),
            }
            for arm, result in results.items()
        },
        "comparison_to_current_equal_long": {
            arm: _compare(current_metrics, result.metrics)
            for arm, result in results.items()
            if arm != "current_equal_long"
        },
        "market_regime_diagnostics": {
            "rebalance_count": int(top2_frames[SYMBOLS[0]]["rebalance_signal"].sum()),
            "positive_regime_rebalance_count": int(
                (
                    top2_frames[SYMBOLS[0]]["rebalance_signal"]
                    & top2_frames[SYMBOLS[0]]["market_regime_ok"]
                ).sum()
            ),
        },
        "research_status": "BACKTEST_CANDIDATE",
        "promotion_status": "NOT_ELIGIBLE_HIGH_DRAWDOWN",
        "limitations": [
            "top1・top2は最大投資額が200・400 USDTで、current_equal_longの800 USDTより小さい。現金比率の差を含む結果である。",
            "相対モメンタムと市場regimeは同じ30日リターンから計算しており、独立したリスク指標ではない。",
            "単一過去期間の比較であり、paper・shadow・live運用を承認しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
