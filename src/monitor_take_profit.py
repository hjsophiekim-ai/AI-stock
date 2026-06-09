"""KIS-position based +2% take-profit monitor.

The monitor uses broker positions first, syncs them into positions.json, and
only then sends sell orders. This avoids missing positions that exist in the
KIS MOCK account but are absent or stale in local storage.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from kis_api import KISApiClient
from order_manager import OrderManager
from position_manager import PositionManager
from price_tick import adjust_price_to_tick
from safety_gate import SafetyGate, TRADE_MODE_PAPER
from strategy_config import get_strategy
from market_strength import get_market_strength
from sell_policy import get_sell_policy, save_sell_policy_event, should_auto_sell
from utils import ensure_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _report_path() -> Path:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    return PROJECT_ROOT / "reports" / f"take_profit_monitor_{datetime.now().strftime('%Y%m%d')}.csv"


def _sell_policy_report_path() -> Path:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    return PROJECT_ROOT / "reports" / f"sell_policy_events_{datetime.now().strftime('%Y%m%d')}.csv"


def _save_rows(rows: list[dict]) -> str:
    path = _report_path()
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


def _save_policy_rows(rows: list[dict]) -> str:
    path = _sell_policy_report_path()
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


def _sync_broker_df_to_local(
    broker_df: pd.DataFrame,
    strategy_id: str = "morning_0930",
    config_path: str = "config.yaml",
) -> None:
    pm = PositionManager(config_path)
    strategy_cfg = get_strategy(strategy_id)
    now = datetime.now().isoformat()
    for _, row in broker_df.iterrows():
        code = str(row.get("stock_code", "")).zfill(6)
        qty = int(row.get("quantity", 0) or 0)
        avg_price = float(row.get("avg_price", 0) or 0)
        if not code or qty <= 0 or avg_price <= 0:
            continue
        current_price = float(row.get("current_price", avg_price) or avg_price)
        existing = pm.get_position(code)
        policy_id = getattr(existing, "sell_policy_id", "") if existing else ""
        pos = pm.update_position_after_buy(
            stock_code=code,
            stock_name=str(row.get("stock_name", code)),
            quantity=qty,
            entry_price=avg_price,
            entry_time=datetime.now(),
            strategy_id=(getattr(existing, "strategy_id", "") if existing else "") or strategy_cfg["id"],
            strategy_name=(getattr(existing, "strategy_name", "") if existing else "") or strategy_cfg["name"],
            take_profit_rate=strategy_cfg["take_profit_rate"],
            stop_loss_rate=strategy_cfg["stop_loss_rate"],
            allowed_sell_sessions=strategy_cfg["allowed_sell_sessions"],
            source="broker_synced",
            replace_existing=True,
            sell_policy_id=policy_id,
        )
        pos.current_price = current_price
        pos.avg_price = avg_price
        pos.profit_loss_rate = float(row.get("pnl_rate", 0) or 0)
        pos.target_price = adjust_price_to_tick(round(avg_price * 1.02), side="sell", method="ceil")
        pos.stop_price = adjust_price_to_tick(round(avg_price * 0.97), side="sell", method="ceil")
        pos.stop_loss_price = pos.stop_price
        pos.broker_synced_at = now
    pm.save_local_positions()


def _position_to_dict(pos) -> dict:
    if isinstance(pos, dict):
        return dict(pos)
    return dict(getattr(pos, "__dict__", {}))


def find_take_profit_targets(
    mode: str = "mock",
    take_profit_rate: float = 0.02,
    strategy_id: Optional[str] = None,
    config_path: str = "config.yaml",
) -> pd.DataFrame:
    """Return positions whose current policy marks them as sell targets."""
    gate = SafetyGate(config_path, runtime_mode=mode)
    if mode == "mock" and gate.mode == TRADE_MODE_PAPER:
        raise RuntimeError("mode=mock resolved to PAPER; MOCK sell monitor must not fall back to PAPER")
    broker_df = pd.DataFrame()
    if gate.mode != TRADE_MODE_PAPER:
        api = KISApiClient(config_path, gate=gate)
        broker_df = api.get_positions()
        if broker_df is None or broker_df.empty:
            _save_rows([])
            return pd.DataFrame()
        _sync_broker_df_to_local(broker_df, strategy_id or "morning_0930", config_path)

    pm = PositionManager(config_path)
    market = get_market_strength()
    rows = []
    if gate.mode == TRADE_MODE_PAPER:
        iterable = [_position_to_dict(p) for p in pm.get_all_positions().values()]
    else:
        iterable = []
        for _, row in broker_df.iterrows():
            code = str(row.get("stock_code", "")).zfill(6)
            pos = pm.get_position(code)
            pdata = _position_to_dict(pos) if pos else {}
            pdata.update(row.to_dict())
            pdata["stock_code"] = code
            pdata.setdefault("stock_name", row.get("stock_name", code))
            pdata["current_price"] = float(row.get("current_price", row.get("avg_price", 0)) or 0)
            pdata["avg_price"] = float(row.get("avg_price", pdata.get("avg_price", 0)) or 0)
            pdata["entry_price"] = pdata.get("avg_price") or pdata.get("entry_price", 0)
            iterable.append(pdata)

    for pdata in iterable:
        code = str(pdata.get("stock_code", "")).zfill(6)
        pos = pm.get_position(code)
        if strategy_id and pos and getattr(pos, "strategy_id", "") != strategy_id:
            continue
        qty = int(pdata.get("quantity", 0) or 0)
        avg_price = float(pdata.get("avg_price") or pdata.get("entry_price") or 0)
        current_price = float(pdata.get("current_price", avg_price) or avg_price)
        pnl_rate_raw = float(pdata.get("pnl_rate", pdata.get("profit_loss_rate", 0)) or 0)
        pnl_rate_pct = pnl_rate_raw if abs(pnl_rate_raw) > 1 else pnl_rate_raw * 100
        if pnl_rate_pct == 0 and avg_price:
            pnl_rate_pct = (current_price / avg_price - 1) * 100
        policy_result = should_auto_sell(pdata, current_price, market)
        target_price = adjust_price_to_tick(round(avg_price * (1 + take_profit_rate)), side="sell", method="ceil")
        stop_price = adjust_price_to_tick(round(avg_price * 0.97), side="sell", method="ceil")
        target_reached = bool((pnl_rate_pct >= take_profit_rate * 100) or (current_price >= target_price))
        updated = policy_result.get("updated_position", {})
        if pos and updated:
            for key in [
                "trailing_active", "trailing_high_price", "trailing_stop_rate", "trailing_stop_price",
                "first_take_profit_done", "additional_take_profit_done", "last_trailing_update_at",
                "current_price",
            ]:
                if key in updated:
                    setattr(pos, key, updated[key])
            pm.save_local_positions()
        rows.append({
            "timestamp": datetime.now().isoformat(),
            "requested_mode": mode,
            "resolved_mode": gate.mode,
            "stock_code": code,
            "stock_name": pdata.get("stock_name", ""),
            "quantity": qty,
            "avg_price": avg_price,
            "current_price": current_price,
            "target_price": target_price,
            "stop_loss_price": stop_price,
            "trailing_high_price": updated.get("trailing_high_price", getattr(pos, "trailing_high_price", 0) if pos else 0),
            "trailing_stop_price": updated.get("trailing_stop_price", getattr(pos, "trailing_stop_price", 0) if pos else 0),
            "profit_loss": (current_price - avg_price) * qty,
            "profit_loss_rate": round(pnl_rate_pct, 4),
            "target_reached": target_reached,
            "sell_target": bool(policy_result.get("should_sell")),
            "alert_only": bool(policy_result.get("alert_only")),
            "sell_reason": policy_result.get("sell_reason"),
            "sell_quantity": policy_result.get("sell_quantity", 0),
            "sell_quantity_ratio": policy_result.get("sell_quantity_ratio", 0),
            "sell_policy_id": policy_result.get("sell_policy_id", pdata.get("sell_policy_id", "")),
            "sell_policy_name": policy_result.get("sell_policy_name", pdata.get("sell_policy_name", "")),
            "market_strength_level": market.get("level", "normal"),
            "policy_message": policy_result.get("message", ""),
            "strategy_id": getattr(pos, "strategy_id", "") if pos else "",
            "strategy_name": getattr(pos, "strategy_name", "") if pos else "",
            "status": "SELL" if policy_result.get("should_sell") else ("ALERT" if policy_result.get("alert_only") else "HOLD"),
        })
    result = pd.DataFrame(rows)
    _save_rows(rows)
    _save_policy_rows(rows)
    return result[result["sell_target"] == True].reset_index(drop=True) if not result.empty else result


def execute_take_profit_sells(
    mode: str = "mock",
    strategy_id: Optional[str] = None,
    take_profit_rate: float = 0.02,
    config_path: str = "config.yaml",
) -> dict:
    gate = SafetyGate(config_path, runtime_mode=mode)
    if mode == "mock" and gate.mode == TRADE_MODE_PAPER:
        raise RuntimeError("mode=mock resolved to PAPER; refusing to sell")
    targets = find_take_profit_targets(mode, take_profit_rate, strategy_id, config_path)
    mgr = OrderManager(config_path, gate=gate)
    rows = []
    sold = 0
    for _, target in targets.iterrows():
        code = str(target["stock_code"]).zfill(6)
        qty = int(target.get("sell_quantity", 0) or 0)
        reason = str(target.get("sell_reason", "take_profit")).lower()
        if qty > 0 and qty < int(target.get("quantity", 0) or 0):
            result = mgr.sell_stock(
                code,
                str(target.get("stock_name", code)),
                qty,
                target_price=int(target.get("current_price", 0) or 0),
                reason=reason,
            )
        else:
            result = mgr.sell_all_position(code, reason=reason)
        if result.get("success"):
            sold += 1
        rows.append({
            **target.to_dict(),
            "action": "sell",
            "success": bool(result.get("success")),
            "order_no": result.get("order_no", ""),
            "api_called": result.get("api_called", gate.mode != TRADE_MODE_PAPER),
            "mock_order_called": result.get("mock_order_called", gate.mode == "MOCK"),
            "real_order_called": result.get("real_order_called", gate.mode == "REAL"),
            "rejected_reason": result.get("rejected_reason", result.get("reason", "")),
        })
        save_sell_policy_event(rows[-1])
    if not rows and not targets.empty:
        rows = targets.to_dict("records")
    report_path = _save_rows(rows if rows else targets.to_dict("records"))
    return {
        "success": True,
        "mode": mode,
        "resolved_mode": gate.mode,
        "checked": int(len(targets)),
        "sold": sold,
        "report_path": report_path,
        "rows": rows,
    }


def monitor_once(
    mode: str = "mock",
    strategy: Optional[str] = None,
    config_path: str = "config.yaml",
    sync_broker: bool = True,
    stop_loss: bool = False,
    sell_now: bool = False,
    sell_policy_id: Optional[str] = None,
    policy_override: bool = False,
) -> dict:
    if sell_policy_id and policy_override:
        pm = PositionManager(config_path)
        policy = get_sell_policy(sell_policy_id)
        for pos in pm.get_all_positions().values():
            pos.sell_policy_id = policy["id"]
            pos.sell_policy_name = policy["name"]
        pm.save_local_positions()
    if sell_now:
        return execute_take_profit_sells(mode=mode, strategy_id=strategy, config_path=config_path)
    targets = find_take_profit_targets(mode=mode, strategy_id=strategy, config_path=config_path)
    return {
        "success": True,
        "mode": mode,
        "checked": int(len(targets)),
        "targets": int(len(targets)),
        "sold": 0,
        "report_path": str(_report_path()),
        "rows": targets.to_dict("records") if isinstance(targets, pd.DataFrame) else [],
    }


def run_loop(mode: str, strategy: Optional[str], sleep_sec: float, config_path: str, sell_now: bool = True, sell_policy_id: Optional[str] = None, policy_override: bool = False) -> None:
    while True:
        result = monitor_once(mode=mode, strategy=strategy, config_path=config_path, sell_now=sell_now, sell_policy_id=sell_policy_id, policy_override=policy_override)
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False))
        time.sleep(sleep_sec)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="mock", choices=["paper", "mock", "real"])
    parser.add_argument("--strategy", default=None, choices=["morning_0930", "afternoon_1500"])
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--sleep", type=float, default=5.0)
    parser.add_argument("--sync-broker", action="store_true")
    parser.add_argument("--stop-loss", action="store_true")
    parser.add_argument("--sell-now", action="store_true")
    parser.add_argument("--sell-policy", default=None, choices=["fixed_2pct", "market_strength_trailing", "manual_hold"])
    parser.add_argument("--policy-override", action="store_true")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    if args.loop:
        run_loop(args.mode, args.strategy, args.sleep, args.config, sell_now=True, sell_policy_id=args.sell_policy, policy_override=args.policy_override)
    else:
        print(json.dumps(
            monitor_once(args.mode, args.strategy, args.config, args.sync_broker, args.stop_loss, args.sell_now, args.sell_policy, args.policy_override),
            ensure_ascii=False,
            indent=2,
        ))


if __name__ == "__main__":
    main()
