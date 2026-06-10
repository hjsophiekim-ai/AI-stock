"""Verify whether a REAL order was accepted by querying today's KIS orders."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(__file__))

from kis_api import KISApiClient
from real_order_utils import normalize_stock_code, save_verify_csv
from safety_gate import SafetyGate

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _row_from_kis(raw: Dict[str, Any]) -> Dict[str, Any]:
    order_no = raw.get("odno", raw.get("ODNO", raw.get("order_no", "")))
    stock_code = raw.get("pdno", raw.get("PDNO", raw.get("stock_code", "")))
    ordered_qty = raw.get("ord_qty", raw.get("ORD_QTY", raw.get("order_qty", 0)))
    filled_qty = raw.get("tot_ccld_qty", raw.get("CCLD_QTY", raw.get("filled_qty", 0)))
    order_price = raw.get("ord_unpr", raw.get("ORD_UNPR", raw.get("order_price", raw.get("filled_price", 0))))
    status = raw.get("ord_tmd", "") or raw.get("order_status", "")
    rejected_reason = raw.get("rjct_rson", "") or raw.get("msg1", "") or raw.get("rejected_reason", "")
    return {
        "order_found": bool(order_no or stock_code),
        "stock_code": str(stock_code),
        "order_no": str(order_no),
        "order_status": str(status),
        "ordered_qty": int(float(ordered_qty or 0)),
        "filled_qty": int(float(filled_qty or 0)),
        "order_price": int(float(order_price or 0)),
        "rejected_reason": str(rejected_reason),
    }


def run_real_order_verify(
    stock_code: str = "",
    order_no: str = "",
    config_path: str = "config.yaml",
) -> Dict[str, Any]:
    gate = SafetyGate(config_path, runtime_mode="real")
    api = KISApiClient(config_path, gate=gate)
    code = normalize_stock_code(stock_code) if stock_code else ""
    rows: List[Dict[str, Any]] = []
    raw_response: Dict[str, Any] = {}

    try:
        if order_no:
            raw_response = api.get_order_status(order_no)
            raw_rows = raw_response.get("output1", [])
        else:
            raw_response = api.get_order_status("")
            raw_rows = raw_response.get("output1", [])
    except Exception as exc:
        rows = [{
            "order_found": False,
            "stock_code": code,
            "order_no": order_no,
            "order_status": "QUERY_FAILED",
            "ordered_qty": 0,
            "filled_qty": 0,
            "order_price": 0,
            "rejected_reason": str(exc),
        }]
        csv_path = save_verify_csv(rows)
        return {
            "success": False,
            "run_at": datetime.now().isoformat(),
            "order_found": False,
            "stock_code": code,
            "order_no": order_no,
            "rows": rows,
            "csv_path": csv_path,
            "message": str(exc),
        }

    for raw in raw_rows or []:
        row = _row_from_kis(raw)
        if order_no and row.get("order_no") != str(order_no):
            continue
        if code and row.get("stock_code") != code:
            continue
        rows.append(row)

    if not rows:
        rows = [{
            "order_found": False,
            "stock_code": code,
            "order_no": order_no,
            "order_status": "NOT_FOUND",
            "ordered_qty": 0,
            "filled_qty": 0,
            "order_price": 0,
            "rejected_reason": raw_response.get("msg1", ""),
        }]

    csv_path = save_verify_csv(rows)
    return {
        "success": True,
        "run_at": datetime.now().isoformat(),
        "order_found": any(bool(r.get("order_found")) for r in rows),
        "stock_code": code,
        "order_no": order_no,
        "rows": rows,
        "csv_path": csv_path,
        "raw_rt_cd": raw_response.get("rt_cd", ""),
        "raw_msg": raw_response.get("msg1", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-code", default="")
    parser.add_argument("--order-no", default="")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    result = run_real_order_verify(args.stock_code, args.order_no, args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
