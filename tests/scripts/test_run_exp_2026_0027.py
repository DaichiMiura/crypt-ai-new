from decimal import Decimal

from scripts.run_exp_2026_0027 import WIDER_STOP_ATR, _aggregate, _compare_metrics


def test_wider_stop_is_single_preregistered_variant():
    """広いストップvariantが6 ATRであることをテストする。"""
    assert WIDER_STOP_ATR == Decimal("6")


def test_compare_metrics_reports_exit_and_drawdown_deltas():
    """ストップ幅変更の損益、退出件数、DD差をテストする。"""
    control = {
        "final_equity": "200",
        "max_drawdown": "-0.40",
        "entry_count": 10,
        "stop_loss_count": 8,
        "sma_exit_count": 1,
        "time_exit_count": 1,
        "max_position_notional": "210",
        "total_funding_cash_flow": "-2",
        "total_fees": "5",
        "index_benchmark": {"beats_benchmark": False},
    }
    variant = {
        "final_equity": "215",
        "max_drawdown": "-0.35",
        "entry_count": 8,
        "stop_loss_count": 4,
        "sma_exit_count": 2,
        "time_exit_count": 2,
        "max_position_notional": "220",
        "total_funding_cash_flow": "-3",
        "total_fees": "4",
        "index_benchmark": {"beats_benchmark": False},
    }
    control_trades = [
        {
            "exit_reason": "STOP_LOSS",
            "net_pnl": "-10",
            "hold_bars": 5,
            "funding_cash_flow": "0",
            "fees": "1",
            "entry_notional": "200",
        }
    ]
    variant_trades = [
        {
            "exit_reason": "TIME_EXIT",
            "net_pnl": "5",
            "hold_bars": 10,
            "funding_cash_flow": "0",
            "fees": "1",
            "entry_notional": "200",
        }
    ]

    comparison = _compare_metrics(control, variant, control_trades, variant_trades)

    assert comparison["final_equity_delta"] == "15"
    assert comparison["closed_net_pnl_delta"] == "15"
    assert comparison["max_drawdown_delta"] == "0.05"
    assert comparison["stop_loss_count_delta"] == -4
    assert comparison["time_exit_count_delta"] == 1


def test_aggregate_counts_wider_stop_improvements():
    """広いストップの最終資産・DD改善銘柄数をテストする。"""
    comparisons = {
        "A": {
            "final_equity_delta": "10",
            "closed_net_pnl_delta": "10",
            "max_drawdown_delta": "0.05",
            "stop_loss_count_delta": -2,
            "time_exit_count_delta": 1,
            "max_position_notional_delta": "5",
        },
        "B": {
            "final_equity_delta": "-3",
            "closed_net_pnl_delta": "-3",
            "max_drawdown_delta": "-0.02",
            "stop_loss_count_delta": 1,
            "time_exit_count_delta": -1,
            "max_position_notional_delta": "-2",
        },
    }

    aggregate = _aggregate(comparisons)

    assert aggregate["symbols_improved_final_equity"] == 1
    assert aggregate["symbols_worsened_final_equity"] == 1
    assert aggregate["symbols_improved_max_drawdown"] == 1
    assert aggregate["sum_stop_loss_count_delta"] == -1
