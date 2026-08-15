"""EXP-2026-0015のVOID式ショートに共通する基本方針を提供する。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import StrEnum

import pandas as pd


VOID_SHORT_SYMBOLS = frozenset(
    {"LINKUSDT", "UNIUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT", "AAVEUSDT"}
)
VOID_SHORT_ATR_BARS = 14
VOID_SHORT_RALLY_ATR = 2.0
VOID_SHORT_PULLBACK_ATR = 1.0
VOID_SHORT_REBOUND_ATR = 0.5
VOID_SHORT_FIBONACCI_RATIOS = (
    Decimal("0.236"),
    Decimal("0.382"),
    Decimal("0.618"),
    Decimal("1.0"),
)
VOID_SHORT_SIZING_REFERENCE_LEVERAGE = Decimal("20")
VOID_SHORT_LOT_DIVISOR = Decimal("100")
VOID_SHORT_EXECUTION_LEVERAGE = Decimal("1")


class VoidShortSetupState(StrEnum):
    """VOID式ショートのエントリー準備状態。"""

    NO_TRADE = "NO_TRADE"
    WAIT_RALLY = "WAIT_RALLY"
    WAIT_PULLBACK = "WAIT_PULLBACK"
    WAIT_REBOUND = "WAIT_REBOUND"
    READY = "READY"


@dataclass(frozen=True)
class VoidShortLimitLevel:
    """数量決定前のフィボナッチ売り指値候補。

    Attributes:
        ratio: フィボナッチ・エクステンション比率。
        raw_price: tick size適用前の理論価格。
        limit_price: tick sizeへ切り上げた売り指値価格。
    """

    ratio: Decimal
    raw_price: Decimal
    limit_price: Decimal


@dataclass(frozen=True)
class VoidShortSizedLimit:
    """1ロットの数量を割り当てた売り指値候補。

    Attributes:
        ratio: フィボナッチ・エクステンション比率。
        limit_price: tick size適用済みの売り指値価格。
        quantity: qty stepへ切り下げた注文数量。
        notional: ``limit_price * quantity``で求めた想定元本。
        lot_count: 初期実験で固定するロット数。
    """

    ratio: Decimal
    limit_price: Decimal
    quantity: Decimal
    notional: Decimal
    lot_count: int = 1


@dataclass(frozen=True)
class VoidShortCorePolicy:
    """VOID式ショートの変更不能な初期基本方針。

    Attributes:
        bar_interval: シグナル判定に使う確定足の間隔。
        max_holding_bars: 時間切れ決済までに保有できる最大バー数。
        direction: 許可する建玉方向。
        entry_order_type: 許可する新規注文種別。
        allow_same_bar_exit: 約定バー内の通常決済を許可するか。
        default_no_trade: 判定不能時に新規取引を拒否するか。
        live_trading_enabled: 実注文を許可するか。
        allowed_symbols: 新規エントリーを検討できる固定銘柄集合。
    """

    bar_interval: timedelta = timedelta(hours=2)
    max_holding_bars: int = 168
    direction: str = "short"
    entry_order_type: str = "limit"
    allow_same_bar_exit: bool = False
    default_no_trade: bool = True
    live_trading_enabled: bool = False
    allowed_symbols: frozenset[str] = VOID_SHORT_SYMBOLS

    def __post_init__(self) -> None:
        """基本方針を緩和する設定を拒否する。

        Raises:
            ValueError: EXP-2026-0015で承認していない値が指定された場合。
        """

        if self.bar_interval != timedelta(hours=2):
            raise ValueError("bar_interval must be exactly 2 hours")
        if self.max_holding_bars != 168:
            raise ValueError("max_holding_bars must be exactly 168")
        if self.direction != "short":
            raise ValueError("direction must be short")
        if self.entry_order_type != "limit":
            raise ValueError("entry_order_type must be limit")
        if self.allow_same_bar_exit:
            raise ValueError("same-bar exit must remain disabled")
        if not self.default_no_trade:
            raise ValueError("unknown state must default to no trade")
        if self.live_trading_enabled:
            raise ValueError("live trading must remain disabled")
        if self.allowed_symbols != VOID_SHORT_SYMBOLS:
            raise ValueError("allowed_symbols must match the preregistered universe")

    @property
    def max_holding_duration(self) -> timedelta:
        """最大保有期間を返す。

        Returns:
            2時間足168本に相当する14日間。
        """

        return self.bar_interval * self.max_holding_bars

    def permits_entry(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        signal_bar_closed: bool,
        setup_confirmed: bool,
        state_known: bool,
    ) -> bool:
        """基本方針だけを使って新規エントリー可否を判定する。

        後続段階で実装する銘柄、トレンド、リバウンド条件は
        ``setup_confirmed``へ集約し、本関数は不明状態を常に拒否する。

        Args:
            symbol: 事前登録したZOOMEX symbol。
            side: 注文方向。``short``だけを許可する。
            order_type: 新規注文種別。``limit``だけを許可する。
            signal_bar_closed: シグナルに使ったバーが確定済みか。
            setup_confirmed: 後続の全セットアップ条件が成立したか。
            state_known: データと判定状態が正常に確定しているか。

        Returns:
            すべての基本条件を満たす場合だけ``True``。
        """

        return (
            state_known
            and signal_bar_closed
            and setup_confirmed
            and symbol in self.allowed_symbols
            and side == self.direction
            and order_type == self.entry_order_type
        )

    def permits_symbol(self, symbol: str) -> bool:
        """固定ユニバースに含まれる銘柄か返す。

        大文字・空白除去などの暗黙な補正は行わず、未知の表記は取引拒否とする。

        Args:
            symbol: 判定するZOOMEX symbol。

        Returns:
            事前登録した6銘柄の完全一致なら``True``。
        """

        return symbol in self.allowed_symbols

    def can_evaluate_normal_exit(self, held_bars: int) -> bool:
        """通常の利確・損切り判定を開始できるか返す。

        Args:
            held_bars: 約定後に完了した2時間足の本数。

        Returns:
            約定バーより後なら``True``。

        Raises:
            ValueError: 保有バー数が負の場合。
        """

        if held_bars < 0:
            raise ValueError("held_bars must be non-negative")
        return held_bars >= 1

    def time_exit_due(self, held_bars: int) -> bool:
        """14日の時間切れ決済が必要か返す。

        Args:
            held_bars: 約定後に完了した2時間足の本数。

        Returns:
            168本以上を保有していれば``True``。

        Raises:
            ValueError: 保有バー数が負の場合。
        """

        if held_bars < 0:
            raise ValueError("held_bars must be non-negative")
        return held_bars >= self.max_holding_bars


VOID_SHORT_CORE_POLICY = VoidShortCorePolicy()


def prepare_void_short_trend_regime(frame: pd.DataFrame) -> pd.DataFrame:
    """2時間足のSMA200・SMA400から下落トレンド状態を作る。

    現在バーの終値で確定した状態は次のバーからだけ利用可能にする。SMA400を
    計算できない期間は状態不明として新規エントリーを許可しない。

    Args:
        frame: ``event_time``、``close``、``is_interpolated``を持つ2時間足。

    Returns:
        SMA、確定時点の状態、次バーで利用できる状態を追加したDataFrame。

    Raises:
        ValueError: 必須列不足、空、時刻不正、欠損、重複、補間行、または
            非正の終値がある場合。
    """

    required_columns = {"event_time", "close", "is_interpolated"}
    missing_columns = required_columns - set(frame.columns)
    if missing_columns:
        raise ValueError(f"missing columns: {sorted(missing_columns)}")
    if frame.empty:
        raise ValueError("frame must not be empty")

    result = frame.copy()
    event_time = pd.to_datetime(result["event_time"], utc=True, errors="coerce")
    if event_time.isna().any():
        raise ValueError("event_time contains invalid values")
    if event_time.duplicated().any():
        raise ValueError("event_time contains duplicates")
    if not event_time.is_monotonic_increasing:
        raise ValueError("event_time must be sorted")
    if len(event_time) > 1 and not event_time.diff().dropna().eq(
        VOID_SHORT_CORE_POLICY.bar_interval
    ).all():
        raise ValueError("event_time must be continuous 2-hour bars")
    result["event_time"] = event_time

    interpolated = result["is_interpolated"]
    if not interpolated.map(lambda value: isinstance(value, bool)).all():
        raise ValueError("is_interpolated must contain bool values")
    if interpolated.any():
        raise ValueError("interpolated rows are not allowed")

    close = pd.to_numeric(result["close"], errors="coerce")
    if close.isna().any() or not close.gt(0).all():
        raise ValueError("close must contain positive numeric values")
    result["close"] = close

    result["sma200"] = close.rolling(window=200, min_periods=200).mean()
    result["sma400"] = close.rolling(window=400, min_periods=400).mean()
    result["trend_state_known_at_close"] = result["sma400"].notna()
    result["downtrend_regime_at_close"] = (
        result["trend_state_known_at_close"] & (result["sma200"] < result["sma400"])
    )
    result["trend_state_known_for_bar"] = result[
        "trend_state_known_at_close"
    ].shift(1, fill_value=False)
    result["downtrend_regime_for_bar"] = result[
        "downtrend_regime_at_close"
    ].shift(1, fill_value=False)
    return result


def prepare_void_short_entry_setup(frame: pd.DataFrame) -> pd.DataFrame:
    """下落トレンド中の上昇・下落・反発を順番に検出する。

    下落トレンド開始後の最安値から2 ATR上昇し、その後の最高値から1 ATR
    下落し、さらにその後の最安値から0.5 ATR反発した場合だけ準備完了とする。
    準備完了イベントと3つの価格アンカーは次のバーから利用可能にする。

    Args:
        frame: ``event_time``、OHLC、``is_interpolated``を持つ2時間足。

    Returns:
        ATR、準備状態、遷移イベント、価格アンカーを追加したDataFrame。

    Raises:
        ValueError: 高値・安値が不足する、数値でない、非正、またはOHLCの
            大小関係が不正な場合。共通の時系列検査はトレンド判定に従う。
    """

    missing_columns = {"high", "low"} - set(frame.columns)
    if missing_columns:
        raise ValueError(f"missing columns: {sorted(missing_columns)}")
    result = prepare_void_short_trend_regime(frame)

    for column in ("high", "low"):
        values = pd.to_numeric(result[column], errors="coerce")
        if values.isna().any() or not values.gt(0).all():
            raise ValueError(f"{column} must contain positive numeric values")
        result[column] = values
    if not (
        (result["low"] <= result["close"])
        & (result["close"] <= result["high"])
        & (result["low"] <= result["high"])
    ).all():
        raise ValueError("OHLC price relationship is invalid")

    previous_close = result["close"].shift(1)
    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr14"] = true_range.rolling(
        window=VOID_SHORT_ATR_BARS,
        min_periods=VOID_SHORT_ATR_BARS,
    ).mean()

    states: list[str] = []
    ready_events: list[bool] = []
    rally_start_prices: list[float | None] = []
    rally_peak_prices: list[float | None] = []
    rebound_low_prices: list[float | None] = []
    rally_start_times: list[pd.Timestamp | None] = []
    rally_peak_times: list[pd.Timestamp | None] = []
    rebound_low_times: list[pd.Timestamp | None] = []
    state = VoidShortSetupState.NO_TRADE
    rally_start_price: float | None = None
    rally_peak_price: float | None = None
    rebound_low_price: float | None = None
    rally_start_time: pd.Timestamp | None = None
    rally_peak_time: pd.Timestamp | None = None
    rebound_low_time: pd.Timestamp | None = None

    for row in result.itertuples(index=False):
        regime_ready = bool(row.downtrend_regime_at_close) and pd.notna(row.atr14)
        ready_event = False
        if not regime_ready:
            state = VoidShortSetupState.NO_TRADE
            rally_start_price = None
            rally_peak_price = None
            rebound_low_price = None
            rally_start_time = None
            rally_peak_time = None
            rebound_low_time = None
        elif state == VoidShortSetupState.NO_TRADE:
            state = VoidShortSetupState.WAIT_RALLY
            rally_start_price = float(row.low)
            rally_start_time = row.event_time
        elif state == VoidShortSetupState.WAIT_RALLY:
            if float(row.low) < rally_start_price:
                rally_start_price = float(row.low)
                rally_start_time = row.event_time
            if float(row.close) - rally_start_price >= VOID_SHORT_RALLY_ATR * float(
                row.atr14
            ):
                state = VoidShortSetupState.WAIT_PULLBACK
                rally_peak_price = float(row.high)
                rally_peak_time = row.event_time
        elif state == VoidShortSetupState.WAIT_PULLBACK:
            if float(row.high) > rally_peak_price:
                rally_peak_price = float(row.high)
                rally_peak_time = row.event_time
            if rally_peak_price - float(row.close) >= VOID_SHORT_PULLBACK_ATR * float(
                row.atr14
            ):
                state = VoidShortSetupState.WAIT_REBOUND
                rebound_low_price = float(row.low)
                rebound_low_time = row.event_time
        elif state == VoidShortSetupState.WAIT_REBOUND:
            if float(row.low) < rebound_low_price:
                rebound_low_price = float(row.low)
                rebound_low_time = row.event_time
            if float(row.close) - rebound_low_price >= VOID_SHORT_REBOUND_ATR * float(
                row.atr14
            ):
                state = VoidShortSetupState.READY
                ready_event = True

        states.append(state.value)
        ready_events.append(ready_event)
        rally_start_prices.append(rally_start_price)
        rally_peak_prices.append(rally_peak_price)
        rebound_low_prices.append(rebound_low_price)
        rally_start_times.append(rally_start_time)
        rally_peak_times.append(rally_peak_time)
        rebound_low_times.append(rebound_low_time)

    result["entry_setup_state_at_close"] = states
    result["entry_setup_ready_at_close"] = ready_events
    result["rally_start_price_at_close"] = rally_start_prices
    result["rally_peak_price_at_close"] = rally_peak_prices
    result["rebound_low_price_at_close"] = rebound_low_prices
    result["rally_start_time_at_close"] = rally_start_times
    result["rally_peak_time_at_close"] = rally_peak_times
    result["rebound_low_time_at_close"] = rebound_low_times
    result["entry_setup_ready_for_bar"] = result[
        "entry_setup_ready_at_close"
    ].shift(1, fill_value=False)
    for column in (
        "rally_start_price_at_close",
        "rally_peak_price_at_close",
        "rebound_low_price_at_close",
        "rally_start_time_at_close",
        "rally_peak_time_at_close",
        "rebound_low_time_at_close",
    ):
        result[column.replace("_at_close", "_for_bar")] = result[column].shift(1)
    return result


def build_void_short_fibonacci_levels(
    *,
    rally_start_price: Decimal,
    rally_peak_price: Decimal,
    rebound_low_price: Decimal,
    current_price: Decimal,
    tick_size: Decimal,
) -> tuple[VoidShortLimitLevel, ...]:
    """3アンカーから市場価格より上の売り指値候補を作る。

    売り指値は``rebound_low + (rally_peak - rally_start) * ratio``で求め、
    注文が現在価格以下にならないようtick sizeへ切り上げる。現在価格以下の
    候補は個別に破棄し、全候補が破棄された場合は空tupleを返す。

    Args:
        rally_start_price: 上昇開始地点の下ヒゲ価格。
        rally_peak_price: 上昇後の高値の上ヒゲ価格。
        rebound_low_price: 最初の下落後のリバウンド起点価格。
        current_price: 指値を作成する時点の市場価格。
        tick_size: ZOOMEX銘柄仕様の価格刻み。

    Returns:
        比率順の有効な売り指値候補。数量は含まない。

    Raises:
        ValueError: 価格・tick sizeが非正、非有限、または3アンカーの順序が
            ``rally_start < rebound_low < rally_peak``を満たさない場合。
    """

    values = {
        "rally_start_price": rally_start_price,
        "rally_peak_price": rally_peak_price,
        "rebound_low_price": rebound_low_price,
        "current_price": current_price,
        "tick_size": tick_size,
    }
    for name, value in values.items():
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if not rally_start_price < rebound_low_price < rally_peak_price:
        raise ValueError(
            "anchors must satisfy rally_start < rebound_low < rally_peak"
        )

    price_range = rally_peak_price - rally_start_price
    levels: list[VoidShortLimitLevel] = []
    for ratio in VOID_SHORT_FIBONACCI_RATIOS:
        raw_price = rebound_low_price + price_range * ratio
        ticks = (raw_price / tick_size).to_integral_value(rounding=ROUND_CEILING)
        limit_price = ticks * tick_size
        if limit_price <= current_price:
            continue
        levels.append(
            VoidShortLimitLevel(
                ratio=ratio,
                raw_price=raw_price,
                limit_price=limit_price,
            )
        )
    return tuple(levels)


def pending_void_short_limits_must_cancel(
    *,
    pending_bars: int,
    downtrend_regime: bool,
    state_known: bool,
) -> bool:
    """未約定フィボナッチ指値を取り消すべきか判定する。

    Args:
        pending_bars: 指値作成後に完了した2時間足の本数。
        downtrend_regime: 現在もSMA200がSMA400を下回るか。
        state_known: データとトレンド状態が正常に確定しているか。

    Returns:
        状態不明、トレンド無効、または168本経過なら``True``。

    Raises:
        ValueError: 待機バー数が負の場合。
    """

    if pending_bars < 0:
        raise ValueError("pending_bars must be non-negative")
    return (
        not state_known
        or not downtrend_regime
        or pending_bars >= VOID_SHORT_CORE_POLICY.max_holding_bars
    )


def partition_touched_void_short_levels(
    levels: tuple[VoidShortLimitLevel, ...],
    *,
    bar_high: Decimal,
) -> tuple[tuple[VoidShortLimitLevel, ...], tuple[VoidShortLimitLevel, ...]]:
    """2時間足の高値に触れた指値候補と未到達候補を分ける。

    本関数は数量や約定価格を決めず、各候補を独立に到達判定するだけである。

    Args:
        levels: 有効な売り指値候補。
        bar_high: 判定対象の確定2時間足高値。

    Returns:
        ``(到達候補, 未到達候補)``のtuple。

    Raises:
        ValueError: 高値が非正または非有限の場合。
    """

    if not bar_high.is_finite() or bar_high <= 0:
        raise ValueError("bar_high must be positive and finite")
    touched = tuple(level for level in levels if bar_high >= level.limit_price)
    pending = tuple(level for level in levels if bar_high < level.limit_price)
    return touched, pending


def size_void_short_limit_levels(
    levels: tuple[VoidShortLimitLevel, ...],
    *,
    initial_equity: Decimal,
    current_equity: Decimal,
    qty_step: Decimal,
    min_order_qty: Decimal,
    min_order_notional: Decimal,
) -> tuple[VoidShortSizedLimit, ...]:
    """VOID式の1ロットを各フィボナッチ水準へ割り当てる。

    基準資産は``min(initial_equity, current_equity)``とし、その20倍を100で
    割った20%を1ロットの想定元本にする。利益後の増額、除外水準からの
    再配分、ココモ法による複数ロット化は行わない。

    Args:
        levels: 市場価格より上に残ったフィボナッチ指値候補。
        initial_equity: 実験開始時の基準資産。
        current_equity: セットアップ作成時点の現在資産。
        qty_step: ZOOMEX銘柄仕様の数量刻み。
        min_order_qty: ZOOMEX銘柄仕様の最小注文数量。
        min_order_notional: 初期実験で使う最小想定元本。

    Returns:
        最小数量・想定元本を満たす1ロットの指値候補。

    Raises:
        ValueError: 入力値が非正・非有限、比率が未登録・重複、候補数が4を
            超える、または指値価格が非正・非有限の場合。
    """

    numeric_values = {
        "initial_equity": initial_equity,
        "current_equity": current_equity,
        "qty_step": qty_step,
        "min_order_qty": min_order_qty,
        "min_order_notional": min_order_notional,
    }
    for name, value in numeric_values.items():
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if len(levels) > len(VOID_SHORT_FIBONACCI_RATIOS):
        raise ValueError("levels must not exceed four candidates")
    ratios = tuple(level.ratio for level in levels)
    if len(set(ratios)) != len(ratios):
        raise ValueError("level ratios must not contain duplicates")
    if any(ratio not in VOID_SHORT_FIBONACCI_RATIOS for ratio in ratios):
        raise ValueError("level ratio is not preregistered")

    reference_equity = min(initial_equity, current_equity)
    lot_notional = (
        reference_equity
        * VOID_SHORT_SIZING_REFERENCE_LEVERAGE
        / VOID_SHORT_LOT_DIVISOR
    )
    sized_levels: list[VoidShortSizedLimit] = []
    for level in levels:
        if not level.limit_price.is_finite() or level.limit_price <= 0:
            raise ValueError("limit_price must be positive and finite")
        raw_quantity = lot_notional / level.limit_price
        steps = (raw_quantity / qty_step).to_integral_value(rounding=ROUND_FLOOR)
        quantity = steps * qty_step
        notional = quantity * level.limit_price
        if quantity < min_order_qty or notional < min_order_notional:
            continue
        sized_levels.append(
            VoidShortSizedLimit(
                ratio=level.ratio,
                limit_price=level.limit_price,
                quantity=quantity,
                notional=notional,
            )
        )
    return tuple(sized_levels)
