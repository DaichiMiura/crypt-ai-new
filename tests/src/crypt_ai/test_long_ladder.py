"""ロング段階買い増し会計をテストする。"""

from decimal import Decimal

import pandas as pd

from crypt_ai.allocation import AllocationConfig
from crypt_ai.long_ladder import run_long_ladder_portfolio
from crypt_ai.research import CostModel


def _config() -> AllocationConfig:
    """テスト用配分設定を作る。"""

    return AllocationConfig(
        currency="USDT",
        allowed_symbols=("AAA",),
        initial_equity=Decimal("1000"),
        reserve_cash=Decimal("0"),
        max_long_gross_notional=Decimal("500"),
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=Decimal("500"),
        per_symbol_max_notional=Decimal("500"),
        lot_notional=Decimal("100"),
        max_concurrent_long_positions=1,
        max_concurrent_short_positions=1,
    )


def _frame(
    desired: list[int],
    opens: list[float],
    lows: list[float],
    *,
    atr: list[float] | None = None,
) -> pd.DataFrame:
    """テスト用OHLCシグナルを作る。"""

    frame = pd.DataFrame(
        {
            "event_time": pd.date_range(
                "2026-01-01", periods=len(desired), freq="2h", tz="UTC"
            ),
            "open": opens,
            "high": [value + 1 for value in opens],
            "low": lows,
            "close": opens,
            "desired_long_position": desired,
            "is_interpolated": False,
            "funding_rate": 0,
        }
    )
    if atr is not None:
        frame["entry_atr"] = atr
    return frame


def test_fixed_ladder_triggers_on_low_and_adds_at_next_open() -> None:
    """確定足lowで発火した段階を次足openで買い増すことをテストする。"""

    result = run_long_ladder_portfolio(
        {
            "AAA": _frame(
                [0, 1, 1, 1, 0],
                [100, 100, 95, 95, 95],
                [100, 97, 94, 94, 94],
            )
        },
        _config(),
        CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
        fixed_drawdowns=(
            Decimal("0.0236"),
            Decimal("0.0382"),
            Decimal("0.0618"),
            Decimal("0.10"),
        ),
    )

    additions = [event for event in result.events if event["event_type"] == "ADD"]
    assert [event["level_index"] for event in additions] == [1, 2]
    assert [event["event_time"] for event in additions] == [
        "2026-01-01T04:00:00+00:00",
        "2026-01-01T06:00:00+00:00",
    ]


def test_exit_discards_pending_addition() -> None:
    """退出シグナルが買い増し予定より優先されることをテストする。"""

    result = run_long_ladder_portfolio(
        {"AAA": _frame([0, 1, 0], [100, 100, 90], [100, 90, 90])},
        _config(),
        CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
        fixed_drawdowns=(
            Decimal("0.0236"),
            Decimal("0.0382"),
            Decimal("0.0618"),
            Decimal("0.10"),
        ),
    )

    assert not any(event["event_type"] == "ADD" for event in result.events)
    assert any(event["event_type"] == "EXIT" for event in result.events)


def test_atr_ladder_freezes_entry_atr() -> None:
    """ATR段階がentry時のATRを固定して使うことをテストする。"""

    result = run_long_ladder_portfolio(
        {
            "AAA": _frame(
                [0, 1, 1, 0],
                [100, 100, 98, 98],
                [100, 98.5, 98, 98],
                atr=[1, 1, 100, 100],
            )
        },
        _config(),
        CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
        atr_multipliers=(
            Decimal("1"),
            Decimal("1.618"),
            Decimal("2.618"),
            Decimal("4.236"),
        ),
    )

    triggers = [
        event for event in result.events if event["event_type"] == "ADD_TRIGGER"
    ]
    assert [event["level_index"] for event in triggers] == [1, 2]


def test_ladder_never_exceeds_five_lots() -> None:
    """複数段階へ一度に到達しても最大5ロットを超えないことをテストする。"""

    result = run_long_ladder_portfolio(
        {"AAA": _frame([0, 1, 1, 0], [100, 100, 50, 50], [100, 50, 50, 50])},
        _config(),
        CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
        fixed_drawdowns=(
            Decimal("0.0236"),
            Decimal("0.0382"),
            Decimal("0.0618"),
            Decimal("0.10"),
        ),
    )

    exit_event = next(event for event in result.events if event["event_type"] == "EXIT")
    assert exit_event["lot_count"] == 5


def test_ladder_metrics_include_addition_fees() -> None:
    """集計手数料に買い増し約定の手数料を含むことをテストする。"""

    result = run_long_ladder_portfolio(
        {"AAA": _frame([0, 1, 1, 0], [100, 100, 90, 90], [100, 90, 90, 90])},
        _config(),
        CostModel(Decimal("0.01"), Decimal("0"), Decimal("0")),
        fixed_drawdowns=(
            Decimal("0.0236"),
            Decimal("0.0382"),
            Decimal("0.0618"),
            Decimal("0.10"),
        ),
    )

    event_fees = sum(
        (Decimal(event["fee"]) for event in result.events if "fee" in event),
        Decimal("0"),
    )
    assert Decimal(result.metrics["total_fees"]) == event_fees
