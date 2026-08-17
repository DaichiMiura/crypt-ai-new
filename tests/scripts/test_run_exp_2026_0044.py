"""EXP-2026-0044ランナーをテストする。"""

from decimal import Decimal
from types import SimpleNamespace

from scripts.run_exp_2026_0044 import (
    ARM_FRACTIONS,
    _decision_rules,
    _return_to_drawdown,
)


def _result(final: str, drawdown: str) -> SimpleNamespace:
    """判定テスト用の最小結果を作る。"""

    initial = Decimal("1000")
    final_equity = Decimal(final)
    return SimpleNamespace(
        metrics={
            "final_equity": final,
            "return_rate": str(final_equity / initial - 1),
            "max_drawdown": drawdown,
        }
    )


def test_arms_separate_compounding_from_full_allocation() -> None:
    """固定、20%複利、50%全額配分の3armをテストする。"""

    assert ARM_FRACTIONS == {
        "fixed_200": None,
        "equity_20pct_per_slot": Decimal("0.20"),
        "equity_50pct_per_slot": Decimal("0.50"),
    }


def test_return_to_drawdown_uses_absolute_drawdown() -> None:
    """return/DD比が負の最大DDを絶対値として扱うことをテストする。"""

    assert _return_to_drawdown(
        {"return_rate": "0.60", "max_drawdown": "-0.30"}
    ) == Decimal("2")


def test_decision_rules_require_return_and_risk_improvement() -> None:
    """20%枠と50%枠の事前登録条件をテストする。"""

    rules = _decision_rules(
        {
            "fixed_200": _result("1500", "-0.30"),
            "equity_20pct_per_slot": _result("1600", "-0.28"),
            "equity_50pct_per_slot": _result("2200", "-0.50"),
        }
    )

    assert rules["equity_20pct_per_slot_candidate"]
    assert rules["equity_50pct_per_slot_candidate"]
