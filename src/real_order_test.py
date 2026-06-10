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
from real_order_verify import run_real_order_verify
from safety_gate import SafetyGate
from trading_calendar import SESSION_REGULAR
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
        "http_status_code": "",
        "response_text": "",
        "response_json": "",
        "request_url": "",
        "tr_id": "",
        "ord_dvsn": "",
        "hashkey_ready": "",
        "payload_validation_ok": "",
        "payload_errors": "",
        "error_category": "",
        "verify_order_found": "",
        "verify_csv_path": "",
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

    api = KISApiClient(config_path, gate=gate)
    request = api.build_cash_order_request(
        code,
        int(quantity),
        order_price,
        "buy",
        "limit",
        order_session=SESSION_REGULAR,
        include_hashkey=True,
    )
    preview = request.get("preview", {})
    row["request_url"] = request.get("request_url", "")
    row["tr_id"] = request.get("resolved", {}).get("tr_id", "")
    row["ord_dvsn"] = request.get("body", {}).get("ORD_DVSN", "")
    row["hashkey_ready"] = "OK" if request.get("hashkey_ready") is True else "FAIL"
    row["payload_validation_ok"] = "OK" if request.get("payload_validation_ok") else "FAIL"
    row["payload_errors"] = "; ".join(request.get("payload_errors", []))

    if not execute:
        row["msg"] = "dry-run only; no real order was sent"
        path = _save_result(row)
        row["report_path"] = path
        return {
            "success": True,
            "preview": row,
            "readiness": readiness,
            "dry_run_payload": preview,
            "report_path": path,
        }

    row["api_called"] = True
    row["real_order_called"] = True
    row["mock_order_called"] = False
    try:
        resp = api.place_cash_buy_order(code, int(quantity), order_price, "limit", order_session=SESSION_REGULAR)
        row["rt_cd"] = resp.get("rt_cd", "")
        row["msg"] = resp.get("msg1", resp.get("raw_msg", ""))
        row["order_no"] = resp.get("output", {}).get("ODNO", "")
        row["http_status_code"] = resp.get("http_status_code", "")
        row["response_text"] = str(resp.get("response_text", ""))[:2000]
        row["response_json"] = json.dumps(resp.get("response_json", {}), ensure_ascii=False)[:2000]
        row["error_category"] = resp.get("error_category", "")
        if row["rt_cd"] == "0" and not row["order_no"]:
            row["rejected_reason"] = "KIS response returned no order_no; real order receipt failed"
            row["error_category"] = row["error_category"] or "KIS_REAL_ORDER_REJECTED"
        elif row["rt_cd"] != "0":
            row["rejected_reason"] = f"API rejected: {row['rt_cd']} {row['msg']}"
    except Exception as ex:
        row["rejected_reason"] = str(ex)

    try:
        verify = run_real_order_verify(stock_code=code, order_no=row["order_no"], config_path=config_path)
        row["verify_order_found"] = bool(verify.get("order_found"))
        row["verify_csv_path"] = verify.get("csv_path", "")
    except Exception as ex:
        verify = {"success": False, "message": str(ex)}
        row["verify_order_found"] = False

    path = _save_result(row)
    row["report_path"] = path
    success = row["rt_cd"] == "0" and bool(row["order_no"])
    return {
        "success": success,
        "result": row,
        "readiness": readiness,
        "dry_run_payload": preview,
        "order_verify": verify,
        "report_path": path,
    }


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
