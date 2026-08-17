import json

import numpy as np
import pandas as pd
import yaml

from scripts.run_exp_2026_0054_development import (
    CONTEXT_SYMBOL,
    TRADED_SYMBOLS,
    _sha256,
    build_samples,
)
from scripts.run_exp_2026_0054_holdout import (
    _authorize_holdout,
    _circular_block_ci,
    _provisional_decision,
)


def _result(*, pnl: str, trades: int = 40, drawdown: str = "-0.02") -> dict[str, object]:
    """holdout判定テスト用の最小結果を作る。"""

    return {
        "net_pnl": pnl,
        "completed_round_trips": trades,
        "max_drawdown": drawdown,
        "trades": [],
    }


def _bootstrap(lower: float = 0.0001, upper: float = 0.001) -> dict[str, dict[str, float]]:
    """3種類の固定bootstrap CIを作る。"""

    return {
        key: {"lower": lower, "upper": upper}
        for key in ("24h", "72h", "168h")
    }


def test_build_samples_accepts_explicit_holdout_bounds():
    """明示したholdout判断境界とexit上限から4銘柄行を一組作る。"""

    decision = pd.Timestamp("2026-01-01T00:00:00Z")
    times = pd.date_range(decision - pd.Timedelta(hours=30), periods=37, freq="1h")
    prices = {}
    for position, symbol in enumerate((*TRADED_SYMBOLS, CONTEXT_SYMBOL)):
        close = np.linspace(100 + position, 110 + position, len(times))
        trade = pd.DataFrame(
            {
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.3,
                "close": close,
                "volume": np.linspace(10, 20, len(times)),
                "turnover": np.linspace(1000, 2000, len(times)),
            },
            index=times,
        )
        prices[symbol] = {
            "trade": trade,
            "mark_price": trade[["open", "high", "low", "close"]] * 0.999,
            "index_price": trade[["open", "high", "low", "close"]] * 0.998,
            "premium_index": trade[["open", "high", "low", "close"]] * 0 + 0.0001,
        }
    funding = {
        symbol: pd.DataFrame(
            {"funding_rate": [0.0001, 0.0002]},
            index=[decision - pd.Timedelta(hours=16), decision - pd.Timedelta(hours=8)],
        )
        for symbol in TRADED_SYMBOLS
    }

    samples, exclusions = build_samples(
        prices,
        funding,
        decision_start=decision,
        decision_end=decision + pd.Timedelta(hours=6),
        exit_end=decision + pd.Timedelta(hours=12),
    )

    assert len(samples) == 4
    assert exclusions == {}
    assert {sample.decision_time for sample in samples} == {decision}


def test_authorize_holdout_requires_fixed_ridge_and_hash(tmp_path):
    """Ridge選択とdevelopment成果物hashが一致する場合だけ開封を許す。"""

    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps({"selected_model": "ridge", "holdout_authorized": True}),
        encoding="utf-8",
    )
    registry = {
        "experiment_id": "EXP-2026-0054",
        "evaluation": {
            "development_result": {
                "selected_model": "ridge",
                "holdout_authorized_by_preregistered_gate": True,
                "sealed_holdout_opened": False,
                "result_sha256": _sha256(summary),
            }
        },
        "execution_status": {"sealed_holdout_opened": False},
    }
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(yaml.safe_dump(registry), encoding="utf-8")

    assert _authorize_holdout(registry_path, summary)["experiment_id"] == "EXP-2026-0054"

    summary.write_text("{}", encoding="utf-8")
    try:
        _authorize_holdout(registry_path, summary)
    except ValueError as error:
        assert str(error) == "development result hash mismatch"
    else:
        raise AssertionError("改変されたdevelopment成果物を受理した")


def test_circular_block_bootstrap_is_deterministic():
    """固定seedのblock bootstrapが同じCIを返す。"""

    differences = np.asarray([0.001, -0.0002, 0.0008, 0.0004] * 20)

    first = _circular_block_ci(differences, 3, repetitions=500, seed=0)
    second = _circular_block_ci(differences, 3, repetitions=500, seed=0)

    assert first == second
    assert first[0] > 0


def test_provisional_decision_rejects_each_hard_gate():
    """PnL、baseline、件数、stress、DD、欠測を独立理由として棄却する。"""

    status, reasons = _provisional_decision(
        _result(pnl="-1", trades=29, drawdown="-0.10"),
        _result(pnl="1"),
        _result(pnl="0"),
        _bootstrap(),
        excluded_decision_times=1,
    )

    assert status == "REJECTED"
    assert reasons == [
        "holdout_net_pnl_nonpositive",
        "holdout_did_not_beat_momentum",
        "holdout_completed_round_trips_below_30",
        "stress_holdout_net_pnl_nonpositive",
        "holdout_max_drawdown_not_better_than_minus_10pct",
        "holdout_decision_times_excluded",
    ]


def test_provisional_decision_caps_at_inconclusive():
    """hard gate通過後もCIが0を含めばINCONCLUSIVEを上限にする。"""

    status, reasons = _provisional_decision(
        _result(pnl="10"),
        _result(pnl="2"),
        _result(pnl="5"),
        _bootstrap(lower=-0.0001, upper=0.001),
        excluded_decision_times=0,
    )

    assert status == "INCONCLUSIVE"
    assert reasons == ["bootstrap_lower_bound_not_positive"]


def test_provisional_decision_does_not_pass_negative_ci():
    """CI全体が負でもlower bound非正なのでPASSEDへ進めない。"""

    status, reasons = _provisional_decision(
        _result(pnl="10"),
        _result(pnl="2"),
        _result(pnl="5"),
        _bootstrap(lower=-0.002, upper=-0.001),
        excluded_decision_times=0,
    )

    assert status == "INCONCLUSIVE"
    assert reasons == ["bootstrap_lower_bound_not_positive"]


def test_provisional_decision_passes_all_fixed_conditions():
    """全hard gateと3 CIを通過した場合だけforward候補にする。"""

    assert _provisional_decision(
        _result(pnl="10"),
        _result(pnl="2"),
        _result(pnl="5"),
        _bootstrap(),
        excluded_decision_times=0,
    ) == ("PASSED_FORWARD_TEST", [])
