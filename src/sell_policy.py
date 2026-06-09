"""Per-position sell policy evaluation."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from price_tick import adjust_price_to_tick
from trailing_stop_manager import should_trigger_trailing_stop, update_trailing_state
from utils import ensure_dir, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _cfg(config: dict | None = None) -> dict:
    return config or load_config()


def list_sell_policies(config: dict | None = None) -> list[dict]:
    cfg = _cfg(config)
    policies = cfg.get("sell_policy", {}).get("policies", {})
    return [{"id": key, **value} for key, value in policies.items()]


def get_sell_policy(policy_id: str, config: dict | None = None) -> dict:
    cfg = _cfg(config)
    root = cfg.get("sell_policy", {})
    policies = root.get("policies", {})
    default_id = root.get("default_policy", "fixed_2pct")
    pid = policy_id if policy_id in policies else default_id
    policy = dict(policies.get(pid, {}))
    policy["id"] = pid
    policy.setdefault("name", pid)
    policy.setdefault("take_profit_rate", 0.02)
    policy.setdefault("stop_loss_rate", -0.03)
    return policy


def validate_sell_policy(policy_id: str) -> bool:
    cfg = load_config()
    return policy_id in cfg.get("sell_policy", {}).get("policies", {})


def _force_exit_due(policy: dict, now: datetime) -> bool:
    if not bool(policy.get("force_exit_enabled", False)):
        return False
    try:
        h, m = map(int, str(policy.get("force_exit_time", "15:15")).split(":"))
        return now.time() >= time(h, m)
    except Exception:
        return False


def _qty(position: dict, ratio: float) -> int:
    quantity = int(position.get("quantity", 0) or 0)
    if ratio >= 1:
        return quantity
    return max(1, int(quantity * ratio)) if quantity > 0 else 0


def apply_sell_policy_to_position(position: dict, policy_id: str, config: dict | None = None) -> dict:
    policy = get_sell_policy(policy_id, config)
    pos = dict(position)
    avg_price = float(pos.get("avg_price") or pos.get("entry_price") or 0)
    tp_rate = float(policy.get("take_profit_rate", 0.02))
    sl_rate = float(policy.get("stop_loss_rate", -0.03))
    pos.update({
        "sell_policy_id": policy["id"],
        "sell_policy_name": policy.get("name", policy["id"]),
        "take_profit_rate": tp_rate,
        "stop_loss_rate": sl_rate,
        "auto_take_profit_enabled": not bool(policy.get("manual_only", False)) and bool(policy.get("auto_take_profit_enabled", True)),
        "auto_stop_loss_enabled": not bool(policy.get("manual_only", False)) and bool(policy.get("auto_stop_loss_enabled", True)),
        "trailing_enabled": bool(policy.get("trailing_enabled", False)),
        "manual_only": bool(policy.get("manual_only", False)),
        "target_price": adjust_price_to_tick(round(avg_price * (1 + tp_rate)), side="sell", method="ceil") if avg_price else 0,
        "stop_loss_price": adjust_price_to_tick(round(avg_price * (1 + sl_rate)), side="sell", method="ceil") if avg_price else 0,
        "trailing_active": bool(pos.get("trailing_active", False)),
        "trailing_high_price": float(pos.get("trailing_high_price", 0) or 0),
        "trailing_stop_price": float(pos.get("trailing_stop_price", 0) or 0),
        "first_take_profit_done": bool(pos.get("first_take_profit_done", False)),
        "additional_take_profit_done": bool(pos.get("additional_take_profit_done", False)),
    })
    return pos


def _base_response(position: dict, policy: dict) -> dict:
    return {
        "should_sell": False,
        "sell_reason": None,
        "sell_quantity_ratio": 0.0,
        "sell_quantity": 0,
        "alert_only": False,
        "message": "",
        "sell_policy_id": policy["id"],
        "sell_policy_name": policy.get("name", policy["id"]),
        "updated_position": dict(position),
    }


def should_auto_sell(position: dict, current_price: float, market_strength: dict | None = None, now: Any = None) -> dict:
    current = now if isinstance(now, datetime) else datetime.now()
    policy = get_sell_policy(str(position.get("sell_policy_id") or ""))
    pos = apply_sell_policy_to_position(position, policy["id"])
    pos["current_price"] = float(current_price)
    avg_price = float(pos.get("avg_price") or pos.get("entry_price") or 0)
    quantity = int(pos.get("quantity", 0) or 0)
    if avg_price <= 0 or quantity <= 0:
        result = _base_response(pos, policy)
        result["message"] = "invalid position price or quantity"
        return result

    tp_rate = float(policy.get("take_profit_rate", 0.02))
    sl_rate = float(policy.get("stop_loss_rate", -0.03))
    target_price = float(pos.get("target_price") or avg_price * (1 + tp_rate))
    stop_price = float(pos.get("stop_loss_price") or avg_price * (1 + sl_rate))
    pnl_rate = float(current_price) / avg_price - 1
    result = _base_response(pos, policy)

    if policy["id"] == "manual_hold":
        if current_price >= target_price:
            result.update({
                "alert_only": True,
                "sell_reason": "MANUAL_HOLD_ALERT",
                "message": "+2% 도달했지만 수동보유 정책으로 자동매도하지 않습니다.",
            })
        elif current_price <= stop_price:
            result.update({
                "alert_only": True,
                "sell_reason": "MANUAL_HOLD_ALERT",
                "message": "-3% 손절 구간이지만 수동보유 정책으로 자동매도하지 않습니다.",
            })
        return result

    if current_price <= stop_price and bool(policy.get("auto_stop_loss_enabled", True)):
        result.update({
            "should_sell": True,
            "sell_reason": "STOP_LOSS",
            "sell_quantity_ratio": 1.0,
            "sell_quantity": quantity,
            "message": "stop loss reached",
        })
        return result

    if _force_exit_due(policy, current):
        result.update({
            "should_sell": True,
            "sell_reason": "FORCE_EXIT",
            "sell_quantity_ratio": 1.0,
            "sell_quantity": quantity,
            "message": "force exit time reached",
        })
        return result

    if policy["id"] == "fixed_2pct":
        if current_price >= target_price:
            result.update({
                "should_sell": True,
                "sell_reason": "TAKE_PROFIT",
                "sell_quantity_ratio": 1.0,
                "sell_quantity": quantity,
                "message": "+2% target reached; sell all",
            })
        return result

    if policy["id"] == "market_strength_trailing":
        market_level = str((market_strength or {}).get("level", "normal"))
        if pnl_rate < float(policy.get("trailing_start_rate", tp_rate)):
            return result

        if not bool(pos.get("first_take_profit_done", False)):
            ratio_map = policy.get("first_take_profit_sell_ratio", {})
            ratio = float(ratio_map.get(market_level, ratio_map.get("normal", 0.7)))
            pos["first_take_profit_done"] = True
            pos = update_trailing_state(pos, current_price, policy, market_level)
            result["updated_position"] = pos
            if ratio > 0:
                result.update({
                    "should_sell": True,
                    "sell_reason": "TAKE_PROFIT_PARTIAL",
                    "sell_quantity_ratio": ratio,
                    "sell_quantity": _qty(pos, ratio),
                    "message": f"+2% reached; {market_level} market partial sell ratio={ratio}",
                })
            else:
                result.update({
                    "alert_only": True,
                    "sell_reason": "TRAILING_HOLD",
                    "message": "+2% reached; very strong market, trailing hold",
                })
            return result

        if (
            pnl_rate >= float(policy.get("additional_take_profit_rate", 0.035))
            and not bool(pos.get("additional_take_profit_done", False))
        ):
            ratio = float(policy.get("additional_take_profit_sell_ratio", 0.3))
            pos["additional_take_profit_done"] = True
            result.update({
                "should_sell": True,
                "sell_reason": "ADDITIONAL_TAKE_PROFIT",
                "sell_quantity_ratio": ratio,
                "sell_quantity": _qty(pos, ratio),
                "message": "additional take profit reached",
                "updated_position": pos,
            })
            return result

        trail = should_trigger_trailing_stop(pos, current_price, policy, market_level)
        result["updated_position"] = trail["position"]
        if trail["triggered"]:
            result.update({
                "should_sell": True,
                "sell_reason": "TRAILING_STOP",
                "sell_quantity_ratio": 1.0,
                "sell_quantity": quantity,
                "message": "trailing stop triggered",
            })
        return result

    return result


def save_sell_policy_event(row: dict) -> str:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    path = PROJECT_ROOT / "reports" / f"sell_policy_events_{datetime.now().strftime('%Y%m%d')}.csv"
    df = pd.DataFrame([{**{"timestamp": datetime.now().isoformat()}, **row}])
    if path.exists():
        old = pd.read_csv(path, encoding="utf-8-sig")
        df = pd.concat([old, df], ignore_index=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)
