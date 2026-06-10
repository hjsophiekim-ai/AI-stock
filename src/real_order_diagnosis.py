"""Diagnose REAL single-order readiness without placing an order."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(__file__))

from kis_api import KISApiClient
from price_tick import adjust_price_to_tick, get_tick_size
from real_order_utils import (
    classify_order_error,
    normalize_stock_code,
    save_diagnosis_report,
)
from real_order_verify import run_real_order_verify
from safety_gate import SafetyGate
from trading_calendar import SESSION_REGULAR
from utils import load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_real_order_diagnosis(
    stock_code: str,
    quantity: int = 1,
    price: int = 0,
    config_path: str = "config.yaml",
) -> Dict[str, Any]:
    cfg = load_config(config_path)
    code = normalize_stock_code(stock_code)
    gate = SafetyGate(config_path, runtime_mode="real")
    api = KISApiClient(config_path, gate=gate)
    started = datetime.now()

    result: Dict[str, Any] = {
        "run_at": started.isoformat(),
        "stock_code": code,
        "quantity": int(quantity),
        "requested_mode": "real",
        "resolved_mode": gate.mode,
        "real_token_ok": False,
        "real_balance_ok": False,
        "orderable_cash": 0,
        "current_price": 0,
        "tick_adjusted_order_price": 0,
        "tick_size": 0,
        "order_amount": 0,
        "max_single_test_amount": int(
            cfg.get("real_trade", {}).get(
                "max_single_test_amount",
                cfg.get("safety", {}).get("max_real_test_order_amount", 10_000),
            )
        ),
        "tr_id": "",
        "ord_dvsn": "",
        "hashkey_generation_ok": False,
        "payload_validation_ok": False,
        "payload_errors": [],
        "dry_run_payload": {},
        "recent_order_check": {},
        "error_category": "",
        "verdict": "NOT_READY",
        "actual_order_sent": False,
    }

    try:
        token = api.auth.get_access_token()
        result["real_token_ok"] = bool(token)
    except Exception as exc:
        result["token_error"] = str(exc)

    try:
        cash = int(float(api.get_orderable_cash() or 0))
        result["orderable_cash"] = cash
        result["real_balance_ok"] = cash >= 0
    except Exception as exc:
        result["balance_error"] = str(exc)

    try:
        quote = api.get_current_price(code)
        current_price = int(quote.get("current_price", 0) or 0)
        result["current_price"] = current_price
    except Exception as exc:
        result["price_error"] = str(exc)
        current_price = 0

    order_price = int(price or 0)
    if order_price <= 0 and current_price > 0:
        order_price = adjust_price_to_tick(current_price, side="buy", method="floor")
    result["tick_adjusted_order_price"] = order_price
    result["tick_size"] = get_tick_size(order_price) if order_price > 0 else 0
    result["order_amount"] = int(order_price * int(quantity))

    try:
        request = api.build_cash_order_request(
            code,
            int(quantity),
            order_price,
            side="buy",
            order_type="limit",
            order_session=SESSION_REGULAR,
            include_hashkey=True,
        )
        result["tr_id"] = request.get("resolved", {}).get("tr_id", "")
        result["ord_dvsn"] = request.get("body", {}).get("ORD_DVSN", "")
        result["hashkey_generation_ok"] = request.get("hashkey_ready") is True
        result["payload_validation_ok"] = bool(request.get("payload_validation_ok"))
        result["payload_errors"] = request.get("payload_errors", [])
        result["dry_run_payload"] = request.get("preview", {})
        if request.get("hashkey_ready") is False:
            result["hashkey_error"] = request.get("headers", {}).get("hashkey_error", "")
    except Exception as exc:
        result["payload_error"] = str(exc)

    try:
        result["recent_order_check"] = run_real_order_verify(stock_code=code, config_path=config_path)
    except Exception as exc:
        result["recent_order_check"] = {"success": False, "message": str(exc)}

    try:
        gate.assert_can_place_real_single_order(
            amount=int(result["order_amount"]),
            quantity=int(quantity),
            order_type="limit",
        )
        safety_ok = True
    except Exception as exc:
        safety_ok = False
        result["safety_gate_error"] = str(exc)

    result["error_category"] = classify_order_error(
        payload_errors=result.get("payload_errors", []),
        hashkey_ready=result.get("hashkey_generation_ok"),
        orderable_cash=int(result.get("orderable_cash", 0) or 0),
        order_amount=int(result.get("order_amount", 0) or 0),
    )

    ready = all([
        result["resolved_mode"] == "REAL",
        result["real_token_ok"],
        result["real_balance_ok"],
        result["current_price"] > 0,
        result["order_amount"] > 0,
        result["order_amount"] <= result["orderable_cash"],
        result["order_amount"] <= result["max_single_test_amount"],
        result["tr_id"] == "TTTC0802U",
        result["ord_dvsn"] == "00",
        result["hashkey_generation_ok"],
        result["payload_validation_ok"],
        safety_ok,
    ])
    result["verdict"] = "READY_FOR_REAL_SINGLE_TEST_DRY_RUN" if ready else "NOT_READY"
    paths = save_diagnosis_report(result, prefix="real_order_diagnosis")
    result.update(paths)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--price", type=int, default=0)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    result = run_real_order_diagnosis(args.stock_code, args.quantity, args.price, args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("verdict") == "NOT_READY":
        sys.exit(1)


if __name__ == "__main__":
    main()
