import json
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
import yaml

from scripts.run_exp_2026_0055_source_gate import (
    FEATURE_NAMES,
    InstrumentRule,
    Sample,
    _sha256,
)
from scripts.run_exp_2026_0055_unseen_target import (
    SEALED_TARGET_SYMBOLS,
    TARGET_START,
    authorize_target,
    build_target_samples,
    decide_status,
    evaluate_target,
)


def _result(
    *, pnl: str, trades: int = 40, drawdown: str = "-0.02",
    symbol_trades: int = 10, positive_symbols: int = 4,
) -> dict[str, object]:
    """target判定テスト用の最小結果を作る。"""

    symbol_pnl = {
        symbol: "1" if index < positive_symbols else "-1"
        for index, symbol in enumerate(SEALED_TARGET_SYMBOLS)
    }
    return {
        "net_pnl": pnl, "completed_round_trips": trades, "max_drawdown": drawdown,
        "symbol_completed_round_trips": {symbol: symbol_trades for symbol in SEALED_TARGET_SYMBOLS},
        "symbol_net_pnl": symbol_pnl, "trades": [],
    }


def _bootstrap(lower: float = 0.0001) -> dict[str, dict[str, float]]:
    """3種類の固定bootstrap下限を作る。"""

    return {key: {"lower": lower, "upper": 0.001} for key in ("24h", "72h", "168h")}


def test_authorize_target_requires_xgboost_gate_and_hash(tmp_path):
    """固定XGBoostとsource成果物hashが一致する場合だけ開封を許す。"""

    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps({"selected_model": "xgboost", "target_opening_authorized": True}),
        encoding="utf-8",
    )
    registry = {
        "experiment_id": "EXP-2026-0055",
        "evaluation": {"source_gate_result": {
            "selected_model": "xgboost", "target_opening_authorized": True,
            "result_sha256": _sha256(summary),
        }},
        "execution_status": {"sealed_target_opened": False},
    }
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(yaml.safe_dump(registry), encoding="utf-8")

    assert authorize_target(registry_path, summary)["experiment_id"] == "EXP-2026-0055"

    summary.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        authorize_target(registry_path, summary)


def test_build_target_samples_requires_exact_universe():
    """target builderが4銘柄固定universe以外を拒否する。"""

    with pytest.raises(ValueError, match="exact sealed target"):
        build_target_samples({}, pd.DataFrame())


def test_evaluate_target_requires_probability_margin():
    """確率差0.03未満ではtarget取引を作らない。"""

    samples = [
        Sample(
            TARGET_START, symbol, (0.0,) * len(FEATURE_NAMES), 1, 0.01,
            Decimal("10"), Decimal("10.1"),
        )
        for symbol in SEALED_TARGET_SYMBOLS
    ]
    probabilities = np.asarray([0.50, 0.48, 0.30, 0.20])
    expected = np.asarray([0.01, 0.01, 0.0, 0.0])
    rules = {
        symbol: InstrumentRule(Decimal("0.1"), Decimal("0.1"), Decimal("5"))
        for symbol in SEALED_TARGET_SYMBOLS
    }
    funding = {
        symbol: pd.DataFrame({"funding_rate": []}, index=pd.DatetimeIndex([], tz="UTC"))
        for symbol in SEALED_TARGET_SYMBOLS
    }

    result = evaluate_target(samples, probabilities, expected, rules, {}, funding)

    assert result["completed_round_trips"] == 0


def test_decide_status_rejects_all_hard_gates():
    """targetの件数、分散、損益、stress、DD、欠測を棄却する。"""

    candidate = _result(
        pnl="-1", trades=39, drawdown="-0.10", symbol_trades=4, positive_symbols=2
    )

    status, reasons = decide_status(
        candidate, _result(pnl="1"), _result(pnl="0"), _bootstrap(),
        excluded_decision_times=1,
    )

    assert status == "REJECTED"
    assert reasons == [
        "target_net_pnl_nonpositive", "target_did_not_beat_momentum",
        "target_completed_round_trips_below_40", "target_symbol_round_trips_below_5",
        "target_positive_symbols_below_3", "target_stress_net_pnl_nonpositive",
        "target_max_drawdown_not_better_than_minus_10pct", "target_decision_times_excluded",
    ]


def test_decide_status_caps_bootstrap_at_inconclusive():
    """hard gate通過後もCI下限が非正ならINCONCLUSIVEにする。"""

    assert decide_status(
        _result(pnl="10"), _result(pnl="2"), _result(pnl="5"), _bootstrap(lower=-0.0001),
        excluded_decision_times=0,
    ) == ("INCONCLUSIVE", ["bootstrap_lower_bound_not_positive"])
