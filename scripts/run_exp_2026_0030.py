#!/usr/bin/env python3
"""EXP-2026-0030の配分層接続バックテストを実行する。"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys
from statistics import median

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.allocation import AllocationConfig  # noqa: E402
from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from crypt_ai.research import (  # noqa: E402
    CostModel,
    prepare_donchian_long_short_regime_signals,
)


SYMBOLS = ("LINKUSDT", "UNIUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT", "AAVEUSDT")
EVALUATION_START = pd.Timestamp("2022-02-01T00:00:00Z")
EVALUATION_END = pd.Timestamp("2026-01-01T00:00:00Z")
ENTRY_WINDOW = 660
EXIT_WINDOW = 240
REGIME_WINDOW = 2400
INITIAL_EQUITY = Decimal("1000")
RESERVE_CASH = Decimal("200")
LOT_NOTIONAL = Decimal("50")
TOTAL_CAP = Decimal("800")
PER_SYMBOL_CAP = Decimal("200")
ARM_SHORT_CAPS = {
    "long_only": Decimal("0"),
    "hedge_5pct": Decimal("50"),
    "hedge_10pct": Decimal("100"),
    "hedge_20pct": Decimal("200"),
}
COST_MODEL = CostModel(
    fee_rate=Decimal("0.0006"),
    round_trip_spread=Decimal("0.001"),
    slippage_per_fill=Decimal("0.0005"),
)


def _read_trade_frame(path: Path) -> pd.DataFrame:
    """ZOOMEX trade 2時間足を読み込み、品質を検査する。

    Args:
        path: 対象銘柄のtrade-2h CSV。

    Returns:
        UTC時刻・OHLC・補間フラグを含むDataFrame。

    Raises:
        ValueError: 必須列、時刻、価格、補間行に不備がある場合。
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
    interpolated = frame["is_interpolated"].astype(str).str.lower().isin(
        {"1", "true", "yes"}
    )
    if interpolated.any():
        raise ValueError(f"interpolated rows are not allowed: {path}")
    frame["is_interpolated"] = interpolated
    return frame


def _prepare_frames(data_dir: Path) -> dict[str, pd.DataFrame]:
    """6銘柄のシグナルを計算し、共通評価期間へ揃える。

    Args:
        data_dir: EXP-2026-0015の銘柄別データディレクトリ。

    Returns:
        資金配分層へ渡せる銘柄別シグナルDataFrame。

    Raises:
        ValueError: 銘柄データ、時刻、2時間間隔が一致しない場合。
    """

    frames: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        frame = _read_trade_frame(data_dir / symbol / "trade-2h.csv")
        prepared = prepare_donchian_long_short_regime_signals(
            frame,
            entry_window=ENTRY_WINDOW,
            exit_window=EXIT_WINDOW,
            regime_window=REGIME_WINDOW,
        )
        prepared["desired_short_position"] = prepared[
            "desired_short_position"
        ].abs().astype(int)
        prepared = prepared[
            (prepared["event_time"] >= EVALUATION_START)
            & (prepared["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        if prepared.empty:
            raise ValueError(f"empty evaluation period: {symbol}")
        gaps = prepared["event_time"].diff().dropna()
        if not (gaps == pd.Timedelta(hours=2)).all():
            raise ValueError(f"non-continuous two-hour data: {symbol}")
        frames[symbol] = prepared[
            [
                "event_time",
                "open",
                "high",
                "low",
                "close",
                "is_interpolated",
                "desired_long_position",
                "desired_short_position",
            ]
        ]
    timestamp_sets = {frozenset(frame["event_time"]) for frame in frames.values()}
    if len(timestamp_sets) != 1:
        raise ValueError("evaluation timestamps differ across symbols")
    return frames


def _allocation_config(short_cap: Decimal) -> AllocationConfig:
    """ショート上限armから固定配分設定を作る。

    Args:
        short_cap: ショート全体の元本上限。

    Returns:
        検証済みの配分設定。
    """

    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=TOTAL_CAP - short_cap,
        max_short_gross_notional=short_cap,
        max_total_gross_notional=TOTAL_CAP,
        per_symbol_max_notional=PER_SYMBOL_CAP,
        lot_notional=LOT_NOTIONAL,
        max_concurrent_long_positions=len(SYMBOLS),
        max_concurrent_short_positions=len(SYMBOLS),
    )


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """指定armの配分ポートフォリオを実行する。"""

    if arm not in ARM_SHORT_CAPS:
        raise ValueError(f"unknown arm: {arm}")
    return run_allocated_portfolio(
        frames,
        _allocation_config(ARM_SHORT_CAPS[arm]),
        COST_MODEL,
    )


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


def _compare(
    baseline: dict[str, object], candidate: dict[str, object]
) -> dict[str, object]:
    """候補armとlong-onlyの主要指標差を計算する。"""

    return {
        "final_equity_delta": str(
            Decimal(str(candidate["final_equity"]))
            - Decimal(str(baseline["final_equity"]))
        ),
        "max_drawdown_delta": str(
            Decimal(str(candidate["max_drawdown"]))
            - Decimal(str(baseline["max_drawdown"]))
        ),
        "allocation_rejection_delta": int(candidate["allocation_rejection_count"])
        - int(baseline["allocation_rejection_count"]),
        "short_realized_pnl": str(candidate["short_realized_pnl"]),
    }


def _aggregate(comparisons: dict[str, dict[str, object]]) -> dict[str, object]:
    """候補armのlong-only差を集計する。"""

    final = [Decimal(str(item["final_equity_delta"])) for item in comparisons.values()]
    dd = [Decimal(str(item["max_drawdown_delta"])) for item in comparisons.values()]
    return {
        "portfolio_count": len(comparisons),
        "improved_final_equity": sum(value > 0 for value in final),
        "median_final_equity_delta": str(median(final)) if final else "0",
        "sum_final_equity_delta": str(sum(final, Decimal("0"))),
        "improved_max_drawdown": sum(value > 0 for value in dd),
        "median_max_drawdown_delta": str(median(dd)) if dd else "0",
    }


def main() -> None:
    """配分armを実行し、監査CSVとsummary JSONを保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0030")
    )
    args = parser.parse_args()
    frames = _prepare_frames(args.data_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, AllocatedPortfolioResult] = {
        arm: _run_arm(frames, arm) for arm in ARM_SHORT_CAPS
    }
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(
            args.output_dir / f"{arm}-events.csv", index=False
        )
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )
    baseline = results["long_only"].metrics
    comparisons = {
        arm: _compare(baseline, result.metrics)
        for arm, result in results.items()
        if arm != "long_only"
    }
    payload = {
        "experiment_id": "EXP-2026-0030",
        "status": "BACKTEST_COMPLETED",
        "parameters": {
            "symbols": SYMBOLS,
            "evaluation_start": EVALUATION_START.isoformat(),
            "evaluation_end": EVALUATION_END.isoformat(),
            "entry_window": ENTRY_WINDOW,
            "exit_window": EXIT_WINDOW,
            "regime_window": REGIME_WINDOW,
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
        },
        "arms": {
            arm: {
                "short_cap": str(short_cap),
                "long_cap": str(TOTAL_CAP - short_cap),
                "metrics": result.metrics,
                "benchmark": _benchmark(Decimal(str(result.metrics["final_equity"]))),
            }
            for arm, short_cap in ARM_SHORT_CAPS.items()
            for result in [results[arm]]
        },
        "comparison_to_long_only": comparisons,
        "aggregate_comparison": {
            arm: _aggregate({"portfolio": comparison})
            for arm, comparison in comparisons.items()
        },
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "EXP-2026-0029のVOID式ショートを再現したものではなく、配分層接続用のDonchian long/short共通シグナル診断である。",
            "ショート会計は1ロット元本を担保相当額として取り置く研究用モデルで、Funding・清算・ZOOMEX固有証拠金を含まない。",
            "6銘柄の同時処理順はシンボル昇順、long先行で固定している。",
            "paper・shadow・live運用を承認する結果ではない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
