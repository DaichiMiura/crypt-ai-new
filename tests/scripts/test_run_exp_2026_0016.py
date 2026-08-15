from decimal import Decimal

from scripts.run_exp_2026_0016 import _aggregate_deltas, _compare_metrics


def _metrics(final: str, pnl: str, drawdown: str, normal: int = 1) -> dict[str, object]:
    """比較テスト用metricsを作る。"""
    return {
        "final_equity": final,
        "net_pnl": pnl,
        "max_drawdown": drawdown,
        "normal_stop_count": normal,
        "emergency_stop_count": 0,
        "time_exit_count": 1,
    }


def test_compare_metrics_reports_variant_delta():
    """通常損切り無効版とcontrolの差分を計算することをテストする。"""
    result = _compare_metrics(
        _metrics("1000", "0", "-0.1"),
        _metrics("1100", "100", "-0.2", normal=0),
    )

    assert Decimal(result["final_equity_delta"]) == Decimal("100")
    assert Decimal(result["max_drawdown_delta"]) == Decimal("-0.1")
    assert result["normal_stop_count_delta"] == -1


def test_aggregate_deltas_counts_improved_symbols():
    """銘柄別差分の改善数と中央値を集計することをテストする。"""
    result = _aggregate_deltas(
        {
            "A": {
                "final_equity_delta": "10",
                "net_pnl_delta": "10",
            },
            "B": {
                "final_equity_delta": "-5",
                "net_pnl_delta": "-5",
            },
        }
    )

    assert result["symbols_improved"] == 1
    assert result["symbols_worsened"] == 1
    assert Decimal(result["median_final_equity_delta"]) == Decimal("2.5")
