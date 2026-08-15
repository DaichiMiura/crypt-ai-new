#!/usr/bin/env python3
"""確定日足ファイルからEXP-2026-0012をpaper実行する。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.paper import (  # noqa: E402
    append_paper_events,
    load_paper_config,
    load_paper_state,
    process_paper_bar,
    save_paper_state,
)
from crypt_ai.research import (  # noqa: E402
    INTERPOLATED_COLUMN,
    inspect_daily_data,
    prepare_atr_trailing_exit_signals,
)


def _read_bars(path: Path) -> pd.DataFrame:
    """BTC/JPY日足CSVをpaper戦略用に正規化する。

    Args:
        path: event_timeとOHLCを持つ日足CSV。

    Returns:
        UTC時刻、数値OHLC、補間フラグを持つデータ。

    Raises:
        ValueError: 必須列が不足する場合。
    """

    frame = pd.read_csv(path)
    required = {"event_time", "open", "high", "low", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing paper bar columns: {sorted(missing)}")
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if INTERPOLATED_COLUMN not in frame:
        frame[INTERPOLATED_COLUMN] = False
    else:
        frame[INTERPOLATED_COLUMN] = (
            frame[INTERPOLATED_COLUMN]
            .fillna(False)
            .astype(str)
            .str.lower()
            .isin(["1", "true", "yes"])
        )
    return frame.sort_values("event_time").reset_index(drop=True)


def main() -> None:
    """未処理の確定日足だけを仮想約定し、状態と台帳を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument(
        "--global-config", type=Path, default=Path("config/paper-risk-limits.yaml")
    )
    parser.add_argument(
        "--strategy-config",
        type=Path,
        default=Path("config/strategies/exp-2026-0012-paper.yaml"),
    )
    parser.add_argument(
        "--state", type=Path, default=Path("var/paper/EXP-2026-0012/state.json")
    )
    parser.add_argument(
        "--ledger", type=Path, default=Path("var/paper/EXP-2026-0012/ledger.jsonl")
    )
    args = parser.parse_args()

    config = load_paper_config(args.global_config, args.strategy_config)
    frame = _read_bars(args.bars)
    quality = inspect_daily_data(frame)
    if quality["duplicate_count"] or quality["missing_intervals"]:
        raise ValueError(f"refusing incomplete paper bars: {quality}")
    if frame.empty or frame.iloc[0]["event_time"] > config.start_utc - pd.Timedelta(
        days=365
    ):
        raise ValueError("paper bars require at least 365 days of warm-up")

    signals = prepare_atr_trailing_exit_signals(
        frame,
        entry_window=55,
        baseline_exit_window=20,
        regime_window=200,
        atr_window=20,
        atr_multiplier=3.0,
    )
    state = load_paper_state(args.state, config)
    pending = signals[signals["event_time"] >= config.start_utc]
    if state.last_event_time is not None:
        pending = pending[
            pending["event_time"] >= pd.Timestamp(state.last_event_time)
        ]
    generated_events: list[dict[str, object]] = []
    processed_before = state.processed_bars
    for row in pending.itertuples(index=False):
        generated_events.extend(process_paper_bar(state, row, config))
    append_paper_events(args.ledger, generated_events)
    save_paper_state(args.state, state)
    result = {
        "strategy_id": config.strategy_id,
        "environment": "paper",
        "live_orders_sent": 0,
        "processed_bars_this_run": state.processed_bars - processed_before,
        "events_this_run": len(generated_events),
        "last_event_time": state.last_event_time,
        "cash": state.cash,
        "quantity": state.quantity,
        "equity": state.previous_equity,
        "halted": state.halted,
        "halt_reasons": state.halt_reasons,
        "state_path": str(args.state),
        "ledger_path": str(args.ledger),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
