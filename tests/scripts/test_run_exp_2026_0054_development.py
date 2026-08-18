from decimal import Decimal

import numpy as np
import pandas as pd

from scripts.run_exp_2026_0054_development import (
    FEATURE_NAMES,
    InstrumentRule,
    MODEL_SELECTION_START,
    PREDICTION_THRESHOLD,
    Sample,
    _base_features,
    _read_before_cutoff,
    evaluate_predictions,
    fit_predict_xgboost,
    fit_ridge,
    predict_ridge,
    select_model,
)


def _price_frame(times: pd.DatetimeIndex, *, offset: float = 0.0) -> pd.DataFrame:
    """特徴量テスト用の連続1時間価格frameを作る。"""

    close = np.linspace(100.0 + offset, 110.0 + offset, len(times))
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.3,
            "close": close,
            "volume": np.linspace(10.0, 30.0, len(times)),
            "turnover": np.linspace(1000.0, 4000.0, len(times)),
        },
        index=times,
    )


def test_read_before_cutoff_does_not_return_sealed_values(tmp_path):
    """時刻走査後も封印境界以降の値をDataFrameへ返さない。"""

    path = tmp_path / "prices.csv"
    pd.DataFrame(
        {
            "event_time": pd.date_range("2025-12-31T22:00:00Z", periods=4, freq="1h"),
            "close": [1, 2, 999999, 999999],
        }
    ).to_csv(path, index=False)

    frame = _read_before_cutoff(path, pd.Timestamp("2026-01-01T00:00:00Z"))

    assert frame["close"].tolist() == [1, 2]
    assert (frame["event_time"] < pd.Timestamp("2026-01-01T00:00:00Z")).all()


def test_base_features_use_only_completed_bars():
    """判断境界以後の価格変更が24特徴量の基礎部分へ混入しない。"""

    decision = pd.Timestamp("2024-01-03T00:00:00Z")
    times = pd.date_range(decision - pd.Timedelta(hours=30), periods=37, freq="1h")
    trade = _price_frame(times)
    mark = _price_frame(times, offset=-0.1).drop(columns=["volume", "turnover"])
    index = _price_frame(times, offset=-0.2).drop(columns=["volume", "turnover"])
    premium = _price_frame(times, offset=-100.0).drop(columns=["volume", "turnover"])
    premium[["open", "high", "low", "close"]] = 0.0001
    btc = _price_frame(times, offset=100.0)
    funding = pd.DataFrame(
        {"funding_rate": [0.0001, 0.0002]},
        index=[decision - pd.Timedelta(hours=16), decision - pd.Timedelta(hours=8)],
    )
    first = _base_features(
        {"trade": trade, "mark_price": mark, "index_price": index, "premium_index": premium},
        funding,
        btc,
        decision,
    )
    trade.loc[trade.index >= decision, "close"] = 1_000_000.0
    second = _base_features(
        {"trade": trade, "mark_price": mark, "index_price": index, "premium_index": premium},
        funding,
        btc,
        decision,
    )

    assert len(first) == len(FEATURE_NAMES) - 2
    assert first == second


def _sample(index: int, target: float, symbol: str = "LINKUSDT") -> Sample:
    """modelテスト用の固定次元標本を作る。"""

    feature = index / 100.0
    return Sample(
        MODEL_SELECTION_START + pd.Timedelta(hours=6 * index),
        symbol,
        (feature, *([0.0] * (len(FEATURE_NAMES) - 1))),
        target,
        Decimal("10"),
        Decimal("10.1"),
    )


def test_ridge_is_deterministic_and_learns_relation():
    """closed-form Ridgeが固定入力で同じ単調予測を返す。"""

    train = [_sample(index, index / 1000.0) for index in range(100)]
    evaluation = [_sample(20, 0.0), _sample(80, 0.0)]

    first = predict_ridge(fit_ridge(train), evaluation)
    second = predict_ridge(fit_ridge(train), evaluation)

    assert np.array_equal(first, second)
    assert first[1] > first[0]


def test_xgboost_native_api_is_deterministic():
    """固定seed・単一threadのnative XGBoost予測が再現する。"""

    train = [_sample(index, math_target) for index, math_target in ((i, i / 1000.0) for i in range(100))]
    evaluation = [_sample(20, 0.0), _sample(80, 0.0)]

    first = fit_predict_xgboost(train, evaluation)
    second = fit_predict_xgboost(train, evaluation)

    assert np.array_equal(first, second)
    assert first[1] > first[0]


def test_select_model_rejects_nonpositive_and_cent_tie():
    """非正損益とcent丸め同額ではholdout modelを選ばない。"""

    assert select_model({"ridge": {"net_pnl": "0"}, "xgboost": {"net_pnl": "-1"}}) == (
        None,
        "both_models_nonpositive",
    )
    assert select_model({"ridge": {"net_pnl": "1.001"}, "xgboost": {"net_pnl": "1.004"}}) == (
        None,
        "rounded_net_pnl_tie",
    )


def test_evaluate_predictions_applies_costs_and_top1():
    """同時刻top1だけをlongし、固定費用を控除する。"""

    decision = MODEL_SELECTION_START
    symbols = ("LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT")
    samples = [
        Sample(decision, symbol, (0.0,) * len(FEATURE_NAMES), 0.01, Decimal("10"), Decimal("10.1"))
        for symbol in symbols
    ]
    predictions = np.asarray([PREDICTION_THRESHOLD + 0.01, 0.0, 0.0, 0.0])
    rules = {symbol: InstrumentRule(Decimal("0.1"), Decimal("0.1"), Decimal("5")) for symbol in symbols}
    price_frames = {
        symbol: {"mark_price": pd.DataFrame({"open": [Decimal("10")]}, index=[decision + pd.Timedelta(hours=1)])}
        for symbol in symbols
    }
    funding = {
        symbol: pd.DataFrame({"funding_rate": []}, index=pd.DatetimeIndex([], tz="UTC"))
        for symbol in symbols
    }

    result = evaluate_predictions(samples, predictions, rules, price_frames, funding)

    assert result["completed_round_trips"] == 1
    assert Decimal(str(result["net_pnl"])) < Decimal("1")
    assert result["trades"][0]["symbol"] == "LINKUSDT"
    assert Decimal(result["net_pnl"]) == (
        Decimal(result["gross_price_pnl"])
        + Decimal(result["funding_cash_flow"])
        - Decimal(result["fees"])
        - Decimal(result["spread_cost"])
        - Decimal(result["slippage_cost"])
    )
    assert result["trades"][0]["exit_time"] == "2025-01-01T06:00:00+00:00"
