from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from scripts.run_exp_2026_0054_development import InstrumentRule
from scripts.run_exp_2026_0056_source_gate import (
    FEATURE_NAMES,
    SEALED_TARGET_SYMBOLS,
    Sample,
    _base_features,
    _pinball,
    _trade_net_return,
    build_samples,
    evaluate,
    fit_quantile,
)


def _sample(symbol: str, score_feature: float = 0.0) -> Sample:
    """ranking/quantileテスト用標本を作る。"""

    return Sample(
        pd.Timestamp("2025-01-01T00:00:00Z"), symbol,
        (score_feature, *([0.0] * (len(FEATURE_NAMES) - 1))),
        0.01, Decimal("10"), Decimal("10.1"),
    )


def _price_frame(times: pd.DatetimeIndex, offset: float = 0.0) -> pd.DataFrame:
    """15分特徴量テスト用の連続価格frameを作る。"""

    close = np.linspace(100.0 + offset, 110.0 + offset, len(times))
    return pd.DataFrame({
        "open": close - 0.1, "high": close + 0.2, "low": close - 0.3,
        "close": close, "volume": np.linspace(10.0, 30.0, len(times)),
        "turnover": np.linspace(1000.0, 4000.0, len(times)),
    }, index=times)


def test_build_samples_rejects_sealed_target():
    """source builderが新しい未観測targetを拒否する。"""

    with pytest.raises(ValueError, match="sealed targets"):
        build_samples({}, {}, {}, {}, symbols=(SEALED_TARGET_SYMBOLS[0],))


def test_base_features_use_only_completed_15m_bars():
    """判断境界以後の変更が26特徴量の基礎部分へ混入しない。"""

    decision = pd.Timestamp("2025-01-02T00:00:00Z")
    times = pd.date_range(decision - pd.Timedelta(hours=25), periods=105, freq="15min")
    trade = _price_frame(times)
    btc = _price_frame(times, 100.0)

    first = _base_features(trade, btc, decision)
    trade.loc[trade.index >= decision, "close"] = 1_000_000.0
    second = _base_features(trade, btc, decision)

    assert len(first) == len(FEATURE_NAMES) - 3
    assert first == second


def test_quantile_model_is_deterministic():
    """固定seedのq40 modelが同じ予測を返す。"""

    samples = [
        Sample(
            pd.Timestamp("2023-01-01T00:00:00Z") + pd.Timedelta(hours=6 * index),
            "LINKUSDT", (index / 100.0, *([0.0] * (len(FEATURE_NAMES) - 1))),
            -0.02 + index / 2500.0, Decimal("10"), Decimal("10"),
        )
        for index in range(100)
    ]

    first = fit_quantile(samples).predict(
        __import__("xgboost").DMatrix(np.asarray([samples[20].features, samples[80].features]))
    )
    second = fit_quantile(samples).predict(
        __import__("xgboost").DMatrix(np.asarray([samples[20].features, samples[80].features]))
    )

    assert np.array_equal(first, second)


def test_trade_net_return_includes_costs():
    """横ばい価格でも固定費用によりnet returnが負になる。"""

    decision = pd.Timestamp("2025-01-01T00:00:00Z")
    rule = InstrumentRule(Decimal("0.1"), Decimal("0.1"), Decimal("5"))
    funding = pd.DataFrame({"funding_rate": []}, index=pd.DatetimeIndex([], tz="UTC"))

    value = _trade_net_return(
        "LINKUSDT", decision, Decimal("10"), Decimal("10"), {"LINKUSDT": rule},
        {"LINKUSDT": pd.DataFrame()}, {"LINKUSDT": funding},
    )

    assert value < -0.003


def test_evaluate_requires_margin_and_positive_q40():
    """rank marginまたはq40が不足すれば取引しない。"""

    samples = [_sample(symbol) for symbol in ("AAVEUSDT", "ETHUSDT", "SOLUSDT")]
    rules = {
        sample.symbol: InstrumentRule(Decimal("0.1"), Decimal("0.1"), Decimal("5"))
        for sample in samples
    }
    funding = {
        sample.symbol: pd.DataFrame({"funding_rate": []}, index=pd.DatetimeIndex([], tz="UTC"))
        for sample in samples
    }

    result = evaluate(
        samples, np.asarray([1.0, 0.99, 0.98]), np.asarray([-0.01, 0.01, 0.01]),
        rules, {}, funding,
    )

    assert result["completed_round_trips"] == 0


def test_pinball_prefers_exact_prediction():
    """実現値と一致するq予測のpinball lossが0になる。"""

    labels = np.asarray([-0.01, 0.0, 0.02])

    assert _pinball(labels, labels) == 0.0
    assert _pinball(labels, np.zeros(3)) > 0.0
