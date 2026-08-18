from decimal import Decimal

import numpy as np
import pandas as pd

from scripts.run_exp_2026_0057_source_gate import (
    BETA_WARMUP_END,
    FEATURE_NAMES,
    HUBER_SLOPE,
    ResidualSample,
    _decision_betas,
    fit_huber,
    predict,
)
from scripts.run_exp_2026_0056_source_gate import CONTEXT_SYMBOL, SOURCE_SYMBOLS


def _sample(index: int, *, symbol: str = "LINKUSDT") -> ResidualSample:
    """Huber modelテスト用の残差標本を作る。"""

    feature = index / 100.0
    features = (feature, *([0.0] * (len(FEATURE_NAMES) - 1)))
    return ResidualSample(
        pd.Timestamp("2023-01-01T00:00:00Z") + pd.Timedelta(hours=6 * index),
        symbol, features, feature / 10.0, feature / 20.0, feature / 30.0,
        Decimal("10"), Decimal("10.1"),
    )


def test_huber_residual_model_is_deterministic():
    """固定Pseudo-Huber残差modelが再現する。"""

    samples = [_sample(index) for index in range(1000)]
    evaluation = [samples[100], samples[900]]

    first = predict(fit_huber(samples, "residual"), evaluation)
    second = predict(fit_huber(samples, "residual"), evaluation)

    assert HUBER_SLOPE == 0.02
    assert np.array_equal(first, second)
    assert first[1] > first[0]


def test_huber_market_model_deduplicates_decision_times():
    """market modelが同時刻の銘柄行を一つのBTC labelとして扱う。"""

    samples = []
    for index in range(100):
        samples.extend([_sample(index, symbol="LINKUSDT"), _sample(index, symbol="UNIUSDT")])

    values = predict(fit_huber(samples, "market"), samples[:2], market=True)

    assert len(values) == 2
    assert values[0] == values[1]


def test_beta_does_not_change_when_future_prices_change():
    """判断後の価格変更がpoint-in-time betaを変えない。"""

    index = pd.date_range("2022-01-01T00:00:00Z", "2022-02-05T00:00:00Z", freq="15min")
    steps = np.arange(len(index), dtype=float)
    btc_close = np.exp(0.00005 * steps + 0.002 * np.sin(steps / 17.0))
    trade_frames = {
        CONTEXT_SYMBOL: pd.DataFrame({"close": btc_close}, index=index),
    }
    for offset, symbol in enumerate(SOURCE_SYMBOLS, start=1):
        local_close = np.exp(
            0.00004 * steps + (0.001 + offset / 10000.0) * np.sin(steps / 17.0)
            + 0.0005 * np.cos(steps / (11.0 + offset))
        )
        trade_frames[symbol] = pd.DataFrame({"close": local_close}, index=index)
    decision_times = list(pd.date_range(
        "2022-01-02T06:00:00Z", BETA_WARMUP_END, freq="6h"
    ))
    original, _ = _decision_betas(trade_frames, decision_times)
    target_time = decision_times[-1]

    changed_frames = {symbol: frame.copy() for symbol, frame in trade_frames.items()}
    for frame in changed_frames.values():
        frame.loc[frame.index >= target_time, "close"] *= 10.0
    changed, _ = _decision_betas(changed_frames, decision_times)

    for symbol in SOURCE_SYMBOLS:
        assert original[symbol].loc[target_time] == changed[symbol].loc[target_time]
