"""Monitor local positions and sell when the +2% take-profit target is reached."""

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
from safety_gate import SafetyGate, TRADE_MODE_PAPER
from utils import ensure_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def monitor_once(
    mode: str = "mock",
    strategy: Optional[str] = None,
    config_path: str = "config.yaml",
    sync_broker: bool = False,
    stop_loss: bool = False,
) -> dict:
    if sync_broker:
        from sync_broker_positions import sync_broker_positions
        sync_broker_positions(mode=mode, strategy=strategy or "morning_0930", config_path=config_path)

    gate = SafetyGate(config_path, runtime_mode=mode)
    pm = PositionManager(config_path)
    mgr = OrderManager(config_path, gate=gate)
    api = KISApiClient(config_path, gate=gate) if gate.mode != TRADE_MODE_PAPER else None
    rows = []
    sold = 0

    for code, pos in list(pm.get_all_positions().items()):
        if strategy and getattr(pos, "strategy_id", "") != strategy:
            continue
        if getattr(pos, "source", "local") == "local_only":
            rows.append({"stock_code": code, "action": "skip", "rejected_reason": "local_only"})
            continue

        current_price = float(getattr(pos, "current_price", 0) or getattr(pos, "entry_price", 0) or 0)
        if api is not None:
            try:
                info = api.get_current_price(code)
                current_price = float(info.get("current_price", current_price) or current_price)
            except Exception as ex:
                rows.append({"stock_code": code, "action": "skip", "rejected_reason": f"price lookup failed: {ex}"})
                continue

        target_price = float(getattr(pos, "target_price", 0) or float(pos.entry_price) * 1.02)
        stop_price = float(getattr(pos, "stop_loss_price", 0) or getattr(pos, "stop_price", 0) or float(pos.entry_price) * 0.97)
        action = "hold"
        reason = ""
        result = {}
        if current_price >= target_price:
            action = "sell"
            reason = "take_profit"
            result = mgr.sell_all_position(code, reason=reason)
        elif stop_loss and current_price <= stop_price:
            action = "sell"
            reason = "stop_loss"
            result = mgr.sell_all_position(code, reason=reason)

        if result.get("success"):
            sold += 1

        rows.append({
            "timestamp": datetime.now().isoformat(),
            "requested_mode": mode,
            "resolved_mode": gate.mode,
            "stock_code": code,
            "stock_name": getattr(pos, "stock_name", ""),
            "strategy_id": getattr(pos, "strategy_id", ""),
            "quantity": getattr(pos, "quantity", 0),
            "avg_price": getattr(pos, "avg_price", getattr(pos, "entry_price", 0)),
            "current_price": current_price,
            "target_price": target_price,
            "target_reached": current_price >= target_price,
            "stop_loss_price": stop_price,
            "action": action,
            "reason": reason,
            "success": bool(result.get("success", False)) if result else False,
            "order_no": result.get("order_no", "") if result else "",
            "mock_order_called": result.get("mock_order_called", gate.mode == "MOCK" and action == "sell"),
            "real_order_called": result.get("real_order_called", gate.mode == "REAL" and action == "sell"),
            "rejected_reason": result.get("rejected_reason", result.get("reason", "")) if result else "",
        })

    ensure_dir(str(PROJECT_ROOT / "reports"))
    today = datetime.now().strftime("%Y%m%d")
    report_path = PROJECT_ROOT / "reports" / f"take_profit_monitor_{today}.csv"
    pd.DataFrame(rows).to_csv(report_path, index=False, encoding="utf-8-sig")
    return {"success": True, "mode": mode, "resolved_mode": gate.mode, "checked": len(rows), "sold": sold, "report_path": str(report_path), "rows": rows}


def run_loop(mode: str, strategy: Optional[str], sleep_sec: float, config_path: str) -> None:
    while True:
        result = monitor_once(mode=mode, strategy=strategy, config_path=config_path)
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
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    if args.loop:
        run_loop(args.mode, args.strategy, args.sleep, args.config)
    else:
        print(json.dumps(monitor_once(args.mode, args.strategy, args.config, args.sync_broker, args.stop_loss), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
