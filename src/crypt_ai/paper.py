"""秘密情報や実注文経路を持たない決定論的paper会計。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from crypt_ai.research import INTERPOLATED_COLUMN, CostModel


def _decimal(value: object) -> Decimal:
    """設定値または市場値をDecimalへ変換する。

    Args:
        value: 文字列化できる数値。

    Returns:
        浮動小数点表現を直接引き継がないDecimal値。
    """

    return Decimal(str(value))


@dataclass(frozen=True)
class PaperConfig:
    """固定したpaper戦略設定と実効リスク上限。

    Attributes:
        strategy_id: 承認済み実験ID。
        strategy_version: 固定戦略の版。
        symbol: paper対象銘柄。
        start_utc: paper処理を開始するUTC時刻。
        initial_cash: 仮想初期現金。
        cost_model: 仮想約定へ使う費用モデル。
        max_order_notional: 1注文の実効元本上限。
        max_position_notional: 銘柄positionの実効元本上限。
        max_daily_loss: 前日markからの日次損失停止値。
        max_drawdown: equity最高値からの絶対損失停止値。
        reject_interpolated_bar_orders: 補間日上の新規約定を拒否するか。
    """

    strategy_id: str
    strategy_version: str
    symbol: str
    start_utc: pd.Timestamp
    initial_cash: Decimal
    cost_model: CostModel
    max_order_notional: Decimal
    max_position_notional: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    reject_interpolated_bar_orders: bool


@dataclass
class PaperState:
    """再起動後にも引き継ぐpaper口座状態。

    Attributes:
        strategy_id: 状態を所有する実験ID。
        cash: 仮想現金残高。
        quantity: 仮想BTC数量。
        initial_cash: 開始時の仮想資本。
        high_watermark: 過去最高equity。
        previous_equity: 直前バー終値のequity。
        last_event_time: 最後に処理した市場バー時刻。
        last_bar_hash: 最終バー内容のSHA-256。
        processed_bars: 処理済みバー数。
        halted: 新規リスクを停止しているか。
        halt_reasons: 停止理由。
    """

    strategy_id: str
    cash: str
    quantity: str
    initial_cash: str
    high_watermark: str
    previous_equity: str
    last_event_time: str | None = None
    last_bar_hash: str | None = None
    processed_bars: int = 0
    halted: bool = False
    halt_reasons: list[str] = field(default_factory=list)


def load_paper_config(global_path: Path, strategy_path: Path) -> PaperConfig:
    """全体上限と戦略上限から実効paper設定を読み込む。

    Args:
        global_path: paper口座全体のYAML設定。
        strategy_path: EXP-2026-0012専用YAML設定。

    Returns:
        小さい方のリスク上限を持つ固定paper設定。

    Raises:
        ValueError: paper専用、安全境界、許可銘柄、設定値が不正な場合。
    """

    global_config = yaml.safe_load(global_path.read_text(encoding="utf-8"))
    strategy_config = yaml.safe_load(strategy_path.read_text(encoding="utf-8"))
    for name, value in (("global", global_config), ("strategy", strategy_config)):
        if not isinstance(value, dict):
            raise ValueError(f"{name} paper config must be a mapping")
        if value.get("environment") != "paper" or value.get(
            "live_trading_enabled"
        ) is not False:
            raise ValueError(f"{name} config is not paper-only")
    if strategy_config.get("status") != "paper_approved":
        raise ValueError("strategy is not paper-approved")
    symbol = strategy_config["allowed_symbols"][0]
    if symbol not in global_config["allowed_symbols"]:
        raise ValueError(f"symbol is not globally allowed: {symbol}")
    if strategy_config.get("allow_short") is not False:
        raise ValueError("paper strategy must remain long-only")
    allocation = strategy_config["allocation"]
    if allocation.get("currency") != "JPY":
        raise ValueError("paper accounting currency must be JPY")
    global_limits = global_config["limits"]
    strategy_limits = strategy_config["limits"]

    def effective_limit(name: str) -> Decimal:
        """口座全体と戦略固有の小さい上限を返す。

        Args:
            name: 両設定の`limits`に存在する上限名。

        Returns:
            正の実効上限。

        Raises:
            ValueError: 実効上限が正でない場合。
        """

        value = min(_decimal(global_limits[name]), _decimal(strategy_limits[name]))
        if value <= 0:
            raise ValueError(f"effective paper limit must be positive: {name}")
        return value

    initial_cash = _decimal(allocation["initial_cash"])
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    cost = strategy_config["cost_model"]
    return PaperConfig(
        strategy_id=strategy_config["strategy_id"],
        strategy_version=strategy_config["strategy_version"],
        symbol=symbol,
        start_utc=pd.Timestamp(strategy_config["paper_window"]["start_utc"]),
        initial_cash=initial_cash,
        cost_model=CostModel(
            _decimal(cost["fee_rate"]),
            _decimal(cost["round_trip_spread"]),
            _decimal(cost["slippage_per_fill"]),
        ),
        max_order_notional=effective_limit("max_order_notional"),
        max_position_notional=effective_limit("max_symbol_position_notional"),
        max_daily_loss=effective_limit("max_daily_loss"),
        max_drawdown=effective_limit("max_drawdown"),
        reject_interpolated_bar_orders=bool(
            strategy_config["stops"]["reject_interpolated_bar_orders"]
        ),
    )


def new_paper_state(config: PaperConfig) -> PaperState:
    """承認済み初期資金から新しいpaper状態を作る。

    Args:
        config: 固定paper設定。

    Returns:
        positionを持たない初期状態。
    """

    initial = str(config.initial_cash)
    return PaperState(
        strategy_id=config.strategy_id,
        cash=initial,
        quantity="0",
        initial_cash=initial,
        high_watermark=initial,
        previous_equity=initial,
    )


def load_paper_state(path: Path, config: PaperConfig) -> PaperState:
    """状態ファイルを読み、戦略IDの一致を確認する。

    Args:
        path: JSON状態ファイル。
        config: 固定paper設定。

    Returns:
        既存状態。ファイルがなければ初期状態。

    Raises:
        ValueError: 状態の戦略IDまたは残高が不正な場合。
    """

    if not path.exists():
        return new_paper_state(config)
    state = PaperState(**json.loads(path.read_text(encoding="utf-8")))
    if state.strategy_id != config.strategy_id:
        raise ValueError("paper state strategy_id mismatch")
    if _decimal(state.cash) < 0 or _decimal(state.quantity) < 0:
        raise ValueError("paper state contains a negative balance")
    return state


def save_paper_state(path: Path, state: PaperState) -> None:
    """paper状態を一時ファイル経由で置換保存する。

    Args:
        path: 保存先JSONファイル。
        state: 保存するpaper状態。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_paper_events(path: Path, events: list[dict[str, object]]) -> None:
    """生成したpaperイベントをJSON Lines台帳へ追記する。

    Args:
        path: 追記先台帳ファイル。
        events: 時系列順の不変paperイベント。
    """

    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def process_paper_bar(
    state: PaperState,
    row: object,
    config: PaperConfig,
) -> list[dict[str, object]]:
    """一つの確定日足を仮想約定・markし、状態を更新する。

    Args:
        state: 更新対象のpaper状態。
        row: 時刻、OHLC、desired position、補間フラグを持つ行。
        config: 固定paper設定とリスク上限。

    Returns:
        このバーで生成した注文、約定、fee、mark、停止イベント。

    Raises:
        ValueError: 時刻逆行、同時刻内容差、価格、position、残高が不正な場合。
    """

    event_time = pd.Timestamp(row.event_time)
    desired = int(row.desired_atr_position)
    if desired not in (0, 1):
        raise ValueError(f"unknown desired_atr_position: {desired}")
    raw_open = _decimal(row.open)
    close = _decimal(row.close)
    if raw_open <= 0 or close <= 0:
        raise ValueError("paper prices must be positive")
    bar_payload = "|".join(
        str(value)
        for value in (event_time.isoformat(), row.open, row.high, row.low, row.close)
    )
    bar_hash = hashlib.sha256(bar_payload.encode("utf-8")).hexdigest()
    if state.last_event_time is not None:
        previous_time = pd.Timestamp(state.last_event_time)
        if event_time < previous_time:
            raise ValueError("paper bar time moved backwards")
        if event_time == previous_time:
            if state.last_bar_hash != bar_hash:
                state.halted = True
                state.halt_reasons.append("duplicate_bar_conflict")
                raise ValueError("duplicate paper bar has conflicting OHLC")
            return []

    cash = _decimal(state.cash)
    quantity = _decimal(state.quantity)
    events: list[dict[str, object]] = []
    if state.processed_bars == 0:
        events.append(
            _event(config, event_time, "cash_deposit", amount=str(config.initial_cash))
        )
    effective_desired = 0 if state.halted else desired
    requires_order = (effective_desired == 1 and quantity == 0) or (
        effective_desired == 0 and quantity > 0
    )
    if (
        requires_order
        and config.reject_interpolated_bar_orders
        and bool(getattr(row, INTERPOLATED_COLUMN, False))
    ):
        state.halted = True
        state.halt_reasons.append("order_on_interpolated_bar")
        events.append(_event(config, event_time, "order_rejected", reason="interpolated_bar"))
        effective_desired = 0

    if effective_desired == 1 and quantity == 0:
        order_notional = min(
            cash, config.max_order_notional, config.max_position_notional
        )
        order_id = _order_id(config, event_time, "BUY")
        execution_price = config.cost_model.buy_price(raw_open)
        fee = order_notional * config.cost_model.fee_rate
        quantity = (order_notional - fee) / execution_price
        cash -= order_notional
        events.extend(
            _fill_events(
                config, event_time, order_id, "BUY", execution_price, quantity, fee
            )
        )
    elif effective_desired == 0 and quantity > 0:
        order_id = _order_id(config, event_time, "SELL")
        execution_price = config.cost_model.sell_price(raw_open)
        sold_quantity = quantity
        gross = sold_quantity * execution_price
        fee = gross * config.cost_model.fee_rate
        cash = gross - fee
        quantity = Decimal("0")
        events.extend(
            _fill_events(
                config, event_time, order_id, "SELL", execution_price, sold_quantity, fee
            )
        )

    equity = cash + quantity * close
    previous_equity = _decimal(state.previous_equity)
    high_watermark = max(_decimal(state.high_watermark), equity)
    daily_loss = max(Decimal("0"), previous_equity - equity)
    drawdown = high_watermark - equity
    position_notional = quantity * close
    if (
        position_notional > config.max_position_notional
        and "max_position_notional" not in state.halt_reasons
    ):
        state.halted = True
        state.halt_reasons.append("max_position_notional")
        events.append(
            _event(config, event_time, "risk_halt", reason="max_position_notional")
        )
    if daily_loss > config.max_daily_loss and "max_daily_loss" not in state.halt_reasons:
        state.halted = True
        state.halt_reasons.append("max_daily_loss")
        events.append(_event(config, event_time, "risk_halt", reason="max_daily_loss"))
    if drawdown > config.max_drawdown and "max_drawdown" not in state.halt_reasons:
        state.halted = True
        state.halt_reasons.append("max_drawdown")
        events.append(_event(config, event_time, "risk_halt", reason="max_drawdown"))
    events.append(
        _event(
            config,
            event_time,
            "mark",
            cash=str(cash),
            quantity=str(quantity),
            mark_price=str(close),
            equity=str(equity),
            daily_loss=str(daily_loss),
            drawdown=str(drawdown),
        )
    )
    state.cash = str(cash)
    state.quantity = str(quantity)
    state.high_watermark = str(high_watermark)
    state.previous_equity = str(equity)
    state.last_event_time = event_time.isoformat()
    state.last_bar_hash = bar_hash
    state.processed_bars += 1
    return events


def _order_id(config: PaperConfig, event_time: pd.Timestamp, side: str) -> str:
    """戦略、時刻、sideから決定論的paper order IDを作る。

    Args:
        config: 戦略IDと版を持つpaper設定。
        event_time: 仮想注文時刻。
        side: `BUY`または`SELL`。

    Returns:
        再実行でも変わらない短いorder ID。
    """

    source = f"{config.strategy_id}|{config.strategy_version}|{event_time.isoformat()}|{side}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def _event(
    config: PaperConfig, event_time: pd.Timestamp, event_type: str, **values: Any
) -> dict[str, object]:
    """共通識別情報を持つpaper台帳イベントを作る。

    Args:
        config: 戦略と銘柄を持つpaper設定。
        event_time: 市場イベントのUTC時刻。
        event_type: 会計または運用イベント種別。
        **values: 種別固有の値。

    Returns:
        JSONへ直列化可能なイベント辞書。
    """

    return {
        "event_type": event_type,
        "event_time": event_time.isoformat(),
        "source": "paper",
        "account": "paper",
        "symbol": config.symbol,
        "strategy_id": config.strategy_id,
        "strategy_version": config.strategy_version,
        **values,
    }


def _fill_events(
    config: PaperConfig,
    event_time: pd.Timestamp,
    order_id: str,
    side: str,
    execution_price: Decimal,
    quantity: Decimal,
    fee: Decimal,
) -> list[dict[str, object]]:
    """仮想注文、約定、手数料の台帳イベントを作る。

    Args:
        config: 固定paper設定。
        event_time: 仮想約定時刻。
        order_id: 決定論的注文ID。
        side: `BUY`または`SELL`。
        execution_price: 費用調整後の仮想約定価格。
        quantity: 仮想約定数量。
        fee: 仮想手数料。

    Returns:
        order_submitted、fill、feeの3イベント。
    """

    common = {"order_id": order_id, "side": side}
    return [
        _event(config, event_time, "order_submitted", **common),
        _event(
            config,
            event_time,
            "fill",
            **common,
            price=str(execution_price),
            quantity=str(quantity),
        ),
        _event(
            config,
            event_time,
            "fee",
            **common,
            amount=str(fee),
            currency="JPY",
        ),
    ]
