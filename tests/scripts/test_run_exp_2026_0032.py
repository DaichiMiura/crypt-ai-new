from decimal import Decimal

from scripts.run_exp_2026_0032 import (
    ARM_MAX_PAIRS,
    ENTRY_BASIS,
    EXIT_BASIS,
    MAX_HOLDING_BARS,
    PAIR_NOTIONAL,
    _benchmark,
)


def test_basis_parameters_are_fixed_before_backtest():
    """entry0.50%、exit0.10%、最大30日、1ペア100 USDTを固定することをテストする。"""
    assert ENTRY_BASIS == Decimal("0.005")
    assert EXIT_BASIS == Decimal("0.001")
    assert MAX_HOLDING_BARS == 360
    assert PAIR_NOTIONAL == Decimal("100")


def test_basis_arms_limit_concurrent_pairs():
    """ペア同時保有上限を1・2・4に固定することをテストする。"""
    assert ARM_MAX_PAIRS == {
        "cash_control": 0,
        "pair_1": 1,
        "pair_2": 2,
        "pair_4": 4,
    }


def test_benchmark_uses_four_year_compounding():
    """年率10%を4年間複利計算することをテストする。"""
    result = _benchmark(Decimal("1000"))
    assert result["benchmark_final_equity"] == "1464.10000000"
    assert result["beats_benchmark"] is False
