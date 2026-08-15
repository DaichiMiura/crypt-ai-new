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
VOID_SHORT_STOP_PULLBACK_ATR = Decimal("1")
VOID_SHORT_FIBONACCI_RATIOS = (
    Decimal("0.236"),
    Decimal("0.382"),
    Decimal("0.618"),
    Decimal("1.0"),
)
VOID_SHORT_SIZING_REFERENCE_LEVERAGE = Decimal("20")
VOID_SHORT_LOT_DIVISOR = Decimal("100")
VOID_SHORT_EXECUTION_LEVERAGE = Decimal("1")
VOID_SHORT_TAKE_PROFIT_RATIOS = (Decimal("0.382"), Decimal("0.618"))
VOID_SHORT_STOP_ARM_RATIO = Decimal("1.618")
VOID_SHORT_EMERGENCY_STOP_RATIO = Decimal("2.618")
VOID_SHORT_SMA_PROXIMITY_ATR = Decimal("0.5")


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
class VoidShortTakeProfit:
    """ショート建玉を減らすフィボナッチ利確候補。

    Attributes:
        ratio: フィボナッチ・リトレースメント比率。
        raw_price: tick size適用前の理論価格。
        limit_price: tick sizeへ切り下げた買い指値価格。
        quantity: この水準で閉じる建玉数量。
    """

    ratio: Decimal
    raw_price: Decimal
    limit_price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class VoidShortStopPlan:
    """ショート建玉の通常・緊急損切り価格。

    Attributes:
        arm_price: 押し戻し監視を開始する1.618エクステンション価格。
        emergency_price: 即時緊急決済する2.618エクステンション価格。
    """

    arm_price: Decimal
    emergency_price: Decimal


@dataclass(frozen=True)
class VoidShortAdverseState:
    """1.618到達後の不利方向最高値を保持する状態。

    Attributes:
        armed: 通常損切りの押し戻し監視中か。
        peak_mark_price: 監視開始後のmark price最高値。
    """

    armed: bool = False
    peak_mark_price: Decimal | None = None

    def __post_init__(self) -> None:
        """監視状態と最高値の整合性を検査する。

        Raises:
            ValueError: 未監視なのに最高値がある、または監視中の最高値が
                非正・非有限・未設定の場合。
        """

        if not self.armed and self.peak_mark_price is not None:
            raise ValueError("unarmed state must not have peak_mark_price")
        if self.armed and (
            self.peak_mark_price is None
            or not self.peak_mark_price.is_finite()
            or self.peak_mark_price <= 0
        ):
            raise ValueError("armed state requires positive finite peak_mark_price")


class VoidShortStopDecision(StrEnum):
    """損切り評価が要求する決済種別。"""

    HOLD = "HOLD"
    NORMAL_STOP_NEXT_BAR = "NORMAL_STOP_NEXT_BAR"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    LIQUIDATION = "LIQUIDATION"


@dataclass(frozen=True)
class VoidShortStopEvaluation:
    """1本のmark price足に対する損切り評価結果。

    Attributes:
        state: 次のバーへ引き継ぐ不利方向監視状態。
        decision: このバーで確定した決済要求。
    """

    state: VoidShortAdverseState
    decision: VoidShortStopDecision


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


def prepare_void_short_trend_regime(
    frame: pd.DataFrame,
    *,
    downtrend_persistence_bars: int = 1,
) -> pd.DataFrame:
    """2時間足のSMA200・SMA400から下落トレンド状態を作る。

    現在バーの終値で確定した状態は次のバーからだけ利用可能にする。SMA400を
    計算できない期間は状態不明として新規エントリーを許可しない。
    ``downtrend_persistence_bars``を2以上にすると、現在を含む直近の確定足が
    すべて``SMA200 < SMA400``である場合だけ下落トレンドとする。

    Args:
        frame: ``event_time``、``close``、``is_interpolated``を持つ2時間足。
        downtrend_persistence_bars: 下落条件を連続確認する確定足数。

    Returns:
        SMA、確定時点の状態、次バーで利用できる状態を追加したDataFrame。

    Raises:
        ValueError: 必須列不足、空、時刻不正、欠損、重複、補間行、非正の終値、
            または確認足数が不正な場合。
    """

    if (
        isinstance(downtrend_persistence_bars, bool)
        or not isinstance(downtrend_persistence_bars, int)
        or downtrend_persistence_bars <= 0
    ):
        raise ValueError("downtrend_persistence_bars must be a positive integer")
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
    downtrend_now = result["trend_state_known_at_close"] & (
        result["sma200"] < result["sma400"]
    )
    result["downtrend_regime_at_close"] = downtrend_now & (
        downtrend_now.astype(int)
        .rolling(
            window=downtrend_persistence_bars,
            min_periods=downtrend_persistence_bars,
        )
        .sum()
        == downtrend_persistence_bars
    )
    result["trend_state_known_for_bar"] = result[
        "trend_state_known_at_close"
    ].shift(1, fill_value=False)
    result["downtrend_regime_for_bar"] = result[
        "downtrend_regime_at_close"
    ].shift(1, fill_value=False)
    return result


def prepare_void_short_entry_setup(
    frame: pd.DataFrame,
    *,
    downtrend_persistence_bars: int = 1,
    require_sma_proximity: bool = False,
) -> pd.DataFrame:
    """下落トレンド中の上昇・下落・反発を順番に検出する。

    下落トレンド開始後の最安値から2 ATR上昇し、その後の最高値から1 ATR
    下落し、さらにその後の最安値から0.5 ATR反発した場合だけ準備完了とする。
    準備完了イベントと3つの価格アンカーは次のバーから利用可能にする。

    Args:
        frame: ``event_time``、OHLC、``is_interpolated``を持つ2時間足。
        downtrend_persistence_bars: 下落条件を連続確認する確定足数。
        require_sma_proximity: 準備完了足の高値がSMA200付近まで戻り、終値が
            SMA200以下であることを追加要求するか。

    Returns:
        ATR、準備状態、遷移イベント、価格アンカーを追加したDataFrame。

    Raises:
        ValueError: 高値・安値が不足する、数値でない、非正、またはOHLCの
            大小関係が不正な場合。共通の時系列検査はトレンド判定に従う。
    """

    missing_columns = {"high", "low"} - set(frame.columns)
    if missing_columns:
        raise ValueError(f"missing columns: {sorted(missing_columns)}")
    if not isinstance(require_sma_proximity, bool):
        raise ValueError("require_sma_proximity must be bool")
    result = prepare_void_short_trend_regime(
        frame,
        downtrend_persistence_bars=downtrend_persistence_bars,
    )

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
    result["sma200_proximity_at_close"] = (
        result["sma200"].notna()
        & result["atr14"].notna()
        & (
            result["high"]
            >= result["sma200"]
            - result["atr14"] * float(VOID_SHORT_SMA_PROXIMITY_ATR)
        )
        & (result["close"] <= result["sma200"])
    )

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
            rebound_confirmed = float(row.close) - rebound_low_price >= (
                VOID_SHORT_REBOUND_ATR * float(row.atr14)
            )
            sma_proximity_confirmed = bool(row.sma200_proximity_at_close)
            if rebound_confirmed and (
                not require_sma_proximity or sma_proximity_confirmed
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
    lot_counts: tuple[int, ...] = (1, 1, 1, 1),
    max_total_lot_count: int | None = None,
    compound_profits: bool = False,
) -> tuple[VoidShortSizedLimit, ...]:
    """指定したロット数を各フィボナッチ水準へ割り当てる。

    通常は基準資産を``min(initial_equity, current_equity)``とし、その20倍を
    100で割った20%を1ロットの想定元本にする。``compound_profits=True``では
    現在資産を基準にするため、利益後はロットが増え、損失後は減る。
    ``lot_counts``は登録済み比率の順序に対応し、除外水準からの再配分は行わない。
    ``max_total_lot_count``を指定した場合は合計ロット数を制限する。

    Args:
        levels: 市場価格より上に残ったフィボナッチ指値候補。
        initial_equity: 実験開始時の基準資産。
        current_equity: セットアップ作成時点の現在資産。
        qty_step: ZOOMEX銘柄仕様の数量刻み。
        min_order_qty: ZOOMEX銘柄仕様の最小注文数量。
        min_order_notional: 初期実験で使う最小想定元本。
        lot_counts: 各登録済みフィボナッチ水準へ割り当てるロット数。
        max_total_lot_count: 1回のセットアップで許可する合計ロット数。
        compound_profits: 現在資産をロット計算へ反映するか。

    Returns:
        最小数量・想定元本を満たすロット数調整済みの指値候補。

    Raises:
        ValueError: 入力値が非正・非有限、比率が未登録・重複、候補数が4を
            超える、ロット列が不正、合計上限を超える、または指値価格が非正・
            非有限、複利フラグがboolでない場合。
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
    if len(lot_counts) != len(VOID_SHORT_FIBONACCI_RATIOS):
        raise ValueError("lot_counts must contain four entries")
    if any(
        isinstance(count, bool) or not isinstance(count, int) or count <= 0
        for count in lot_counts
    ):
        raise ValueError("lot_counts must contain positive integers")
    if max_total_lot_count is not None:
        if (
            isinstance(max_total_lot_count, bool)
            or not isinstance(max_total_lot_count, int)
            or max_total_lot_count <= 0
        ):
            raise ValueError("max_total_lot_count must be a positive integer")
        if sum(lot_counts) > max_total_lot_count:
            raise ValueError("lot_counts exceed max_total_lot_count")
    if not isinstance(compound_profits, bool):
        raise ValueError("compound_profits must be bool")
    ratios = tuple(level.ratio for level in levels)
    if len(set(ratios)) != len(ratios):
        raise ValueError("level ratios must not contain duplicates")
    if any(ratio not in VOID_SHORT_FIBONACCI_RATIOS for ratio in ratios):
        raise ValueError("level ratio is not preregistered")

    reference_equity = (
        current_equity
        if compound_profits
        else min(initial_equity, current_equity)
    )
    lot_notional = (
        reference_equity
        * VOID_SHORT_SIZING_REFERENCE_LEVERAGE
        / VOID_SHORT_LOT_DIVISOR
    )
    sized_levels: list[VoidShortSizedLimit] = []
    for level in levels:
        if not level.limit_price.is_finite() or level.limit_price <= 0:
            raise ValueError("limit_price must be positive and finite")
        lot_index = VOID_SHORT_FIBONACCI_RATIOS.index(level.ratio)
        lot_count = lot_counts[lot_index]
        raw_quantity = lot_notional * lot_count / level.limit_price
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
                lot_count=lot_count,
            )
        )
    return tuple(sized_levels)


def build_void_short_take_profits(
    *,
    rally_start_price: Decimal,
    rally_peak_price: Decimal,
    average_entry_price: Decimal,
    open_quantity: Decimal,
    tick_size: Decimal,
    qty_step: Decimal,
) -> tuple[VoidShortTakeProfit, ...]:
    """フィボナッチ・リトレースメントから2段階利確を作る。

    0.382で建玉の半分、0.618で残量すべてを閉じる。ショート平均約定価格
    以上の候補は利益確定にならないため除外し、浅い候補が除外された場合は
    深い候補へ全数量を割り当てる。

    Args:
        rally_start_price: 上昇開始地点の下ヒゲ価格。
        rally_peak_price: 上昇後の高値の上ヒゲ価格。
        average_entry_price: 現在のショート平均約定価格。
        open_quantity: 現在のショート建玉数量。
        tick_size: ZOOMEX銘柄仕様の価格刻み。
        qty_step: ZOOMEX銘柄仕様の数量刻み。

    Returns:
        比率順の有効な買い指値利確候補。

    Raises:
        ValueError: 入力が非正・非有限、上昇値幅が非正、または建玉数量が
            qty stepに整合しない場合。
    """

    values = {
        "rally_start_price": rally_start_price,
        "rally_peak_price": rally_peak_price,
        "average_entry_price": average_entry_price,
        "open_quantity": open_quantity,
        "tick_size": tick_size,
        "qty_step": qty_step,
    }
    for name, value in values.items():
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if rally_peak_price <= rally_start_price:
        raise ValueError("rally_peak_price must be above rally_start_price")
    quantity_steps = open_quantity / qty_step
    if quantity_steps != quantity_steps.to_integral_value():
        raise ValueError("open_quantity must align with qty_step")

    price_range = rally_peak_price - rally_start_price
    valid_prices: list[tuple[Decimal, Decimal, Decimal]] = []
    for ratio in VOID_SHORT_TAKE_PROFIT_RATIOS:
        raw_price = rally_peak_price - price_range * ratio
        ticks = (raw_price / tick_size).to_integral_value(rounding=ROUND_FLOOR)
        limit_price = ticks * tick_size
        if limit_price <= 0 or limit_price >= average_entry_price:
            continue
        valid_prices.append((ratio, raw_price, limit_price))
    if not valid_prices:
        return ()

    first_quantity_steps = (quantity_steps / Decimal("2")).to_integral_value(
        rounding=ROUND_FLOOR
    )
    first_quantity = first_quantity_steps * qty_step
    quantities: list[Decimal]
    if len(valid_prices) == 1 or first_quantity == 0:
        quantities = [open_quantity]
        valid_prices = [valid_prices[-1]]
    else:
        quantities = [first_quantity, open_quantity - first_quantity]

    return tuple(
        VoidShortTakeProfit(
            ratio=ratio,
            raw_price=raw_price,
            limit_price=limit_price,
            quantity=quantity,
        )
        for (ratio, raw_price, limit_price), quantity in zip(
            valid_prices, quantities, strict=True
        )
    )


def build_void_short_stop_plan(
    *,
    rally_start_price: Decimal,
    rally_peak_price: Decimal,
    rebound_low_price: Decimal,
    tick_size: Decimal,
) -> VoidShortStopPlan:
    """1.618と2.618エクステンションの損切り価格を作る。

    Args:
        rally_start_price: 上昇開始地点の下ヒゲ価格。
        rally_peak_price: 上昇後の高値の上ヒゲ価格。
        rebound_low_price: 最初の下落後のリバウンド起点価格。
        tick_size: ZOOMEX銘柄仕様の価格刻み。

    Returns:
        tick sizeへ切り上げた通常監視開始価格と緊急停止価格。

    Raises:
        ValueError: 入力が非正・非有限、または3アンカーの順序が不正な場合。
    """

    values = {
        "rally_start_price": rally_start_price,
        "rally_peak_price": rally_peak_price,
        "rebound_low_price": rebound_low_price,
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

    def extension_price(ratio: Decimal) -> Decimal:
        """指定比率の価格をtick sizeへ切り上げる。

        Args:
            ratio: フィボナッチ・エクステンション比率。

        Returns:
            有効な価格刻みに切り上げた価格。
        """

        raw_price = rebound_low_price + price_range * ratio
        ticks = (raw_price / tick_size).to_integral_value(rounding=ROUND_CEILING)
        return ticks * tick_size

    return VoidShortStopPlan(
        arm_price=extension_price(VOID_SHORT_STOP_ARM_RATIO),
        emergency_price=extension_price(VOID_SHORT_EMERGENCY_STOP_RATIO),
    )


def evaluate_void_short_stop_bar(
    *,
    plan: VoidShortStopPlan,
    state: VoidShortAdverseState,
    mark_high: Decimal,
    mark_close: Decimal,
    atr: Decimal,
    liquidation_price: Decimal,
    normal_stop_pullback_atr: Decimal = VOID_SHORT_STOP_PULLBACK_ATR,
) -> VoidShortStopEvaluation:
    """確定したmark price足で通常・緊急損切りを評価する。

    清算価格到達を最優先し、次に2.618緊急停止を判定する。1.618へ初めて
    到達したバーでは通常損切りを行わず、次バー以降に最高値から1 ATR以上
    下落して終えた場合だけ、次バーのreduce-only決済を要求する。

    Args:
        plan: 1.618と2.618の損切り価格。
        state: 前バーから引き継いだ監視状態。
        mark_high: 現在の確定2時間足mark price高値。
        mark_close: 現在の確定2時間足mark price終値。
        atr: trade価格から計算した現在のATR14。
        liquidation_price: 現在のショート建玉清算価格。
        normal_stop_pullback_atr: 通常損切りに必要な最高値からのATR倍率。

    Returns:
        次バーへ引き継ぐ状態と決済要求。

    Raises:
        ValueError: 価格・ATRが非正・非有限、mark終値が高値を上回る、または
            損切り計画の価格順序または反落幅が不正な場合。
    """

    values = {
        "arm_price": plan.arm_price,
        "emergency_price": plan.emergency_price,
        "mark_high": mark_high,
        "mark_close": mark_close,
        "atr": atr,
        "liquidation_price": liquidation_price,
        "normal_stop_pullback_atr": normal_stop_pullback_atr,
    }
    for name, value in values.items():
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if plan.emergency_price <= plan.arm_price:
        raise ValueError("emergency_price must be above arm_price")
    if normal_stop_pullback_atr <= 0:
        raise ValueError("normal_stop_pullback_atr must be positive")
    if mark_close > mark_high:
        raise ValueError("mark_close must not exceed mark_high")

    if mark_high >= liquidation_price:
        return VoidShortStopEvaluation(
            state=state,
            decision=VoidShortStopDecision.LIQUIDATION,
        )
    if mark_high >= plan.emergency_price:
        return VoidShortStopEvaluation(
            state=state,
            decision=VoidShortStopDecision.EMERGENCY_STOP,
        )
    if not state.armed:
        if mark_high >= plan.arm_price:
            return VoidShortStopEvaluation(
                state=VoidShortAdverseState(
                    armed=True,
                    peak_mark_price=mark_high,
                ),
                decision=VoidShortStopDecision.HOLD,
            )
        return VoidShortStopEvaluation(
            state=state,
            decision=VoidShortStopDecision.HOLD,
        )

    peak_mark_price = max(state.peak_mark_price, mark_high)
    next_state = VoidShortAdverseState(
        armed=True,
        peak_mark_price=peak_mark_price,
    )
    if mark_close <= peak_mark_price - atr * normal_stop_pullback_atr:
        return VoidShortStopEvaluation(
            state=next_state,
            decision=VoidShortStopDecision.NORMAL_STOP_NEXT_BAR,
        )
    return VoidShortStopEvaluation(
        state=next_state,
        decision=VoidShortStopDecision.HOLD,
    )
