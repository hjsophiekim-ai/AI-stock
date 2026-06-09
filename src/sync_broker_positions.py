"""Synchronize KIS broker positions into data/positions.json."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from kis_api import KISApiClient
from position_manager import PositionManager
from price_tick import adjust_price_to_tick
from safety_gate import SafetyGate
from strategy_config import get_strategy
from utils import ensure_dir, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def sync_broker_positions(mode: str = "mock", strategy: str = "morning_0930", config_path: str = "config.yaml") -> dict:
    gate = SafetyGate(config_path, runtime_mode=mode)
    api = KISApiClient(config_path, gate=gate)
    broker_df = api.get_positions()
    pm = PositionManager(config_path)
    local_before = pm.get_all_positions()
    strategy_cfg = get_strategy(strategy)
    now = datetime.now().isoformat()

    ensure_dir(str(PROJECT_ROOT / "data"))
    ensure_dir(str(PROJECT_ROOT / "reports"))

    if broker_df is None:
        broker_df = pd.DataFrame()
    with open(PROJECT_ROOT / "data" / "broker_positions.json", "w", encoding="utf-8") as f:
        json.dump(broker_df.to_dict("records"), f, ensure_ascii=False, indent=2)

    diff_rows = []
    broker_codes = set()
    if not broker_df.empty:
        for _, row in broker_df.iterrows():
            code = str(row.get("stock_code", "")).zfill(6)
            broker_codes.add(code)
            qty = int(row.get("quantity", 0) or 0)
            avg_price = float(row.get("avg_price", 0) or 0)
            current_price = float(row.get("current_price", avg_price) or avg_price)
            existing = pm.get_position(code)
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
                source="broker",
                replace_existing=True,
                sell_policy_id=(getattr(existing, "sell_policy_id", "") if existing else ""),
            )
            pos.current_price = current_price
            pos.avg_price = avg_price
            pos.profit_loss_rate = float(row.get("pnl_rate", 0) or 0)
            pos.target_price = adjust_price_to_tick(round(avg_price * 1.02), side="sell", method="ceil")
            pos.stop_price = adjust_price_to_tick(round(avg_price * 0.97), side="sell", method="ceil")
            pos.stop_loss_price = pos.stop_price
            pos.broker_synced_at = now
            diff_rows.append({"stock_code": code, "status": "broker_and_local", "quantity": qty, "avg_price": avg_price})

    for code, pos in local_before.items():
        if code not in broker_codes:
            diff_rows.append({"stock_code": code, "status": "local_only", "quantity": pos.quantity, "avg_price": pos.entry_price})

    pm.save_local_positions()
    today = datetime.now().strftime("%Y%m%d")
    diff_path = PROJECT_ROOT / "reports" / f"account_position_diff_{today}.csv"
    pd.DataFrame(diff_rows).to_csv(diff_path, index=False, encoding="utf-8-sig")
    return {
        "success": True,
        "mode": mode,
        "resolved_mode": gate.mode,
        "broker_count": int(len(broker_df)),
        "local_count": int(len(pm.get_all_positions())),
        "diff_path": str(diff_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="mock", choices=["paper", "mock", "real"])
    parser.add_argument("--strategy", default="morning_0930", choices=["morning_0930", "afternoon_1500"])
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    print(json.dumps(sync_broker_positions(args.mode, args.strategy, args.config), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
