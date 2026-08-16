#!/usr/bin/env python3
"""EXP-2026-0032のベーシス収束ペアを実行する。"""

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

from crypt_ai.basis import BasisSignalConfig, prepare_basis_signals  # noqa: E402
from crypt_ai.basis_backtest import (  # noqa: E402
    BasisBacktestConfig,
    BasisBacktestResult,
    BasisCostModel,
    run_basis_backtest,
)


SYMBOLS = ("LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT")
EVALUATION_START = pd.Timestamp("2022-02-01T00:00:00Z")
EVALUATION_END = pd.Timestamp("2026-01-01T00:00:00Z")
BAR_INTERVAL = pd.Timedelta(hours=2)
WARMUP_BARS = 400
SIGNAL_START = EVALUATION_START - WARMUP_BARS * BAR_INTERVAL
ENTRY_BASIS = Decimal("0.005")
EXIT_BASIS = Decimal("0.001")
MAX_HOLDING_BARS = 360
INITIAL_EQUITY = Decimal("1000")
RESERVE_CASH = Decimal("200")
PAIR_NOTIONAL = Decimal("100")
ARM_MAX_PAIRS = {"cash_control": 0, "pair_1": 1, "pair_2": 2, "pair_4": 4}
COST_MODEL = BasisCostModel()
SIGNAL_CONFIG = BasisSignalConfig(
    entry_basis=ENTRY_BASIS,
    exit_basis=EXIT_BASIS,
    max_holding_bars=MAX_HOLDING_BARS,
)


def _read_csv(path: Path, required: set[str]) -> pd.DataFrame:
    """価格またはFunding CSVを読み込み、共通の基本品質を検査する。

    Args:
        path: 読み込むCSVパス。
        required: 必須列集合。

    Returns:
        時刻昇順へ正規化したDataFrame。

    Raises:
        ValueError: 必須列、時刻、重複、または数値列が不正な場合。
    """

    frame = pd.read_csv(path)
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing columns in {path}: {sorted(missing)}")
    frame["event_time"] = pd.to_datetime(
        frame["event_time"], utc=True, errors="coerce"
    )
    if frame["event_time"].isna().any() or frame["event_time"].duplicated().any():
        raise ValueError(f"invalid or duplicate event_time: {path}")
    return frame.sort_values("event_time").reset_index(drop=True)


def _read_price(path: Path, prefix: str) -> pd.DataFrame:
    """OHLC CSVをペア会計用の接頭辞付き列へ変換する。

    Args:
        path: OHLC CSVパス。
        prefix: 列名へ付ける`spot`または`perp`接頭辞。

    Returns:
        event_timeと接頭辞付きOHLCを含むDataFrame。

    Raises:
        ValueError: 価格列が欠損、非数値、または非正の場合。
    """

    frame = _read_csv(path, {"event_time", "open", "high", "low", "close"})
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[column].isna().any() or not frame[column].gt(0).all():
            raise ValueError(f"invalid {column}: {path}")
    return frame.rename(
        columns={column: f"{prefix}_{column}" for column in ("open", "high", "low", "close")}
    )[
        [
            "event_time",
            f"{prefix}_open",
            f"{prefix}_high",
            f"{prefix}_low",
            f"{prefix}_close",
        ]
    ]


def _validate_continuous(frame: pd.DataFrame, symbol: str) -> None:
    """共通評価入力が2時間間隔で連続していることを検査する。

    Args:
        frame: event_timeを含む共通入力DataFrame。
        symbol: エラーに表示する銘柄名。

    Raises:
        ValueError: 内部gapまたは評価期間不足がある場合。
    """

    if frame.empty:
        raise ValueError(f"empty basis input: {symbol}")
    gaps = frame["event_time"].diff().dropna()
    if not (gaps == BAR_INTERVAL).all():
        raise ValueError(f"non-continuous two-hour basis data: {symbol}")


def _prepare_frames(
    spot_dir: Path,
    perpetual_dir: Path,
) -> dict[str, pd.DataFrame]:
    """現物・先物・mark・Fundingを結合し、ベーシスシグナルを計算する。

    Args:
        spot_dir: EXP-2026-0032現物データの銘柄別ディレクトリ。
        perpetual_dir: EXP-2026-0015先物データの銘柄別ディレクトリ。

    Returns:
        ベーシスシグナル付きの銘柄別評価DataFrame。

    Raises:
        ValueError: データソース間の時刻、期間、価格、Fundingが不一致の場合。
    """

    prepared: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        spot = _read_price(spot_dir / symbol / "spot-2h.csv", "spot")
        perp = _read_price(perpetual_dir / symbol / "trade-2h.csv", "perp")
        mark = _read_price(perpetual_dir / symbol / "mark-price-2h.csv", "mark")
        funding = _read_csv(
            perpetual_dir / symbol / "funding-rate.csv",
            {"event_time", "funding_rate"},
        )[["event_time", "funding_rate"]]
        funding["funding_rate"] = pd.to_numeric(
            funding["funding_rate"], errors="coerce"
        )
        if funding["funding_rate"].isna().any():
            raise ValueError(f"invalid funding_rate: {symbol}")

        frame = spot.merge(perp, on="event_time", how="inner").merge(
            mark[["event_time", "mark_close"]], on="event_time", how="inner"
        )
        frame = frame.merge(funding, on="event_time", how="left")
        frame["funding_rate"] = frame["funding_rate"].fillna(0.0)
        frame = frame[
            (frame["event_time"] >= SIGNAL_START)
            & (frame["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        frame = frame.rename(columns={"mark_close": "perp_mark_close"})
        if frame["event_time"].min() > SIGNAL_START:
            raise ValueError(f"insufficient common warm-up: {symbol}")
        _validate_continuous(frame, symbol)
        signal = prepare_basis_signals(frame, SIGNAL_CONFIG)
        signal = signal[
            (signal["event_time"] >= EVALUATION_START)
            & (signal["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        _validate_continuous(signal, symbol)
        signal["is_interpolated"] = False
        prepared[symbol] = signal[
            [
                "event_time",
                "spot_open",
                "spot_high",
                "spot_low",
                "spot_close",
                "perp_open",
                "perp_high",
                "perp_low",
                "perp_close",
                "perp_mark_close",
                "funding_rate",
                "basis",
                "basis_entry_signal",
                "basis_exit_signal",
                "basis_holding_bars",
                "pair_signal_position",
                "desired_pair_position",
                "is_interpolated",
            ]
        ]
    if len({frozenset(frame["event_time"]) for frame in prepared.values()}) != 1:
        raise ValueError("evaluation timestamps differ across basis symbols")
    return prepared


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> BasisBacktestResult:
    """指定ペア同時保有上限のarmを実行する。

    Args:
        frames: 銘柄別のベーシス入力DataFrame。
        arm: `ARM_MAX_PAIRS`へ登録されたarm名。

    Returns:
        ペア会計結果。

    Raises:
        ValueError: arm名または入力データが不正な場合。
    """

    if arm not in ARM_MAX_PAIRS:
        raise ValueError(f"unknown arm: {arm}")
    if arm == "cash_control":
        frames = {
            symbol: frame.assign(desired_pair_position=0)
            for symbol, frame in frames.items()
        }
        max_pairs = 1
    else:
        max_pairs = ARM_MAX_PAIRS[arm]
    return run_basis_backtest(
        frames,
        BasisBacktestConfig(
            initial_equity=INITIAL_EQUITY,
            reserve_cash=RESERVE_CASH,
            pair_notional=PAIR_NOTIONAL,
            max_concurrent_pairs=max_pairs,
            signal_config=SIGNAL_CONFIG,
            costs=COST_MODEL,
        ),
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
    """ベーシス収束ペアのarmを実行し、監査CSVとsummary JSONを保存する。

    Raises:
        ValueError: データ品質、シグナル、ペア会計の検証に失敗した場合。
    """

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spot-dir", type=Path, default=Path("data/processed/EXP-2026-0032")
    )
    parser.add_argument(
        "--perpetual-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0032")
    )
    args = parser.parse_args()
    frames = _prepare_frames(args.spot_dir, args.perpetual_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = {arm: _run_arm(frames, arm) for arm in ARM_MAX_PAIRS}
    for symbol, frame in frames.items():
        frame[
            [
                "event_time",
                "spot_close",
                "perp_mark_close",
                "basis",
                "basis_entry_signal",
                "basis_exit_signal",
                "basis_holding_bars",
                "desired_pair_position",
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

    basis_stats = {
        symbol: {
            "mean_basis": str(frame["basis"].mean()),
            "min_basis": str(frame["basis"].min()),
            "max_basis": str(frame["basis"].max()),
            "entry_signal_bars": int(frame["basis_entry_signal"].sum()),
            "exit_signal_bars": int(frame["basis_exit_signal"].sum()),
        }
        for symbol, frame in frames.items()
    }
    payload = {
        "experiment_id": "EXP-2026-0032",
        "status": "BACKTEST_COMPLETED",
        "parameters": {
            "symbols": SYMBOLS,
            "evaluation_start": EVALUATION_START.isoformat(),
            "evaluation_end": EVALUATION_END.isoformat(),
            "signal_start": SIGNAL_START.isoformat(),
            "entry_basis": str(ENTRY_BASIS),
            "exit_basis": str(EXIT_BASIS),
            "max_holding_bars": MAX_HOLDING_BARS,
            "pair_notional_per_leg": str(PAIR_NOTIONAL),
            "initial_equity": str(INITIAL_EQUITY),
            "reserve_cash": str(RESERVE_CASH),
            "cost_model": {
                "spot_fee_rate": str(COST_MODEL.spot_fee_rate),
                "perp_fee_rate": str(COST_MODEL.perp_fee_rate),
                "spot_round_trip_spread": str(COST_MODEL.spot_round_trip_spread),
                "perp_round_trip_spread": str(COST_MODEL.perp_round_trip_spread),
                "spot_slippage_per_fill": str(COST_MODEL.spot_slippage_per_fill),
                "perp_slippage_per_fill": str(COST_MODEL.perp_slippage_per_fill),
            },
        },
        "arms": {
            arm: {
                "max_concurrent_pairs": max_pairs,
                "metrics": result.metrics,
                "benchmark": _benchmark(Decimal(str(result.metrics["final_equity"]))),
            }
            for arm, max_pairs in ARM_MAX_PAIRS.items()
            for result in [results[arm]]
        },
        "basis_stats": basis_stats,
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "現物と先物の同一取引所価格を使うため、他取引所間の裁定ではない。",
            "現物の手数料はUSDT元本へ近似換算しており、実際の現物手数料通貨・残高処理を完全には再現しない。",
            "証拠金、清算、数量刻み、片脚だけ約定するリスク、送金・借入コストは完全には再現しない。",
            "Fundingは取得済み履歴の決済時刻でのみ適用する。",
            "paper・shadow・live運用を承認する結果ではない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
