#!/usr/bin/env python3
"""EXP-2026-0015のVOID式ショートを6銘柄でバックテストする。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypt_ai.void_short import VOID_SHORT_SYMBOLS  # noqa: E402
from crypt_ai.void_short_accounting import VoidShortCostModel  # noqa: E402
from crypt_ai.void_short_backtest import (  # noqa: E402
    VoidShortBacktestConfig,
    VoidShortInstrument,
    run_void_short_backtest,
)


INDEX_BENCHMARK_ANNUAL_RETURN = Decimal("0.10")
INDEX_BENCHMARK_YEARS = Decimal("4")


def _compare_index_benchmark(
    *, initial_equity: Decimal, final_equity: Decimal
) -> dict[str, object]:
    """4年間・年率10%複利のインデックス基準と比較する。

    Args:
        initial_equity: 比較開始時の資産。
        final_equity: 評価期間終了時の資産。

    Returns:
        基準最終資産、超過資産、基準達成可否を含む辞書。

    Raises:
        ValueError: 資産が正でない場合。
    """

    if initial_equity <= 0 or final_equity < 0:
        raise ValueError("equity values must be non-negative and initial positive")
    benchmark_multiplier = (
        Decimal("1") + INDEX_BENCHMARK_ANNUAL_RETURN
    ) ** int(INDEX_BENCHMARK_YEARS)
    benchmark_final_equity = initial_equity * benchmark_multiplier
    return {
        "annual_return": str(INDEX_BENCHMARK_ANNUAL_RETURN),
        "compounding_years": str(INDEX_BENCHMARK_YEARS),
        "cumulative_return": str(benchmark_multiplier - Decimal("1")),
        "benchmark_final_equity": str(benchmark_final_equity),
        "excess_equity": str(final_equity - benchmark_final_equity),
        "beats_benchmark": final_equity >= benchmark_final_equity,
    }


def _read_price_frame(path: Path) -> pd.DataFrame:
    """ZOOMEX価格CSVを読み込み、2時間足の基本列を検証する。

    Args:
        path: ``trade-2h.csv``または``mark-price-2h.csv``のパス。

    Returns:
        UTC時刻、OHLC、補間フラグを持つDataFrame。

    Raises:
        ValueError: 必須列、時刻、価格、補間フラグに不備がある場合。
    """

    frame = pd.read_csv(path)
    required = {"event_time", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(
            f"missing price columns in {path}: {sorted(required - set(frame.columns))}"
        )
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True, errors="coerce")
    if frame["event_time"].isna().any() or frame["event_time"].duplicated().any():
        raise ValueError(f"invalid or duplicate event_time: {path}")
    frame = frame.sort_values("event_time").reset_index(drop=True)
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[column].isna().any() or not frame[column].gt(0).all():
            raise ValueError(f"invalid {column}: {path}")
    if "is_interpolated" not in frame:
        raise ValueError(f"missing is_interpolated: {path}")
    frame["is_interpolated"] = (
        frame["is_interpolated"].astype(str).str.lower().isin(["1", "true", "yes"])
    )
    if frame["is_interpolated"].any():
        raise ValueError(f"interpolated rows are not allowed: {path}")
    return frame


def _read_funding_frame(path: Path) -> pd.DataFrame:
    """Funding CSVを読み込み、時刻と率を正規化する。"""

    frame = pd.read_csv(path)
    required = {"event_time", "funding_rate"}
    if not required.issubset(frame.columns):
        raise ValueError(f"missing funding columns in {path}")
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True, errors="coerce")
    frame["funding_rate"] = pd.to_numeric(frame["funding_rate"], errors="coerce")
    if frame["event_time"].isna().any() or frame["funding_rate"].isna().any():
        raise ValueError(f"invalid funding values: {path}")
    return frame.sort_values("event_time").reset_index(drop=True)


def _build_instrument(metadata: dict[str, object], symbol: str) -> VoidShortInstrument:
    """取得済みmetadataから一銘柄の取引仕様を作る。"""

    for record in metadata["symbols"]:
        if record["symbol"] == symbol:
            price_filter = record["instrument"]["priceFilter"]
            lot_filter = record["instrument"]["lotSizeFilter"]
            return VoidShortInstrument(
                symbol=symbol,
                tick_size=Decimal(price_filter["tickSize"]),
                qty_step=Decimal(lot_filter["qtyStep"]),
                min_order_qty=Decimal(lot_filter["minOrderQty"]),
                min_order_notional=Decimal(lot_filter["minNotionalValue"]),
            )
    raise ValueError(f"symbol missing from metadata: {symbol}")


def _load_symbol(
    data_dir: Path, metadata: dict[str, object], symbol: str
) -> tuple[pd.DataFrame, pd.DataFrame, VoidShortInstrument]:
    """一銘柄のtrade・mark・Fundingと銘柄仕様を読み込む。"""

    symbol_dir = data_dir / symbol
    trade = _read_price_frame(symbol_dir / "trade-2h.csv")
    mark = _read_price_frame(symbol_dir / "mark-price-2h.csv")
    funding = _read_funding_frame(symbol_dir / "funding-rate.csv")
    if not trade["event_time"].equals(mark["event_time"]):
        raise ValueError(f"trade and mark timestamps differ: {symbol}")
    merged = trade.merge(
        mark[["event_time", "high", "close"]].rename(
            columns={"high": "mark_high", "close": "mark_close"}
        ),
        on="event_time",
        how="inner",
        validate="one_to_one",
    )
    if len(merged) != len(trade):
        raise ValueError(f"trade and mark rows differ: {symbol}")
    return merged, funding, _build_instrument(metadata, symbol)


def main() -> None:
    """6銘柄を個別に評価し、JSON・CSV監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0015")
    )
    parser.add_argument("--initial-equity", type=Decimal, default=Decimal("1000"))
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    config = VoidShortBacktestConfig(
        initial_equity=args.initial_equity,
        costs=VoidShortCostModel(),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, object]] = {}
    for symbol in sorted(VOID_SHORT_SYMBOLS):
        frame, funding, instrument = _load_symbol(args.data_dir, metadata, symbol)
        result = run_void_short_backtest(frame, funding, instrument, config)
        results[symbol] = result.metrics
        results[symbol]["index_benchmark"] = _compare_index_benchmark(
            initial_equity=config.initial_equity,
            final_equity=Decimal(str(result.metrics["final_equity"])),
        )
        pd.DataFrame(result.events).to_csv(
            args.output_dir / f"{symbol}-events.csv", index=False
        )
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{symbol}-equity.csv", index=False
        )
    payload = {
        "experiment_id": "EXP-2026-0015",
        "status": "BACKTEST_COMPLETED",
        "initial_equity": str(config.initial_equity),
        "execution_leverage": "1",
        "index_benchmark": {
            "annual_return": str(INDEX_BENCHMARK_ANNUAL_RETURN),
            "compounding_years": str(INDEX_BENCHMARK_YEARS),
            "cumulative_return": str(
                (Decimal("1") + INDEX_BENCHMARK_ANNUAL_RETURN)
                ** int(INDEX_BENCHMARK_YEARS)
                - Decimal("1")
            ),
            "benchmark_final_equity": str(
                config.initial_equity
                * (Decimal("1") + INDEX_BENCHMARK_ANNUAL_RETURN)
                ** int(INDEX_BENCHMARK_YEARS)
            ),
        },
        "index_benchmark_scope": "事前登録した6銘柄すべてが個別に基準を満たすことを合格条件とする。結果を見た銘柄選択はしない。",
        "all_symbols_beat_index_benchmark": all(
            result["index_benchmark"]["beats_benchmark"]
            for result in results.values()
        ),
        "research_status": (
            "BACKTEST_CANDIDATE"
            if all(
                result["index_benchmark"]["beats_benchmark"]
                for result in results.values()
            )
            else "REJECTED"
        ),
        "promotion_status": "NOT_ELIGIBLE",
        "liquidation_proxy_maintenance_margin_rate": str(
            config.maintenance_margin_rate
        ),
        "symbols": results,
        "limitations": [
            "2時間足OHLCのため指値は接触時全量maker約定と仮定した。",
            "板・約定履歴がないためtakerスリッページは5bpの固定仮定である。",
            "清算価格はexecution leverage 1とmaintenance margin 0.5%の代理式である。",
            "銘柄間の同時運用ポートフォリオは評価していない。",
            "インデックス基準はNISAの年率10%を4年間複利運用した46.41%で、暗号資産側の個人税率は未反映である。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
