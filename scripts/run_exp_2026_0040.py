#!/usr/bin/env python3
"""EXP-2026-0040の相対モメンタム拡張ユニバースを比較する。"""

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
from crypt_ai.research import prepare_cross_sectional_momentum_long_signals  # noqa: E402
from scripts.run_exp_2026_0031 import (  # noqa: E402
    _read_funding_frame,
    _read_trade_frame,
    _validate_continuous,
)
from scripts.run_exp_2026_0035 import (  # noqa: E402
    COST_MODEL,
    EVALUATION_END,
    EVALUATION_START,
    INITIAL_EQUITY,
    LOOKBACK_BARS,
    REBALANCE_BARS,
    RESERVE_CASH,
    SIGNAL_START,
    _benchmark,
    _decimal,
)


EXPERIMENT_ID = "EXP-2026-0040"
BASE_SYMBOLS = ("LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT")
EXPANDED_SYMBOLS = (
    "LINKUSDT",
    "UNIUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "NEARUSDT",
    "AAVEUSDT",
)
LOT_NOTIONAL = Decimal("200")
ARMS = {
    "cash_control": (EXPANDED_SYMBOLS, 0),
    "base_4_top2": (BASE_SYMBOLS, 2),
    "expanded_6_top2": (EXPANDED_SYMBOLS, 2),
    "expanded_6_top3": (EXPANDED_SYMBOLS, 3),
}


def _load_raw_frames(
    data_dir: Path, symbols: tuple[str, ...]
) -> dict[str, pd.DataFrame]:
    """指定銘柄の取引データを読み込み共通評価期間を検査する。

    Args:
        data_dir: EXP-2026-0015の銘柄別データディレクトリ。
        symbols: 読み込む銘柄の固定タプル。

    Returns:
        銘柄別の検証済み取引DataFrame。

    Raises:
        ValueError: 評価期間が空、不連続、または銘柄間で不一致の場合。
    """

    frames = {
        symbol: _read_trade_frame(data_dir / symbol / "trade-2h.csv")
        for symbol in symbols
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


def _prepare_frames(
    raw_frames: dict[str, pd.DataFrame],
    data_dir: Path,
    *,
    long_count: int,
) -> dict[str, pd.DataFrame]:
    """指定ユニバースの相対モメンタムシグナルとFundingを作る。

    Args:
        raw_frames: 銘柄別の取引DataFrame。
        data_dir: Fundingファイルを含むデータディレクトリ。
        long_count: リバランス時に選ぶ上位銘柄数。

    Returns:
        ポートフォリオ実行用の銘柄別DataFrame。
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
    signals = prepare_cross_sectional_momentum_long_signals(
        ranking_input,
        lookback_bars=LOOKBACK_BARS,
        rebalance_bars=REBALANCE_BARS,
        long_count=long_count,
        require_positive_median=True,
    )
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, signal in signals.items():
        funding = _read_funding_frame(data_dir / symbol / "funding-rate.csv")
        frame = signal.merge(funding, on="event_time", how="left")
        frame["funding_rate"] = frame["funding_rate"].fillna(0.0)
        frame = frame[
            (frame["event_time"] >= EVALUATION_START)
            & (frame["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        _validate_continuous(frame, symbol)
        prepared[symbol] = frame
    return prepared


def _allocation_config(arm: str) -> AllocationConfig:
    """実験armに対応する固定ロット配分設定を作る。

    Args:
        arm: `ARMS`へ登録されたarm名。

    Returns:
        armの銘柄数と上位選択数に対応した配分設定。

    Raises:
        ValueError: arm名が未登録の場合。
    """

    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm}")
    symbols, long_count = ARMS[arm]
    cap = LOT_NOTIONAL * long_count
    return AllocationConfig(
        currency="USDT",
        allowed_symbols=symbols,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=cap,
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=max(LOT_NOTIONAL, cap),
        per_symbol_max_notional=LOT_NOTIONAL,
        lot_notional=LOT_NOTIONAL,
        max_concurrent_long_positions=max(1, long_count),
        max_concurrent_short_positions=len(symbols),
    )


def _diagnostics(
    result: AllocatedPortfolioResult, symbols: tuple[str, ...]
) -> dict[str, object]:
    """資金使用率と銘柄別entry件数を集計する。

    Args:
        result: 配分ポートフォリオの実行結果。
        symbols: armの対象銘柄。

    Returns:
        平均gross、保有率、銘柄別entry件数。
    """

    gross = [_decimal(row["allocated_gross_notional"]) for row in result.equity_curve]
    return {
        "average_allocated_gross_notional": str(
            sum(gross, Decimal("0")) / Decimal(len(gross))
        ),
        "invested_bar_fraction": str(
            Decimal(sum(value > 0 for value in gross)) / Decimal(len(gross))
        ),
        "symbol_entry_count": {
            symbol: sum(
                event["event_type"] == "ENTRY" and event["symbol"] == symbol
                for event in result.events
            )
            for symbol in symbols
        },
    }


def _comparison(
    baseline: AllocatedPortfolioResult, candidate: AllocatedPortfolioResult
) -> dict[str, object]:
    """同額投資の4銘柄版と6銘柄版を比較する。

    Args:
        baseline: 4銘柄top2の結果。
        candidate: 6銘柄top2の結果。

    Returns:
        最終資産・最大DDの差と採用条件判定。
    """

    final_delta = _decimal(candidate.metrics["final_equity"]) - _decimal(
        baseline.metrics["final_equity"]
    )
    drawdown_delta = _decimal(candidate.metrics["max_drawdown"]) - _decimal(
        baseline.metrics["max_drawdown"]
    )
    return {
        "final_equity_delta": str(final_delta),
        "max_drawdown_delta": str(drawdown_delta),
        "final_equity_maintained_or_improved": final_delta >= 0,
        "max_drawdown_improved": drawdown_delta > 0,
        "adoption_rule_passed": final_delta >= 0 and drawdown_delta > 0,
    }


def main() -> None:
    """4銘柄と6銘柄の相対モメンタムarmを実行して保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0040")
    )
    args = parser.parse_args()

    all_raw = _load_raw_frames(args.data_dir, EXPANDED_SYMBOLS)
    arm_frames: dict[str, dict[str, pd.DataFrame]] = {}
    for arm, (symbols, long_count) in ARMS.items():
        raw = {symbol: all_raw[symbol] for symbol in symbols}
        effective_count = max(1, long_count)
        frames = _prepare_frames(raw, args.data_dir, long_count=effective_count)
        if arm == "cash_control":
            frames = {
                symbol: frame.assign(desired_long_position=0)
                for symbol, frame in frames.items()
            }
        arm_frames[arm] = frames

    results = {
        arm: run_allocated_portfolio(frames, _allocation_config(arm), COST_MODEL)
        for arm, frames in arm_frames.items()
    }
    comparison = _comparison(results["base_4_top2"], results["expanded_6_top2"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
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
            "base_symbols": BASE_SYMBOLS,
            "expanded_symbols": EXPANDED_SYMBOLS,
            "lookback_bars": LOOKBACK_BARS,
            "rebalance_bars": REBALANCE_BARS,
            "lot_notional": str(LOT_NOTIONAL),
            "initial_equity": str(INITIAL_EQUITY),
            "reserve_cash": str(RESERVE_CASH),
        },
        "arms": {
            arm: {
                "metrics": result.metrics,
                "diagnostics": _diagnostics(result, ARMS[arm][0]),
                "benchmark": _benchmark(_decimal(result.metrics["final_equity"])),
            }
            for arm, result in results.items()
        },
        "expanded_top2_comparison": comparison,
        "research_status": (
            "BACKTEST_CANDIDATE" if comparison["adoption_rule_passed"] else "REJECTED"
        ),
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "6銘柄は2026-08-15時点の流動性スナップショットで選ばれ、過去流動性を保証しない。",
            "top3はtop2より最大投資額が200 USDT多く、主比較には使用しない。",
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
