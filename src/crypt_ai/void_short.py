"""EXP-2026-0015のVOID式ショートに共通する基本方針を提供する。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


VOID_SHORT_SYMBOLS = frozenset(
    {"LINKUSDT", "UNIUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT", "AAVEUSDT"}
)


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
