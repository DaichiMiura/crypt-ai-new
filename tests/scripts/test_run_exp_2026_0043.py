"""EXP-2026-0043頑健性検証をテストする。"""

from decimal import Decimal

import pandas as pd

from scripts.run_exp_2026_0043 import (
    _calendar_year_diagnostics,
    _rolling_12_month_diagnostics,
)


def test_calendar_year_diagnostics_preserves_continuous_equity() -> None:
    """年別集計が年初リセットせず連続equityを使うことをテストする。"""

    curve = [
        {"event_time": "2022-01-01T00:00:00Z", "equity": "100"},
        {"event_time": "2022-12-31T00:00:00Z", "equity": "120"},
        {"event_time": "2023-01-01T00:00:00Z", "equity": "121"},
        {"event_time": "2023-12-31T00:00:00Z", "equity": "108"},
    ]

    result = _calendar_year_diagnostics(curve)

    assert result[0]["return_rate"] == Decimal("0.2")
    assert result[1]["start_equity"] == Decimal("120")
    assert result[1]["return_rate"] == Decimal("-0.1")


def test_rolling_12_month_diagnostics_counts_positive_windows() -> None:
    """rolling 12か月の正の窓比率を集計することをテストする。"""

    dates = pd.date_range("2022-01-31", periods=14, freq="ME", tz="UTC")
    curve = [
        {"event_time": date, "equity": 100 + index * 10}
        for index, date in enumerate(dates)
    ]

    result = _rolling_12_month_diagnostics(curve)

    assert result["window_count"] == 2
    assert result["positive_window_fraction"] == Decimal("1")
