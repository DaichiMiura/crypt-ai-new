"""複数銘柄の資金配分を決定論的に制御するための層。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml


def _decimal(value: object, name: str) -> Decimal:
    """値を有限なDecimalへ変換する。

    Args:
        value: 数値または文字列化できる数値。
        name: エラーに表示する設定名。

    Returns:
        変換後のDecimal。

    Raises:
        ValueError: 数値へ変換できない、または有限でない場合。
    """

    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a decimal") from error
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _symbols(value: object) -> tuple[str, ...]:
    """資産一覧を重複のない文字列タプルへ変換する。

    Args:
        value: シンボルの反復可能オブジェクト。

    Returns:
        登録順を保ったシンボルタプル。

    Raises:
        ValueError: 空、文字列、重複、空要素が含まれる場合。
    """

    if (
        isinstance(value, (str, bytes, Mapping))
        or not isinstance(value, Iterable)
    ):
        raise ValueError("allowed_symbols must be an iterable")
    result = tuple(str(symbol).strip() for symbol in value)
    if not result or any(not symbol for symbol in result):
        raise ValueError("allowed_symbols must contain at least one non-empty symbol")
    if len(set(result)) != len(result):
        raise ValueError("allowed_symbols must not contain duplicates")
    return result


def _positions(value: Mapping[str, object], name: str) -> dict[str, Decimal]:
    """ポジション元本マップを検証してコピーする。

    Args:
        value: シンボルから元本へのマッピング。
        name: エラーに表示するポジション側名。

    Returns:
        Decimalへ変換したポジションの可変コピー。

    Raises:
        ValueError: シンボルまたは元本が不正な場合。
    """

    result: dict[str, Decimal] = {}
    for symbol, notional in value.items():
        normalized_symbol = str(symbol).strip()
        if not normalized_symbol:
            raise ValueError(f"{name} contains an empty symbol")
        amount = _decimal(notional, f"{name}[{normalized_symbol}]")
        if amount < 0:
            raise ValueError(f"{name}[{normalized_symbol}] must be non-negative")
        if amount > 0:
            result[normalized_symbol] = amount
    return result


def _positive_integer(value: object, name: str) -> int:
    """正の整数設定を検証して返す。

    Args:
        value: 整数または整数文字列。
        name: エラーに表示する設定名。

    Returns:
        検証済みの整数。

    Raises:
        ValueError: 整数でない、または正でない場合。
    """

    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if str(value).strip() != str(result) and not isinstance(value, int):
        raise ValueError(f"{name} must be a positive integer")
    if result <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return result


@dataclass(frozen=True)
class AllocationConfig:
    """資産、ロット、スリーブ上限を固定した配分設定。

    Attributes:
        currency: 元本を表示する通貨。
        allowed_symbols: 配分対象として登録した資産一覧。
        initial_equity: 配分開始時の口座equity。
        reserve_cash: 常に残す予備資金。新規配分には使わない。
        max_long_gross_notional: ロング全体の元本上限。
        max_short_gross_notional: ショート全体の元本上限。
        max_total_gross_notional: ロングとショートを合わせた元本上限。
        per_symbol_max_notional: 1銘柄あたりのロング・ショート合算元本上限。
        lot_notional: 1ロットの固定想定元本。
        max_concurrent_long_positions: 同時保有できるロング銘柄数。
        max_concurrent_short_positions: 同時保有できるショート銘柄数。

    Note:
        ここで扱う元本は目標gross notionalであり、取引所の証拠金やレバレッジを
        自動的に計算するものではない。注文前には別のリスクエンジンも通す。
    """

    currency: str
    allowed_symbols: tuple[str, ...]
    initial_equity: Decimal
    reserve_cash: Decimal
    max_long_gross_notional: Decimal
    max_short_gross_notional: Decimal
    max_total_gross_notional: Decimal
    per_symbol_max_notional: Decimal
    lot_notional: Decimal
    max_concurrent_long_positions: int
    max_concurrent_short_positions: int

    def __post_init__(self) -> None:
        """設定値をDecimalへ正規化し、配分上限の整合性を検査する。"""

        currency = str(self.currency).strip()
        if not currency:
            raise ValueError("currency must be non-empty")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "allowed_symbols", _symbols(self.allowed_symbols))

        decimal_fields = (
            "initial_equity",
            "reserve_cash",
            "max_long_gross_notional",
            "max_short_gross_notional",
            "max_total_gross_notional",
            "per_symbol_max_notional",
            "lot_notional",
        )
        for name in decimal_fields:
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if self.reserve_cash < 0 or self.reserve_cash > self.initial_equity:
            raise ValueError("reserve_cash must be between zero and initial_equity")
        for name in ("max_long_gross_notional", "max_short_gross_notional"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in (
            "max_total_gross_notional",
            "per_symbol_max_notional",
            "lot_notional",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_long_gross_notional > self.max_total_gross_notional:
            raise ValueError("max_long_gross_notional cannot exceed max_total_gross_notional")
        if self.max_short_gross_notional > self.max_total_gross_notional:
            raise ValueError("max_short_gross_notional cannot exceed max_total_gross_notional")
        if self.per_symbol_max_notional > self.max_total_gross_notional:
            raise ValueError("per_symbol_max_notional cannot exceed max_total_gross_notional")
        for name in (
            "max_concurrent_long_positions",
            "max_concurrent_short_positions",
        ):
            object.__setattr__(self, name, _positive_integer(getattr(self, name), name))

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "AllocationConfig":
        """YAML相当のマッピングから配分設定を作る。

        Args:
            mapping: 設定マッピング。`allocation`で一段ネストしてもよい。

        Returns:
            検証済みの配分設定。

        Raises:
            ValueError: 必須項目がない、または設定値が不正な場合。
        """

        if not isinstance(mapping, Mapping):
            raise ValueError("allocation config must be a mapping")
        values = mapping.get("allocation", mapping)
        if not isinstance(values, Mapping):
            raise ValueError("allocation section must be a mapping")

        def required(name: str, *aliases: str) -> object:
            """必須設定を別名を含めて取り出す。"""

            for candidate in (name, *aliases):
                if candidate in values:
                    return values[candidate]
            raise ValueError(f"missing allocation setting: {name}")

        symbols = values.get("allowed_symbols", values.get("assets"))
        if symbols is None:
            raise ValueError("missing allocation setting: allowed_symbols")
        return cls(
            currency=str(values.get("currency", "JPY")),
            allowed_symbols=_symbols(symbols),
            initial_equity=_decimal(
                required("initial_equity", "initial_cash"), "initial_equity"
            ),
            reserve_cash=_decimal(required("reserve_cash"), "reserve_cash"),
            max_long_gross_notional=_decimal(
                required("max_long_gross_notional"), "max_long_gross_notional"
            ),
            max_short_gross_notional=_decimal(
                required("max_short_gross_notional"), "max_short_gross_notional"
            ),
            max_total_gross_notional=_decimal(
                required("max_total_gross_notional"), "max_total_gross_notional"
            ),
            per_symbol_max_notional=_decimal(
                required("per_symbol_max_notional"), "per_symbol_max_notional"
            ),
            lot_notional=_decimal(required("lot_notional"), "lot_notional"),
            max_concurrent_long_positions=_positive_integer(
                required("max_concurrent_long_positions"),
                "max_concurrent_long_positions",
            ),
            max_concurrent_short_positions=_positive_integer(
                required("max_concurrent_short_positions"),
                "max_concurrent_short_positions",
            ),
        )


def load_allocation_config(path: Path) -> AllocationConfig:
    """YAMLファイルから配分設定を読み込む。

    Args:
        path: 配分設定YAMLのパス。

    Returns:
        検証済みの配分設定。

    Raises:
        ValueError: YAMLのトップレベルがマッピングでない場合。
    """

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("allocation YAML must contain a mapping")
    return AllocationConfig.from_mapping(raw)


@dataclass
class AllocationState:
    """現在のequityと銘柄別gross元本を保持する配分状態。

    Attributes:
        equity: 現在の口座equity。損益反映後の値を渡す。
        long_positions: 銘柄ごとのロング元本。
        short_positions: 銘柄ごとのショート元本。
    """

    equity: Decimal
    long_positions: dict[str, Decimal] = field(default_factory=dict)
    short_positions: dict[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """equityと既存ポジションを検証してDecimalへ正規化する。"""

        self.equity = _decimal(self.equity, "equity")
        if self.equity <= 0:
            raise ValueError("equity must be positive")
        self.long_positions = _positions(self.long_positions, "long_positions")
        self.short_positions = _positions(self.short_positions, "short_positions")

    @property
    def long_gross_notional(self) -> Decimal:
        """現在のロング合計元本を返す。"""

        return sum(self.long_positions.values(), Decimal("0"))

    @property
    def short_gross_notional(self) -> Decimal:
        """現在のショート合計元本を返す。"""

        return sum(self.short_positions.values(), Decimal("0"))

    @property
    def total_gross_notional(self) -> Decimal:
        """ロングとショートを合わせた合計元本を返す。"""

        return self.long_gross_notional + self.short_gross_notional

    def positions_for(self, side: str) -> dict[str, Decimal]:
        """指定した売買側のポジションマップを返す。

        Args:
            side: `long`または`short`。

        Returns:
            内部のポジションマップ。

        Raises:
            ValueError: 不明なsideの場合。
        """

        if side == "long":
            return self.long_positions
        if side == "short":
            return self.short_positions
        raise ValueError("side must be 'long' or 'short'")

    def open_position(self, side: str, symbol: str, notional: Decimal) -> None:
        """承認済みポジションを状態へ追加する。

        Args:
            side: `long`または`short`。
            symbol: 資産シンボル。
            notional: 追加する元本。

        Raises:
            ValueError: 元本が正でない、またはsideが不明な場合。
        """

        amount = _decimal(notional, "notional")
        if amount <= 0:
            raise ValueError("notional must be positive")
        normalized_symbol = str(symbol).strip()
        if not normalized_symbol:
            raise ValueError("symbol must be non-empty")
        positions = self.positions_for(side)
        positions[normalized_symbol] = positions.get(normalized_symbol, Decimal("0")) + amount

    def close_position(self, side: str, symbol: str, notional: Decimal) -> None:
        """決済した元本を状態から解放する。

        Args:
            side: `long`または`short`。
            symbol: 資産シンボル。
            notional: 解放する元本。

        Raises:
            ValueError: 未保有、過大な決済、または元本が正でない場合。
        """

        amount = _decimal(notional, "notional")
        if amount <= 0:
            raise ValueError("notional must be positive")
        normalized_symbol = str(symbol).strip()
        positions = self.positions_for(side)
        current = positions.get(normalized_symbol, Decimal("0"))
        if current < amount:
            raise ValueError("close notional exceeds open position")
        remaining = current - amount
        if remaining:
            positions[normalized_symbol] = remaining
        else:
            positions.pop(normalized_symbol, None)


@dataclass(frozen=True)
class AllocationDecision:
    """新規配分の可否と機械可読な理由。

    Attributes:
        accepted: 新規配分を承認したか。
        reason: `accepted`または拒否理由コード。
        side: 判定した売買側。
        symbol: 判定した資産シンボル。
        lot_count: 要求された固定ロット数。
        requested_notional: 要求元本。
        approved_notional: 承認元本。拒否時はゼロ。
    """

    accepted: bool
    reason: str
    side: str
    symbol: str
    lot_count: int
    requested_notional: Decimal
    approved_notional: Decimal


class PortfolioAllocator:
    """設定済みの資産・元本・同時保有上限で新規配分を判定する。

    Attributes:
        config: このアロケータが使用する不変の配分設定。
    """

    def __init__(self, config: AllocationConfig) -> None:
        """配分設定を固定する。

        Args:
            config: 検証済みの配分設定。
        """

        self.config = config

    def evaluate_order(
        self,
        state: AllocationState,
        *,
        side: str,
        symbol: str,
        lot_count: int = 1,
    ) -> AllocationDecision:
        """新規ロットがすべての配分上限を満たすか判定する。

        Args:
            state: 現在の口座配分状態。
            side: `long`または`short`。
            symbol: 事前登録済みの資産シンボル。
            lot_count: 追加する固定ロット数。

        Returns:
            承認可否、元本、拒否理由を含む判定結果。
        """

        normalized_symbol = str(symbol).strip()
        requested = Decimal("0")
        if isinstance(lot_count, bool) or not isinstance(lot_count, int) or lot_count <= 0:
            return AllocationDecision(
                False, "invalid_lot_count", side, normalized_symbol, lot_count, requested, requested
            )
        requested = self.config.lot_notional * lot_count
        if side not in {"long", "short"}:
            return AllocationDecision(
                False, "invalid_side", side, normalized_symbol, lot_count, requested, Decimal("0")
            )
        if normalized_symbol not in self.config.allowed_symbols:
            return AllocationDecision(
                False, "unknown_symbol", side, normalized_symbol, lot_count, requested, Decimal("0")
            )

        positions = state.positions_for(side)
        same_symbol = positions.get(normalized_symbol, Decimal("0"))
        other_side = state.short_positions if side == "long" else state.long_positions
        same_symbol_total = same_symbol + other_side.get(normalized_symbol, Decimal("0"))
        if (
            normalized_symbol not in positions
            and len(positions) >= self._max_concurrent(side)
        ):
            return self._rejected(side, normalized_symbol, lot_count, requested, "max_concurrent_positions")
        if same_symbol_total + requested > self.config.per_symbol_max_notional:
            return self._rejected(side, normalized_symbol, lot_count, requested, "per_symbol_cap")

        side_total = (
            state.long_gross_notional if side == "long" else state.short_gross_notional
        )
        side_cap = (
            self.config.max_long_gross_notional
            if side == "long"
            else self.config.max_short_gross_notional
        )
        if side_total + requested > side_cap:
            return self._rejected(
                side, normalized_symbol, lot_count, requested, f"{side}_cap"
            )
        if state.total_gross_notional + requested > self.config.max_total_gross_notional:
            return self._rejected(side, normalized_symbol, lot_count, requested, "total_cap")
        available_after_reserve = state.equity - self.config.reserve_cash
        if available_after_reserve < 0 or state.total_gross_notional + requested > available_after_reserve:
            return self._rejected(side, normalized_symbol, lot_count, requested, "reserve_cash")
        return AllocationDecision(
            True,
            "accepted",
            side,
            normalized_symbol,
            lot_count,
            requested,
            requested,
        )

    def try_open(
        self,
        state: AllocationState,
        *,
        side: str,
        symbol: str,
        lot_count: int = 1,
    ) -> AllocationDecision:
        """配分判定を行い、承認時だけ状態へロットを追加する。

        Args:
            state: 現在の口座配分状態。
            side: `long`または`short`。
            symbol: 事前登録済みの資産シンボル。
            lot_count: 追加する固定ロット数。

        Returns:
            状態更新後の配分判定結果。
        """

        decision = self.evaluate_order(
            state,
            side=side,
            symbol=symbol,
            lot_count=lot_count,
        )
        if decision.accepted:
            state.open_position(decision.side, decision.symbol, decision.approved_notional)
        return decision

    def release(self, state: AllocationState, *, side: str, symbol: str, lot_count: int = 1) -> None:
        """決済した固定ロット分を配分状態から解放する。

        Args:
            state: 現在の口座配分状態。
            side: `long`または`short`。
            symbol: 決済する資産シンボル。
            lot_count: 解放する固定ロット数。

        Raises:
            ValueError: ロット数または保有元本が不正な場合。
        """

        if isinstance(lot_count, bool) or not isinstance(lot_count, int) or lot_count <= 0:
            raise ValueError("lot_count must be a positive integer")
        state.close_position(side, str(symbol).strip(), self.config.lot_notional * lot_count)

    def _max_concurrent(self, side: str) -> int:
        """sideに対応する同時保有上限を返す。

        Args:
            side: `long`または`short`。

        Returns:
            指定sideの同時保有銘柄数上限。
        """

        return (
            self.config.max_concurrent_long_positions
            if side == "long"
            else self.config.max_concurrent_short_positions
        )

    @staticmethod
    def _rejected(
        side: str,
        symbol: str,
        lot_count: int,
        requested: Decimal,
        reason: str,
    ) -> AllocationDecision:
        """拒否理由を持つ判定結果を作る。

        Args:
            side: 判定した売買側。
            symbol: 判定した資産シンボル。
            lot_count: 要求された固定ロット数。
            requested: 要求元本。
            reason: 機械可読な拒否理由コード。

        Returns:
            承認元本をゼロにした拒否結果。
        """

        return AllocationDecision(
            False, reason, side, symbol, lot_count, requested, Decimal("0")
        )
