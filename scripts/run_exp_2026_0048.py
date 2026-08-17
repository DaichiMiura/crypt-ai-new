#!/usr/bin/env python3
"""EXP-2026-0048のFunding率クロスセクショナル・キャリーを実行する。"""

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
from crypt_ai.funding_carry import (  # noqa: E402
    FundingCarrySignalConfig,
    prepare_cross_sectional_funding_carry_signals,
)
from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from crypt_ai.research import CostModel  # noqa: E402


EXPERIMENT_ID = "EXP-2026-0048"
SYMBOLS = ("LINKUSDT", "UNIUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT", "AAVEUSDT")
EVALUATION_START = pd.Timestamp("2022-02-01T00:00:00Z")
EVALUATION_END = pd.Timestamp("2026-01-01T00:00:00Z")
OOS_START = pd.Timestamp("2025-07-01T00:00:00Z")
BAR_INTERVAL = pd.Timedelta(hours=2)
FUNDING_INTERVAL = pd.Timedelta(hours=8)
SIGNAL_WARMUP = pd.Timedelta(days=14)
SIGNAL_START = EVALUATION_START - SIGNAL_WARMUP
INITIAL_EQUITY = Decimal("1000")
RESERVE_CASH = Decimal("200")
LOT_NOTIONAL = Decimal("50")
LONG_CAP = Decimal("200")
SHORT_CAP = Decimal("200")
TOTAL_CAP = Decimal("400")
PER_SYMBOL_CAP = Decimal("50")
SIGNAL_CONFIG = FundingCarrySignalConfig(
    lookback_events=3,
    rebalance_events=3,
    long_count=2,
    short_count=2,
    signal_delay_bars=1,
)
BASE_COST_MODEL = CostModel(
    fee_rate=Decimal("0.0006"),
    round_trip_spread=Decimal("0.001"),
    slippage_per_fill=Decimal("0.0005"),
)
STRESS_COST_MODEL = CostModel(
    fee_rate=Decimal("0.0012"),
    round_trip_spread=Decimal("0.002"),
    slippage_per_fill=Decimal("0.001"),
)


def _read_trade_frame(path: Path) -> pd.DataFrame:
    """ZOOMEXのtrade 2時間足を読み込み、価格と補間行を検査する。

    Args:
        path: 対象銘柄のtrade-2h CSV。

    Returns:
        UTC時刻、OHLC、補間フラグを含むDataFrame。

    Raises:
        ValueError: 必須列、時刻、価格、補間フラグが不正な場合。
    """

    frame = pd.read_csv(path)
    required = {"event_time", "open", "high", "low", "close", "is_interpolated"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing trade columns in {path}: {sorted(missing)}")
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True, errors="coerce")
    if (
        frame["event_time"].isna().any()
        or frame["event_time"].duplicated().any()
        or not frame["event_time"].is_monotonic_increasing
    ):
        raise ValueError(f"invalid trade timestamps: {path}")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[column].isna().any() or not frame[column].gt(0).all():
            raise ValueError(f"invalid {column}: {path}")
    frame["is_interpolated"] = frame["is_interpolated"].map(_bool_value)
    if frame["is_interpolated"].any():
        raise ValueError(f"interpolated rows are not allowed: {path}")
    return frame.sort_values("event_time").reset_index(drop=True)


def _read_funding_frame(path: Path) -> pd.DataFrame:
    """Funding決済率を読み込み、UTC時刻と有限値を検査する。

    Args:
        path: 対象銘柄のfunding-rate CSV。

    Returns:
        Fundingイベント時刻、率、イベントフラグを含むDataFrame。

    Raises:
        ValueError: 必須列、時刻、重複、またはFunding率が不正な場合。
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
        or not frame["funding_rate"].map(lambda value: math.isfinite(float(value))).all()
    ):
        raise ValueError(f"invalid funding data: {path}")
    frame["funding_event"] = True
    return frame[["event_time", "funding_rate", "funding_event"]].sort_values(
        "event_time"
    )


def _validate_continuous(frame: pd.DataFrame, symbol: str) -> None:
    """2時間足の時刻が連続していることを検査する。

    Args:
        frame: `event_time`を持つ時系列DataFrame。
        symbol: エラーに表示する銘柄名。

    Raises:
        ValueError: 2時間間隔でない時刻が含まれる場合。
    """

    gaps = frame["event_time"].diff().dropna()
    if gaps.empty or not (gaps == BAR_INTERVAL).all():
        raise ValueError(f"non-continuous two-hour data: {symbol}")


def _validate_funding_events(frame: pd.DataFrame, symbol: str) -> None:
    """Fundingイベントが8時間間隔であることを検査する。

    Args:
        frame: `event_time`と`funding_event`を持つDataFrame。
        symbol: エラーに表示する銘柄名。

    Raises:
        ValueError: Fundingイベントが空、または間隔が不正な場合。
    """

    events = frame.loc[frame["funding_event"], "event_time"]
    gaps = events.diff().dropna()
    if events.empty or (not gaps.empty and not (gaps == FUNDING_INTERVAL).all()):
        raise ValueError(f"invalid eight-hour funding events: {symbol}")


def _prepare_frames(data_dir: Path) -> dict[str, pd.DataFrame]:
    """価格とFundingを整列し、結果確認前に固定したシグナルを生成する。

    Args:
        data_dir: EXP-2026-0015の銘柄別データディレクトリ。

    Returns:
        評価期間の銘柄別シグナルDataFrame。

    Raises:
        ValueError: データ品質、時刻整合、Fundingイベント、またはwarm-upが不正な場合。
    """

    source_frames: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        trade = _read_trade_frame(data_dir / symbol / "trade-2h.csv")
        trade = trade[
            (trade["event_time"] >= SIGNAL_START)
            & (trade["event_time"] < EVALUATION_END)
        ].reset_index(drop=True)
        if trade.empty or trade["event_time"].min() > SIGNAL_START:
            raise ValueError(f"insufficient warmup data: {symbol}")
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

    signals = prepare_cross_sectional_funding_carry_signals(
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
        first = evaluation.iloc[0]
        if first["desired_long_position"] != 0 or first["desired_short_position"] != 0:
            raise ValueError(f"evaluation must start flat: {symbol}")
        prepared[symbol] = evaluation

    timestamp_sets = {frozenset(frame["event_time"]) for frame in prepared.values()}
    if len(timestamp_sets) != 1:
        raise ValueError("evaluation timestamps differ across symbols")
    return prepared


def _allocation_config() -> AllocationConfig:
    """固定ロットとlong/short gross上限を定義した配分設定を返す。

    Returns:
        EXP-2026-0048専用の配分設定。

    Raises:
        ValueError: 配分上限が不整合な場合。
    """

    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=LONG_CAP,
        max_short_gross_notional=SHORT_CAP,
        max_total_gross_notional=TOTAL_CAP,
        per_symbol_max_notional=PER_SYMBOL_CAP,
        lot_notional=LOT_NOTIONAL,
        max_concurrent_long_positions=2,
        max_concurrent_short_positions=2,
    )


def _run_arm(
    frames: dict[str, pd.DataFrame],
    cost_model: CostModel,
) -> AllocatedPortfolioResult:
    """固定シグナルを指定費用モデルで研究会計する。

    Args:
        frames: 評価期間の銘柄別シグナルDataFrame。
        cost_model: 基本または費用2倍の費用仮定。

    Returns:
        配分承認後の会計結果。

    Raises:
        ValueError: 入力シグナルまたは会計条件が不正な場合。
    """

    return run_allocated_portfolio(frames, _allocation_config(), cost_model)


def _segment_summary(
    result: AllocatedPortfolioResult,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, object]:
    """equity曲線と監査イベントから指定期間の指標を集計する。

    Args:
        result: 研究会計の全期間結果。
        start: 集計開始時刻（UTC、含む）。
        end: 集計終了時刻（UTC、含まない）。

    Returns:
        期間初期equity、最終equity、純損益、DD、Funding、手数料などの指標。

    Raises:
        ValueError: 指定期間にequity曲線がない場合。
    """

    curve = pd.DataFrame(result.equity_curve)
    curve["event_time"] = pd.to_datetime(curve["event_time"], utc=True)
    in_period = curve[
        (curve["event_time"] >= start) & (curve["event_time"] < end)
    ].copy()
    if in_period.empty:
        raise ValueError("segment has no equity rows")
    prior = curve[curve["event_time"] < start]
    initial = Decimal(str(prior.iloc[-1]["equity"])) if not prior.empty else Decimal(str(in_period.iloc[0]["equity"]))
    equities = [initial, *[Decimal(str(value)) for value in in_period["equity"]]]
    peak = initial
    max_drawdown = Decimal("0")
    for equity in equities[1:]:
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - Decimal("1"))

    events = []
    for event in result.events:
        event_time = pd.Timestamp(event["event_time"])
        if start <= event_time < end:
            events.append(event)
    funding_cash_flow = sum(
        (
            Decimal(str(event["funding_delta"]))
            for event in events
            if event["event_type"] == "FUNDING"
        ),
        Decimal("0"),
    )
    total_fees = sum(
        (Decimal(str(event["fee"])) for event in events if "fee" in event),
        Decimal("0"),
    )
    final = equities[-1]
    return {
        "start_utc": start.isoformat(),
        "end_utc_exclusive": end.isoformat(),
        "initial_equity": str(initial),
        "final_equity": str(final),
        "net_pnl": str(final - initial),
        "return_rate": str(final / initial - Decimal("1")),
        "max_drawdown": str(max_drawdown),
        "funding_cash_flow": str(funding_cash_flow),
        "total_fees": str(total_fees),
        "post_funding_residual": str(final - initial - funding_cash_flow),
        "entry_count": sum(event["event_type"] == "ENTRY" for event in events),
        "exit_count": sum(event["event_type"] == "EXIT" for event in events),
        "funding_event_count": sum(event["event_type"] == "FUNDING" for event in events),
        "mean_allocated_gross_notional": str(
            in_period["allocated_gross_notional"].map(Decimal).mean()
        ),
    }


def _decision(
    base_oos: dict[str, object],
    stress_oos: dict[str, object],
) -> tuple[str, list[str]]:
    """事前登録したOOS棄却条件を機械的に評価する。

    Args:
        base_oos: 基本費用モデルのOOS指標。
        stress_oos: 費用2倍モデルのOOS指標。

    Returns:
        研究判定と棄却理由のタプル。
    """

    reasons: list[str] = []
    if Decimal(str(base_oos["net_pnl"])) <= 0:
        reasons.append("oos_net_pnl_nonpositive")
    if Decimal(str(base_oos["net_pnl"])) <= Decimal("0"):
        reasons.append("oos_does_not_beat_cash_control")
    if Decimal(str(base_oos["funding_cash_flow"])) <= 0:
        reasons.append("oos_funding_cash_flow_nonpositive")
    if Decimal(str(base_oos["max_drawdown"])) <= Decimal("-0.20"):
        reasons.append("oos_max_drawdown_below_minus_20pct")
    if Decimal(str(stress_oos["net_pnl"])) <= 0:
        reasons.append("stress_oos_net_pnl_nonpositive")
    return ("BACKTEST_CANDIDATE" if not reasons else "REJECTED", reasons)


def _bool_value(value: object) -> bool:
    """CSV由来のbool値を厳密に正規化する。

    Args:
        value: 真偽値または文字列化された真偽値。

    Returns:
        正規化したbool値。

    Raises:
        ValueError: 真偽値として解釈できない場合。
    """

    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"invalid boolean: {value}")


def main() -> None:
    """EXP-2026-0048を基本費用と費用2倍stressで実行し成果物を保存する。

    Raises:
        ValueError: 入力データ、シグナル、会計、または判定条件が不正な場合。
    """

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0048")
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
    decision, rejection_reasons = _decision(
        summaries["base"]["oos"], summaries["stress_2x_cost"]["oos"]
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for symbol, frame in frames.items():
        frame[
            [
                "event_time",
                "funding_rate",
                "funding_event",
                "funding_lookback_mean",
                "funding_carry_rank",
                "funding_carry_spread",
                "funding_signal_event",
                "desired_long_position",
                "desired_short_position",
            ]
        ].to_csv(args.output_dir / f"{symbol}-signals.csv", index=False)
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(args.output_dir / f"{arm}-events.csv", index=False)
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )

    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "research_decision": decision,
        "rejection_reasons": rejection_reasons,
        "promotion_status": "NOT_ELIGIBLE",
        "parameters": {
            "symbols": SYMBOLS,
            "evaluation_start": EVALUATION_START.isoformat(),
            "evaluation_end": EVALUATION_END.isoformat(),
            "oos_start": OOS_START.isoformat(),
            "signal_start": SIGNAL_START.isoformat(),
            "bar_interval": "2h",
            "funding_interval": "8h",
            "lookback_events": SIGNAL_CONFIG.lookback_events,
            "rebalance_events": SIGNAL_CONFIG.rebalance_events,
            "long_count": SIGNAL_CONFIG.long_count,
            "short_count": SIGNAL_CONFIG.short_count,
            "signal_delay_bars": SIGNAL_CONFIG.signal_delay_bars,
            "initial_equity": str(INITIAL_EQUITY),
            "reserve_cash": str(RESERVE_CASH),
            "lot_notional": str(LOT_NOTIONAL),
            "long_cap": str(LONG_CAP),
            "short_cap": str(SHORT_CAP),
            "total_cap": str(TOTAL_CAP),
            "per_symbol_cap": str(PER_SYMBOL_CAP),
        },
        "cost_models": {
            "base": {
                "fee_rate": str(BASE_COST_MODEL.fee_rate),
                "round_trip_spread": str(BASE_COST_MODEL.round_trip_spread),
                "slippage_per_fill": str(BASE_COST_MODEL.slippage_per_fill),
            },
            "stress_2x_cost": {
                "fee_rate": str(STRESS_COST_MODEL.fee_rate),
                "round_trip_spread": str(STRESS_COST_MODEL.round_trip_spread),
                "slippage_per_fill": str(STRESS_COST_MODEL.slippage_per_fill),
            },
        },
        "arms": summaries,
        "limitations": [
            "6銘柄固定ユニバースであり、ZOOMEX全銘柄のpoint-in-time universeではない。",
            "Funding率の受信時刻、板、部分約定、証拠金、清算、注文拒否は再現しない。",
            "gross-neutralは市場β中立を意味せず、銘柄間の共通価格変動は残る。",
            "ZOOMEX公開履歴上のresearch結果であり、paper・shadow・liveへの自動昇格を許可しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
