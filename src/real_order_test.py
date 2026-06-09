"""REAL single-stock order dry-run/execute tool.

This tool never performs bulk orders. It only supports one selected stock and
requires SafetyGate REAL single-test conditions before --execute can place an
order.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from kis_api import KISApiClient
from price_tick import adjust_price_to_tick, get_tick_size
from real_order_readiness_check import _real_api_config_path, run_readiness_check
from safety_gate import SafetyGate
from utils import ensure_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _normalize_code(code: str) -> str:
    return str(code).replace(".0", "").strip().zfill(6)


def _save_result(row: dict) -> str:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    path = PROJECT_ROOT / "reports" / f"real_order_test_{datetime.now().strftime('%Y%m%d')}.csv"
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return str(path)


def _lookup_price(stock_code: str, config_path: str) -> dict:
    tmp_path = _real_api_config_path(config_path)
    try:
        api = KISApiClient(tmp_path, gate=SafetyGate(tmp_path, runtime_mode="real"))
        return api.get_current_price(stock_code)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def run_real_order_test(
    stock_code: str,
    quantity: int = 1,
    price: int = 0,
    execute: bool = False,
    config_path: str = "config.yaml",
) -> dict:
    code = _normalize_code(stock_code)
    readiness = run_readiness_check(config_path=config_path, call_real_api=True)
    info = _lookup_price(code, config_path)
    current_price = int(info.get("current_price", 0) or 0)
    stock_name = info.get("stock_name", code)
    order_price = int(price)
    if order_price <= 0:
        order_price = adjust_price_to_tick(current_price, side="buy", method="floor")
    amount = int(order_price * quantity)
    row = {
        "timestamp": datetime.now().isoformat(),
        "stock_code": code,
        "stock_name": stock_name,
        "quantity": int(quantity),
        "current_price": current_price,
        "order_price": order_price,
        "order_amount": amount,
        "dry_run": not execute,
        "requested_mode": "real",
        "resolved_mode": "",
        "api_called": False,
        "real_order_called": False,
        "mock_order_called": False,
        "order_no": "",
        "rt_cd": "",
        "msg": "",
        "rejected_reason": "",
        "readiness_verdict": readiness.get("verdict", "NOT_READY"),
        "missing_conditions": ", ".join(readiness.get("missing_conditions", [])),
    }

    gate = SafetyGate(config_path, runtime_mode="real")
    row["resolved_mode"] = gate.mode
    try:
        gate.assert_can_place_real_single_order(amount=amount, quantity=int(quantity), order_type="limit")
    except Exception as ex:
        row["rejected_reason"] = str(ex)
        path = _save_result(row)
        row["report_path"] = path
        return {"success": False if execute else True, "preview": row, "readiness": readiness, "report_path": path}

    if not execute:
        row["msg"] = "dry-run only; no real order was sent"
        path = _save_result(row)
        row["report_path"] = path
        return {"success": True, "preview": row, "readiness": readiness, "report_path": path}

    api = KISApiClient(config_path, gate=gate)
    row["api_called"] = True
    row["real_order_called"] = True
    row["mock_order_called"] = False
    try:
        resp = api.place_cash_buy_order(code, int(quantity), order_price, "limit")
        row["rt_cd"] = resp.get("rt_cd", "")
        row["msg"] = resp.get("msg1", resp.get("raw_msg", ""))
        row["order_no"] = resp.get("output", {}).get("ODNO", "")
        if row["rt_cd"] != "0":
            row["rejected_reason"] = f"API rejected: {row['rt_cd']} {row['msg']}"
    except Exception as ex:
        row["rejected_reason"] = str(ex)
    path = _save_result(row)
    row["report_path"] = path
    return {"success": row["rt_cd"] == "0", "result": row, "readiness": readiness, "report_path": path}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--price", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    execute = bool(args.execute and not args.dry_run)
    result = run_real_order_test(args.stock_code, args.quantity, args.price, execute, args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if execute and not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
