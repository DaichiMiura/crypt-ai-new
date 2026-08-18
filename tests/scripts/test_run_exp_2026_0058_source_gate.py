from decimal import Decimal

import numpy as np
import pandas as pd

from scripts.run_exp_2026_0058_source_gate import (
    ABLATION_FEATURE_INDICES,
    FEATURE_NAMES,
    WARMUP_END,
    CrowdingSample,
    _matrix_values,
    build_crowding_samples,
    funding_features,
    premium_feature_frame,
)
from scripts.run_exp_2026_0056_source_gate import SOURCE_SYMBOLS, Sample


def test_funding_features_exclude_event_at_decision_time():
    """判断時刻と同時刻のFundingを特徴量へ含めない。"""

    index = pd.date_range("2022-01-01T00:00:00Z", periods=11, freq="8h")
    funding = pd.DataFrame({"funding_rate": np.arange(11, dtype=float)}, index=index)
    decision_time = index[9]

    values = funding_features(funding, decision_time)

    assert values == (8.0, 7.0, 4.0, 1.0)


def test_premium_features_do_not_change_when_future_changes():
    """判断後のpremium変更がpoint-in-time特徴量を変えない。"""

    index = pd.date_range("2022-01-01T00:00:00Z", periods=3000, freq="15min")
    close = 0.001 * np.sin(np.arange(len(index), dtype=float) / 31.0)
    frame = pd.DataFrame({"close": close}, index=index)
    target = index[2890]
    original = premium_feature_frame(frame).loc[target].to_numpy()
    changed = frame.copy()
    changed.loc[changed.index > target, "close"] += 1.0

    future_changed = premium_feature_frame(changed).loc[target].to_numpy()

    assert np.array_equal(original, future_changed)
    assert np.isfinite(original).all()


def test_constant_premium_uses_zero_z_score():
    """30日premium分散が0ならz-scoreを0へ固定する。"""

    index = pd.date_range("2022-01-01T00:00:00Z", periods=2880, freq="15min")
    frame = pd.DataFrame({"close": np.full(len(index), 0.001)}, index=index)

    features = premium_feature_frame(frame)

    assert features.iloc[-1]["premium_z_30d"] == 0.0


def test_ablation_matrix_excludes_premium_and_funding_features():
    """価格ablationが後半17特徴量を参照しない。"""

    base = tuple(float(index) for index in range(len(FEATURE_NAMES)))
    changed = (*base[:14], *([999.0] * 17))
    timestamp = pd.Timestamp("2025-01-01T00:00:00Z")
    samples = [
        CrowdingSample(timestamp, "LINKUSDT", base, 0.01, Decimal("10"), Decimal("10.1")),
        CrowdingSample(timestamp, "LINKUSDT", changed, 0.01, Decimal("10"), Decimal("10.1")),
    ]

    matrix = _matrix_values(samples, ABLATION_FEATURE_INDICES)

    assert matrix.shape == (2, 14)
    assert np.array_equal(matrix[0], matrix[1])


def test_flat_cross_section_uses_neutral_rank_without_exclusion():
    """全premium同値なら中立rankで標本を保持する。"""

    completed = WARMUP_END - pd.Timedelta(minutes=15)
    premium_index = pd.date_range(end=completed, periods=3000, freq="15min")
    funding_index = pd.date_range(end=WARMUP_END - pd.Timedelta(hours=8), periods=9, freq="8h")
    premium_frames = {
        symbol: pd.DataFrame({"close": np.zeros(len(premium_index))}, index=premium_index)
        for symbol in SOURCE_SYMBOLS
    }
    funding_frames = {
        symbol: pd.DataFrame({"funding_rate": np.zeros(9)}, index=funding_index)
        for symbol in SOURCE_SYMBOLS
    }
    base_samples = [
        Sample(
            WARMUP_END, symbol, tuple([0.0] * 26), 0.0,
            Decimal("10"), Decimal("10"),
        )
        for symbol in SOURCE_SYMBOLS
    ]

    samples, exclusions = build_crowding_samples(
        base_samples, premium_frames, funding_frames
    )

    assert exclusions == {}
    assert len(samples) == len(SOURCE_SYMBOLS)
    assert all(sample.features[24:27] == (0.5, 0.0, 0.0) for sample in samples)
