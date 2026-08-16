#!/usr/bin/env python3
"""EXP-2026-0031のクロスセクショナル・モメンタム敗者ショートを実行する。"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
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
    prepare_cross_sectional_momentum_short_signals,
)


SYMBOLS = ("LINKUSDT", "UNIUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT", "AAVEUSDT")
EVALUATION_START = pd.Timestamp("2022-02-01T00:00:00Z")
EVALUATION_END = pd.Timestamp("2026-01-01T00:00:00Z")
LOOKBACK_BARS = 360
REBALANCE_BARS = 84
SHORT_COUNT = 2
BAR_INTERVAL = pd.Timedelta(hours=2)
SIGNAL_START = EVALUATION_START - LOOKBACK_BARS * BAR_INTERVAL
INITIAL_EQUITY = Decimal("1000")
RESERVE_CASH = Decimal("200")
LOT_NOTIONAL = Decimal("50")
TOTAL_CAP = Decimal("200")
PER_SYMBOL_CAP = Decimal("200")
ARM_SHORT_CAPS = {
    "cash_control": Decimal("0"),
    "short_5pct": Decimal("50"),
    "short_10pct": Decimal("100"),
    "short_20pct": Decimal("200"),
}
COST_MODEL = CostModel(
    fee_rate=Decimal("0.0006"),
    round_trip_spread=Decimal("0.001"),
    slippage_per_fill=Decimal("0.0005"),
)


def _read_trade_frame(path: Path) -> pd.DataFrame:
    """ZOOMEXのtrade 2時間足を読み込み、価格と補間行を検査する。

    Args:
        path: 対象銘柄のtrade-2h CSV。

    Returns:
        UTC時刻へ正規化したOHLCと補間フラグを含むDataFrame。

    Raises:
        ValueError: 必須列、時刻、価格、または補間行に不備がある場合。
    """

    frame = pd.read_csv(path)
    required = {"event_time", "open", "high", "low", "close", "is_interpolated"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing columns in {path}: {sorted(missing)}")
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True, errors="coerce")
    if frame["event_time"].isna().any() or frame["event_time"].duplicated().any():
        raise ValueError(f"invalid or duplicate timestamps: {path}")
    frame = frame.sort_values("event_time").reset_index(drop=True)
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[column].isna().any() or not frame[column].gt(0).all():
            raise ValueError(f"invalid {column}: {path}")
    frame["is_interpolated"] = frame["is_interpolated"].astype(str).str.lower().isin(
        {"1", "true", "yes"}
    )
    if frame["is_interpolated"].any():
        raise ValueError(f"interpolated rows are not allowed: {path}")
    return frame


def _read_funding_frame(path: Path) -> pd.DataFrame:
    """ZOOMEXのFunding決済率を読み込み、時刻重複と値を検査する。

    Args:
        path: 対象銘柄のfunding-rate CSV。

    Returns:
        UTC時刻とFunding率を含むDataFrame。

    Raises:
        ValueError: 必須列、時刻、またはFunding率が不正な場合。
    """

    frame = pd.read_csv(path)
    required = {"event_time", "funding_rate"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing funding columns in {path}: {sorted(missing)}")
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True, errors="coerce")
    frame["funding_rate"] = pd.to_numeric(frame["funding_rate"], errors="coerce")
    if (
        frame["event_time"].isna().any()
        or frame["event_time"].duplicated().any()
        or frame["funding_rate"].isna().any()
        or not frame["funding_rate"].map(math.isfinite).all()
    ):
        raise ValueError(f"invalid funding data: {path}")
    return frame[["event_time", "funding_rate"]].sort_values("event_time")


def _validate_continuous(frame: pd.DataFrame, symbol: str) -> None:
    """対象期間の2時間足が連続していることを検査する。

    Args:
        frame: `event_time`を含む時系列DataFrame。
        symbol: エラーに表示する銘柄名。

    Raises:
        ValueError: 2時間間隔でない時刻が含まれる場合。
    """

    gaps = frame["event_time"].diff().dropna()
    if not (gaps == BAR_INTERVAL).all():
        raise ValueError(f"non-continuous two-hour data: {symbol}")


def _prepare_frames(data_dir: Path) -> dict[str, pd.DataFrame]:
    """シグナルとFundingを銘柄別に結合し、共通評価期間へ切り出す。

    Args:
        data_dir: EXP-2026-0015の銘柄別データディレクトリ。

    Returns:
        資金配分層へ渡す銘柄別DataFrame。

    Raises:
        ValueError: データ期間、時刻、品質、またはFunding決済時刻が不正な場合。
    """

    raw_frames: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        frame = _read_trade_frame(data_dir / symbol / "trade-2h.csv")
        frame = frame[
            (frame["event_time"] >= SIGNAL_START)
            & (frame["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        if frame.empty or frame["event_time"].min() > SIGNAL_START:
            raise ValueError(f"insufficient warmup data: {symbol}")
        _validate_continuous(frame, symbol)
        raw_frames[symbol] = frame

    timestamp_sets = {frozenset(frame["event_time"]) for frame in raw_frames.values()}
    if len(timestamp_sets) != 1:
        raise ValueError("signal timestamps differ across symbols")

    signals = prepare_cross_sectional_momentum_short_signals(
        raw_frames,
        lookback_bars=LOOKBACK_BARS,
        rebalance_bars=REBALANCE_BARS,
        short_count=SHORT_COUNT,
    )
    prepared: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        funding = _read_funding_frame(data_dir / symbol / "funding-rate.csv")
        signal = signals[symbol].merge(funding, on="event_time", how="left")
        signal["funding_rate"] = signal["funding_rate"].fillna(0.0)
        signal = signal[
            (signal["event_time"] >= EVALUATION_START)
            & (signal["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        if signal.empty:
            raise ValueError(f"empty evaluation period: {symbol}")
        _validate_continuous(signal, symbol)
        prepared[symbol] = signal[
            [
                "event_time",
                "open",
                "high",
                "low",
                "close",
                "is_interpolated",
                "momentum_return",
                "cross_sectional_rank",
                "rebalance_signal",
                "desired_long_position",
                "desired_short_position",
                "funding_rate",
            ]
        ]
    timestamp_sets = {frozenset(frame["event_time"]) for frame in prepared.values()}
    if len(timestamp_sets) != 1:
        raise ValueError("evaluation timestamps differ across symbols")
    return prepared


def _allocation_config(short_cap: Decimal) -> AllocationConfig:
    """ショート資金枠からショート専用の配分設定を作る。

    Args:
        short_cap: ショート全体へ許可する固定元本上限。

    Returns:
        ロングを無効化し、指定枠を設定した配分設定。

    Raises:
        ValueError: 上限設定が配分設定として不正な場合。
    """

    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=Decimal("0"),
        max_short_gross_notional=short_cap,
        max_total_gross_notional=TOTAL_CAP,
        per_symbol_max_notional=PER_SYMBOL_CAP,
        lot_notional=LOT_NOTIONAL,
        max_concurrent_long_positions=len(SYMBOLS),
        max_concurrent_short_positions=4,
    )


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """指定armのショート専用配分ポートフォリオを実行する。

    Args:
        frames: 銘柄別のシグナル・価格・Funding DataFrame。
        arm: `ARM_SHORT_CAPS`に登録されたarm名。

    Returns:
        配分承認後の会計結果、監査イベント、equity曲線。

    Raises:
        ValueError: arm名または入力データが不正な場合。
    """

    if arm not in ARM_SHORT_CAPS:
        raise ValueError(f"unknown arm: {arm}")
    return run_allocated_portfolio(
        frames,
        _allocation_config(ARM_SHORT_CAPS[arm]),
        COST_MODEL,
    )


def _benchmark(final_equity: Decimal) -> dict[str, object]:
    """4年間・年率10%複利の基準と比較する。

    Args:
        final_equity: armの最終equity。

    Returns:
        基準額、差額、基準超過フラグを含む比較結果。
    """

    benchmark = INITIAL_EQUITY * (Decimal("1.10") ** 4)
    return {
        "annual_return": "0.10",
        "years": 4,
        "benchmark_final_equity": str(benchmark),
        "excess_equity": str(final_equity - benchmark),
        "beats_benchmark": final_equity >= benchmark,
    }


def main() -> None:
    """4つのショート資金枠を実行し、監査CSVとsummary JSONを保存する。

    Raises:
        ValueError: データ品質、シグナル、配分設定の検証に失敗した場合。
    """

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0031")
    )
    args = parser.parse_args()
    frames = _prepare_frames(args.data_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, AllocatedPortfolioResult] = {
        arm: _run_arm(frames, arm) for arm in ARM_SHORT_CAPS
    }
    for symbol, frame in frames.items():
        frame[
            [
                "event_time",
                "momentum_return",
                "cross_sectional_rank",
                "rebalance_signal",
                "desired_short_position",
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
        "experiment_id": "EXP-2026-0031",
        "status": "BACKTEST_COMPLETED",
        "parameters": {
            "symbols": SYMBOLS,
            "evaluation_start": EVALUATION_START.isoformat(),
            "evaluation_end": EVALUATION_END.isoformat(),
            "signal_start": SIGNAL_START.isoformat(),
            "lookback_bars": LOOKBACK_BARS,
            "rebalance_bars": REBALANCE_BARS,
            "short_count": SHORT_COUNT,
            "bar_interval": "2h",
            "initial_equity": str(INITIAL_EQUITY),
            "reserve_cash": str(RESERVE_CASH),
            "lot_notional": str(LOT_NOTIONAL),
            "total_cap": str(TOTAL_CAP),
            "per_symbol_cap": str(PER_SYMBOL_CAP),
            "cost_model": {
                "fee_rate": str(COST_MODEL.fee_rate),
                "round_trip_spread": str(COST_MODEL.round_trip_spread),
                "slippage_per_fill": str(COST_MODEL.slippage_per_fill),
            },
            "funding_accounting": "positive rate is paid by longs and received by shorts; existing positions only",
        },
        "arms": {
            arm: {
                "short_cap": str(short_cap),
                "metrics": result.metrics,
                "benchmark": _benchmark(Decimal(str(result.metrics["final_equity"]))),
            }
            for arm, short_cap in ARM_SHORT_CAPS.items()
            for result in [results[arm]]
        },
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "6銘柄の固定ユニバースであり、ZOOMEX全銘柄の動的ユニバースではない。",
            "下位6銘柄中2銘柄をショートするため、下位四分位を整数化した約33%配分である。",
            "FundingはCSVの決済時刻にある率だけを使い、取引所の清算・証拠金・数量刻みは再現しない。",
            "ショート会計は固定元本を担保相当額として取り置く研究用モデルである。",
            "paper・shadow・live運用を承認する結果ではない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
