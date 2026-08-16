"""現物ロングと無期限先物ショートのベーシスシグナルを提供する。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd


@dataclass(frozen=True)
class BasisSignalConfig:
    """ベーシス収束シグナルの固定条件。"""

    entry_basis: Decimal = Decimal("0.005")
    exit_basis: Decimal = Decimal("0.001")
    max_holding_bars: int = 360

    def __post_init__(self) -> None:
        """閾値と最大保有期間の整合性を検査する。

        Raises:
            ValueError: 閾値が有限でない、順序が不正、または期間が非正の場合。
        """

        for name in ("entry_basis", "exit_basis"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.entry_basis <= self.exit_basis:
            raise ValueError("entry_basis must be greater than exit_basis")
        if (
            isinstance(self.max_holding_bars, bool)
            or not isinstance(self.max_holding_bars, int)
            or self.max_holding_bars <= 0
        ):
            raise ValueError("max_holding_bars must be a positive integer")


def prepare_basis_signals(
    frame: pd.DataFrame,
    config: BasisSignalConfig,
) -> pd.DataFrame:
    """現物に対する先物プレミアムの収束シグナルを作る。

    `perp_mark_close / spot_close - 1`がentry閾値以上になった確定足でペアを
    建て、exit閾値以下への収束、または最大保有バー数到達で決済する。確定した
    状態は次のバーの始値へ遅延するため、現在足の終値で未来の約定を発生させない。

    Args:
        frame: `event_time`、`spot_close`、`perp_mark_close`を含む時系列DataFrame。
        config: entry・exit閾値と最大保有期間を固定した設定。

    Returns:
        basis、entry/exitフラグ、ペア状態、`desired_pair_position`を追加したDataFrame。

    Raises:
        ValueError: 必須列、時刻、価格、またはパラメータが不正な場合。
    """

    required = {"event_time", "spot_close", "perp_mark_close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing basis columns: {sorted(missing)}")
    result = frame.copy()
    result["event_time"] = pd.to_datetime(
        result["event_time"], utc=True, errors="coerce"
    )
    if (
        result["event_time"].isna().any()
        or result["event_time"].duplicated().any()
        or not result["event_time"].is_monotonic_increasing
    ):
        raise ValueError("event_time must be unique and sorted")
    for column in ("spot_close", "perp_mark_close"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
        if result[column].isna().any() or not result[column].gt(0).all():
            raise ValueError(f"{column} must be positive")

    result["basis"] = result["perp_mark_close"] / result["spot_close"] - 1
    active = 0
    held_bars = 0
    pair_positions: list[int] = []
    entry_signals: list[bool] = []
    exit_signals: list[bool] = []
    holding_bars: list[int] = []
    for basis in result["basis"]:
        entered = False
        exited = False
        if active:
            held_bars += 1
            if basis <= float(config.exit_basis) or held_bars >= config.max_holding_bars:
                active = 0
                exited = True
                held_bars = 0
        elif basis >= float(config.entry_basis):
            active = 1
            entered = True
            held_bars = 0
        pair_positions.append(active)
        entry_signals.append(entered)
        exit_signals.append(exited)
        holding_bars.append(held_bars)

    result["basis_entry_signal"] = entry_signals
    result["basis_exit_signal"] = exit_signals
    result["basis_holding_bars"] = holding_bars
    result["pair_signal_position"] = pair_positions
    result["desired_pair_position"] = (
        result["pair_signal_position"].shift(1, fill_value=0).astype(int)
    )
    return result
