"""Local position storage for buy/sell and take-profit monitoring."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from price_tick import adjust_price_to_tick
from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/position_manager.log")


@dataclass
class PositionRecord:
    stock_code: str
    stock_name: str
    quantity: int
    entry_price: float
    entry_time: str
    target_price: float = 0.0
    avg_price: float = 0.0
    stop_price: float = 0.0
    stop_loss_price: float = 0.0
    current_price: float = 0.0
    is_closed: bool = False
    status: str = "OPEN"
    source: str = "local"
    broker_synced_at: str = ""
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None
    order_no: str = ""
    filled_quantity: int = 0
    filled_price: float = 0.0
    force_trade_mode: bool = False
    forced_exit_time_str: str = "09:30"
    strategy_id: str = ""
    strategy_name: str = ""
    take_profit_rate: float = 0.02
    stop_loss_rate: float = -0.03
    allowed_sell_sessions: list = field(default_factory=list)
    buy_window: str = ""
    force_exit_rule: str = ""

    def pnl_rate(self, current_price: Optional[float] = None) -> float:
        p = current_price if current_price is not None else self.exit_price
        if p is None or self.entry_price <= 0:
            return 0.0
        return (float(p) - float(self.entry_price)) / float(self.entry_price)


class PositionManager:
    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        strategy = self.cfg.get("strategy", {})
        self._target_rate = float(strategy.get("target_profit_rate", 0.02))
        self._stop_rate = float(strategy.get("stop_loss_rate", -0.03))
        self._forced_exit_time = strategy.get("forced_exit_time_next_day", "09:30")
        self._positions_file = Path(self.cfg.get("data", {}).get("positions_file", "data/positions.json"))
        self._positions: Dict[str, PositionRecord] = {}
        self._load_local_positions()

    def _load_local_positions(self) -> None:
        if not self._positions_file.exists():
            return
        try:
            with open(self._positions_file, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data, list):
                data = {}
            loaded = {}
            for code, rec in data.items():
                if not isinstance(rec, dict):
                    continue
                rec = dict(rec)
                rec.setdefault("stock_code", code)
                rec.setdefault("stock_name", code)
                rec.setdefault("quantity", 0)
                rec.setdefault("entry_price", rec.get("avg_price", 0))
                rec.setdefault("entry_time", datetime.now().isoformat())
                rec.setdefault("avg_price", rec.get("entry_price", 0))
                rec.setdefault("target_price", round(float(rec.get("entry_price", 0)) * 1.02))
                rec.setdefault("stop_price", round(float(rec.get("entry_price", 0)) * 0.97))
                rec.setdefault("stop_loss_price", rec.get("stop_price", 0))
                rec.setdefault("status", "CLOSED" if rec.get("is_closed") else "OPEN")
                allowed = {f.name for f in PositionRecord.__dataclass_fields__.values()}
                rec = {k: v for k, v in rec.items() if k in allowed}
                loaded[str(code).zfill(6)] = PositionRecord(**rec)
            self._positions = loaded
        except Exception as ex:
            logger.error("positions load failed: %s", ex)
            self._positions = {}

    def save_local_positions(self) -> None:
        ensure_dir(str(self._positions_file.parent))
        data = {code: asdict(pos) for code, pos in self._positions.items()}
        with open(self._positions_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def sync_positions_from_broker(self, broker_positions) -> None:
        if broker_positions is None or len(broker_positions) == 0:
            return
        for _, row in broker_positions.iterrows():
            code = str(row.get("stock_code", "")).zfill(6)
            qty = int(row.get("quantity", 0) or 0)
            if not code or qty <= 0:
                continue
            avg_price = float(row.get("avg_price", 0) or 0)
            current_price = float(row.get("current_price", avg_price) or avg_price)
            pos = self._positions.get(code)
            if pos is None:
                pos = self.update_position_after_buy(
                    stock_code=code,
                    stock_name=str(row.get("stock_name", code)),
                    quantity=qty,
                    entry_price=avg_price,
                    entry_time=datetime.now(),
                    source="broker",
                )
            pos.quantity = qty
            pos.entry_price = avg_price
            pos.avg_price = avg_price
            pos.current_price = current_price
            pos.target_price = adjust_price_to_tick(round(avg_price * 1.02), side="sell", method="ceil")
            pos.stop_price = adjust_price_to_tick(round(avg_price * 0.97), side="sell", method="ceil")
            pos.stop_loss_price = pos.stop_price
            pos.source = "broker"
            pos.broker_synced_at = datetime.now().isoformat()
            pos.status = "OPEN"
            pos.is_closed = False
        self.save_local_positions()

    def update_position_after_buy(
        self,
        stock_code: str,
        stock_name: str,
        quantity: int,
        entry_price: float,
        entry_time: Optional[datetime] = None,
        order_no: str = "",
        filled_quantity: int = 0,
        filled_price: float = 0.0,
        force_trade_mode: bool = False,
        strategy_id: str = "",
        strategy_name: str = "",
        take_profit_rate: float = 0.0,
        stop_loss_rate: float = 0.0,
        allowed_sell_sessions: Optional[list] = None,
        buy_window: str = "",
        force_exit_rule: str = "",
        source: str = "local",
    ) -> PositionRecord:
        code = str(stock_code).zfill(6)
        t = entry_time or datetime.now()
        existing = self._positions.get(code)
        if existing and not existing.is_closed:
            total_qty = int(existing.quantity) + int(quantity)
            if total_qty > 0:
                entry_price = (
                    float(existing.entry_price) * int(existing.quantity)
                    + float(entry_price) * int(quantity)
                ) / total_qty
                quantity = total_qty
        tp_rate = take_profit_rate if take_profit_rate != 0.0 else self._target_rate
        sl_rate = stop_loss_rate if stop_loss_rate != 0.0 else self._stop_rate
        target_p = adjust_price_to_tick(round(float(entry_price) * (1 + tp_rate)), side="sell", method="ceil")
        stop_p = adjust_price_to_tick(round(float(entry_price) * (1 + sl_rate)), side="sell", method="ceil")
        pos = PositionRecord(
            stock_code=code,
            stock_name=stock_name,
            quantity=int(quantity),
            entry_price=float(entry_price),
            avg_price=float(entry_price),
            entry_time=t.isoformat(),
            target_price=target_p,
            stop_price=stop_p,
            stop_loss_price=stop_p,
            current_price=float(entry_price),
            order_no=order_no,
            filled_quantity=filled_quantity if filled_quantity > 0 else int(quantity),
            filled_price=filled_price if filled_price > 0 else float(entry_price),
            force_trade_mode=force_trade_mode,
            forced_exit_time_str=self._forced_exit_time,
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            take_profit_rate=tp_rate,
            stop_loss_rate=sl_rate,
            allowed_sell_sessions=allowed_sell_sessions or [],
            buy_window=buy_window,
            force_exit_rule=force_exit_rule,
            source=source,
        )
        self._positions[code] = pos
        self.save_local_positions()
        return pos

    def update_position_after_sell(
        self,
        stock_code: str,
        exit_price: float,
        exit_reason: str,
        exit_time: Optional[datetime] = None,
    ) -> Optional[PositionRecord]:
        code = str(stock_code).zfill(6)
        pos = self._positions.get(code)
        if pos is None:
            return None
        t = exit_time or datetime.now()
        pos.exit_price = float(exit_price)
        pos.exit_time = t.isoformat()
        pos.exit_reason = exit_reason
        pos.is_closed = True
        pos.status = "CLOSED"
        del self._positions[code]
        self.save_local_positions()
        return pos

    def get_target_price(self, entry_price: float) -> float:
        return round(float(entry_price) * (1 + self._target_rate))

    def get_stop_loss_price(self, entry_price: float) -> float:
        return round(float(entry_price) * (1 + self._stop_rate))

    def should_take_profit(self, stock_code: str, current_price: float) -> bool:
        pos = self._positions.get(str(stock_code).zfill(6))
        return bool(pos and float(current_price) >= float(pos.target_price))

    def should_stop_loss(self, stock_code: str, current_price: float) -> bool:
        pos = self._positions.get(str(stock_code).zfill(6))
        return bool(pos and float(current_price) <= float(pos.stop_price))

    def should_force_exit(self, stock_code: str, now: Optional[datetime] = None) -> bool:
        pos = self._positions.get(str(stock_code).zfill(6))
        if pos is None:
            return False
        current = now or datetime.now()
        try:
            entry_dt = datetime.fromisoformat(pos.entry_time)
        except Exception:
            return False
        if current.date() <= entry_dt.date():
            return False
        force_h, force_m = map(int, self._forced_exit_time.split(":"))
        return current.hour > force_h or (current.hour == force_h and current.minute >= force_m)

    def get_all_positions(self) -> Dict[str, PositionRecord]:
        return dict(self._positions)

    def get_position(self, stock_code: str) -> Optional[PositionRecord]:
        return self._positions.get(str(stock_code).zfill(6))

    def has_position(self, stock_code: str) -> bool:
        return str(stock_code).zfill(6) in self._positions

    def position_count(self) -> int:
        return len(self._positions)

    def get_force_exit_targets(self, now: Optional[datetime] = None) -> List[str]:
        return [code for code in self._positions if self.should_force_exit(code, now)]
