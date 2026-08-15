import json
from pathlib import Path
import sys

import pandas as pd

from scripts.run_exp_2026_0012_paper import main


def test_paper_runner_persists_state_and_skips_processed_bars(
    tmp_path: Path, monkeypatch, capsys
):
    """paper runnerが状態を保存し、再実行で処理済み日足を飛ばすことをテストする。"""
    timestamps = pd.date_range(
        "2025-08-16T00:00:00Z", "2026-08-17T00:00:00Z", freq="D"
    )
    bars = pd.DataFrame(
        {
            "event_time": timestamps,
            "open": [100] * len(timestamps),
            "high": [101] * len(timestamps),
            "low": [99] * len(timestamps),
            "close": [100] * len(timestamps),
            "is_interpolated": [False] * len(timestamps),
        }
    )
    bars_path = tmp_path / "bars.csv"
    state_path = tmp_path / "state.json"
    ledger_path = tmp_path / "ledger.jsonl"
    bars.to_csv(bars_path, index=False)
    arguments = [
        "run_exp_2026_0012_paper.py",
        "--bars",
        str(bars_path),
        "--state",
        str(state_path),
        "--ledger",
        str(ledger_path),
    ]

    monkeypatch.setattr(sys, "argv", arguments)
    main()
    first = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(sys, "argv", arguments)
    main()
    second = json.loads(capsys.readouterr().out)

    assert first["processed_bars_this_run"] == 2
    assert first["live_orders_sent"] == 0
    assert second["processed_bars_this_run"] == 0
    assert second["events_this_run"] == 0
    assert state_path.exists()
    assert ledger_path.exists()
