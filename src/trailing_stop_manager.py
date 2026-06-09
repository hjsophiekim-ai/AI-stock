"""Trailing-stop state management for sell policies."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from price_tick import adjust_price_to_tick
from utils import ensure_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _event_path() -> Path:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    return PROJECT_ROOT / "reports" / f"trailing_stop_events_{datetime.now().strftime('%Y%m%d')}.csv"


def _append_event(row: dict) -> None:
    path = _event_path()
    df = pd.DataFrame([{**{"timestamp": datetime.now().isoformat()}, **row}])
    if path.exists():
        old = pd.read_csv(path, encoding="utf-8-sig")
        df = pd.concat([old, df], ignore_index=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def _rate_for_level(policy: dict, market_level: str) -> float:
    value = policy.get("trailing_stop_rate", 0.006)
    if isinstance(value, dict):
        return float(value.get(market_level, value.get("normal", 0.006)))
    return float(value)


def get_trailing_stop_price(position: dict, market_level: str, policy: dict) -> float:
    high = float(position.get("trailing_high_price") or position.get("current_price") or position.get("avg_price") or 0)
    rate = _rate_for_level(policy, market_level)
    return float(adjust_price_to_tick(round(high * (1 - rate)), side="sell", method="ceil"))


def update_trailing_state(position: dict, current_price: float, policy: dict, market_level: str) -> dict:
    pos = dict(position)
    now = datetime.now().isoformat()
    rate = _rate_for_level(policy, market_level)
    previous_high = float(pos.get("trailing_high_price") or 0)
    high = max(previous_high, float(current_price))
    pos["trailing_active"] = True
    pos["trailing_high_price"] = high
    pos["trailing_stop_rate"] = rate
    pos["trailing_stop_price"] = get_trailing_stop_price(pos, market_level, policy)
    pos["last_trailing_update_at"] = now
    _append_event({
        "stock_code": pos.get("stock_code", ""),
        "market_level": market_level,
        "current_price": current_price,
        "trailing_high_price": high,
        "trailing_stop_price": pos["trailing_stop_price"],
        "trailing_stop_rate": rate,
        "event": "update",
    })
    return pos


def should_trigger_trailing_stop(position: dict, current_price: float, policy: dict, market_level: str) -> dict:
    pos = update_trailing_state(position, current_price, policy, market_level)
    stop_price = float(pos.get("trailing_stop_price") or 0)
    triggered = bool(stop_price > 0 and float(current_price) <= stop_price)
    if triggered:
        _append_event({
            "stock_code": pos.get("stock_code", ""),
            "market_level": market_level,
            "current_price": current_price,
            "trailing_high_price": pos.get("trailing_high_price", 0),
            "trailing_stop_price": stop_price,
            "event": "trigger",
        })
    return {"triggered": triggered, "position": pos, "trailing_stop_price": stop_price}
