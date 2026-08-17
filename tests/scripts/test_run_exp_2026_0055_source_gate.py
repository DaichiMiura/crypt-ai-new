from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from scripts.run_exp_2026_0054_development import InstrumentRule
from scripts.run_exp_2026_0055_source_gate import (
    FEATURE_NAMES,
    FIT_START,
    SEALED_TARGET_SYMBOLS,
    Sample,
    build_samples,
    calibrated_probabilities,
    evaluate_entries,
    fit_logistic,
    fit_platt,
)


def _sample(index: int, event: int, *, symbol: str = "LINKUSDT") -> Sample:
    """分類modelテスト用の固定次元標本を作る。"""

    signal = -2.0 + 4.0 * index / 99.0
    return Sample(
        FIT_START + pd.Timedelta(hours=6 * index),
        symbol,
        (signal, *([0.0] * (len(FEATURE_NAMES) - 1))),
        event,
        0.01 if event else -0.005,
        Decimal("10"),
        Decimal("10.1") if event else Decimal("9.95"),
    )


def test_logistic_is_deterministic_and_orders_signal():
    """固定入力のlogisticが再現し、正例側へ高いlogitを返す。"""

    samples = [_sample(index, int(index >= 50)) for index in range(100)]

    first = fit_logistic(samples).predict_margin([samples[10], samples[90]])
    second = fit_logistic(samples).predict_margin([samples[10], samples[90]])

    assert np.array_equal(first, second)
    assert first[1] > first[0]


def test_platt_returns_finite_ordered_probabilities():
    """時間分離校正器が有限確率を返しlogit順序を保つ。"""

    samples = [_sample(index, int(index >= 50)) for index in range(100)]
    margins = np.linspace(-3.0, 3.0, 100)

    coefficients = fit_platt(margins, samples)
    probabilities = calibrated_probabilities(np.asarray([-2.0, 2.0]), coefficients)

    assert np.isfinite(probabilities).all()
    assert 0.0 < probabilities[0] < probabilities[1] < 1.0


def test_build_samples_rejects_sealed_target_symbols():
    """source gateのdataset builderが未観測target指定を拒否する。"""

    with pytest.raises(ValueError, match="sealed target"):
        build_samples({}, symbols=(SEALED_TARGET_SYMBOLS[0],))


def test_evaluate_entries_applies_all_cost_components():
    """entryのgross損益からfee・spread・slippageを控除する。"""

    sample = _sample(0, 1)
    rule = InstrumentRule(Decimal("0.1"), Decimal("0.1"), Decimal("5"))
    empty_funding = pd.DataFrame(
        {"funding_rate": []}, index=pd.DatetimeIndex([], tz="UTC")
    )
    mark = pd.DataFrame(
        {"open": [Decimal("10")]}, index=[sample.decision_time + pd.Timedelta(hours=1)]
    )

    result = evaluate_entries(
        [sample], np.asarray([True]), np.asarray([0.9]),
        {sample.symbol: rule}, {sample.symbol: mark}, {sample.symbol: empty_funding},
    )

    trade = result["trades"][0]
    assert result["completed_round_trips"] == 1
    assert Decimal(trade["fees"]) > 0
    assert Decimal(trade["spread_cost"]) > 0
    assert Decimal(trade["slippage_cost"]) > 0
    assert Decimal(trade["net_pnl"]) < Decimal(trade["gross_price_pnl"])
