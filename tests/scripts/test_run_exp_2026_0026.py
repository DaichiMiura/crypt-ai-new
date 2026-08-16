from decimal import Decimal

from scripts.run_exp_2026_0026 import reconstruct_trades, summarize_trades


def test_reconstruct_trades_pairs_exit_and_funding():
    """ENTRYからFundingとSTOP_LOSSまでを1トレードへ対応付けることをテストする。"""
    events = [
        {
            "event_time": "2022-01-01T02:00:00+00:00",
            "event_type": "ENTRY",
            "quantity": "2",
            "reference_price": "100",
            "execution_price": "99.9",
            "fee_delta": "0.1",
        },
        {
            "event_time": "2022-01-01T04:00:00+00:00",
            "event_type": "FUNDING",
            "funding_delta": "0.2",
            "fee_delta": "0",
        },
        {
            "event_time": "2022-01-01T06:00:00+00:00",
            "event_type": "STOP_LOSS",
            "quantity": "2",
            "reference_price": "110",
            "fee_delta": "0.12",
        },
    ]
    equity_curve = [
        {"event_time": "2022-01-01T00:00:00+00:00", "equity": "250"},
        {"event_time": "2022-01-01T02:00:00+00:00", "equity": "249"},
        {"event_time": "2022-01-01T04:00:00+00:00", "equity": "250"},
        {"event_time": "2022-01-01T06:00:00+00:00", "equity": "230"},
    ]

    trades = reconstruct_trades(events, equity_curve)

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "STOP_LOSS"
    assert trades[0]["net_pnl"] == "-20"
    assert trades[0]["hold_bars"] == 2
    assert trades[0]["funding_cash_flow"] == "0.2"
    assert trades[0]["fees"] == "0.22"


def test_reconstruct_trades_records_open_position_at_end():
    """退出イベントのない建玉をOPENトレードとして記録することをテストする。"""
    events = [
        {
            "event_time": "2022-01-01T02:00:00+00:00",
            "event_type": "ENTRY",
            "quantity": "1",
            "reference_price": "100",
            "execution_price": "99",
            "fee_delta": "0.1",
        }
    ]
    equity_curve = [
        {"event_time": "2022-01-01T00:00:00+00:00", "equity": "250"},
        {"event_time": "2022-01-01T02:00:00+00:00", "equity": "249"},
        {"event_time": "2022-01-01T04:00:00+00:00", "equity": "245"},
    ]

    trades = reconstruct_trades(events, equity_curve)

    assert trades[0]["exit_reason"] == "OPEN"
    assert trades[0]["net_pnl"] == "-5"
    assert trades[0]["hold_bars"] == 1


def test_summarize_trades_reports_profit_factor_and_win_rate():
    """トレード集合の勝率、損益合計、Profit Factorをテストする。"""
    trades = [
        {
            "exit_reason": "SMA_EXIT",
            "net_pnl": "10",
            "hold_bars": 2,
            "funding_cash_flow": "1",
            "fees": "0.2",
            "entry_notional": "200",
        },
        {
            "exit_reason": "STOP_LOSS",
            "net_pnl": "-5",
            "hold_bars": 4,
            "funding_cash_flow": "-1",
            "fees": "0.3",
            "entry_notional": "210",
        },
    ]

    summary = summarize_trades(trades)

    assert summary["trade_count"] == 2
    assert summary["win_rate"] == "0.5"
    assert summary["total_net_pnl"] == "5"
    assert summary["profit_factor"] == "2"
    assert summary["mean_entry_notional"] == "205"
