"""Helpers for REAL order diagnostics, masking, validation, and reports."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from price_tick import is_valid_tick_price
from utils import ensure_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ERROR_REAL_ORDER_500 = "REAL_ORDER_500_SERVER_ERROR"
ERROR_HASHKEY = "HASHKEY_MISSING_OR_INVALID"
ERROR_INVALID_PAYLOAD = "INVALID_ORDER_PAYLOAD"
ERROR_ACCOUNT = "ACCOUNT_OR_PRODUCT_CODE_ERROR"
ERROR_CASH = "ORDERABLE_CASH_INSUFFICIENT"
ERROR_PRICE_TICK = "PRICE_TICK_ERROR"
ERROR_REJECTED = "KIS_REAL_ORDER_REJECTED"

SENSITIVE_HEADER_KEYS = {"authorization", "appkey", "appsecret", "hashkey"}


def normalize_stock_code(stock_code: str) -> str:
    return str(stock_code).replace(".0", "").strip().zfill(6)


def mask_value(value: Any, visible: int = 4) -> str:
    text = "" if value is None else str(value)
    if not text:
        return ""
    if len(text) <= visible:
        return "*" * len(text)
    return text[:visible] + "*" * (len(text) - visible)


def mask_account(account_no: Any) -> str:
    text = "" if account_no is None else str(account_no)
    if len(text) <= 3:
        return "***" if text else ""
    show = 4 if len(text) >= 8 else 3
    return text[:show] + "*" * (len(text) - show)


def mask_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
    masked: Dict[str, Any] = {}
    for key, value in (headers or {}).items():
        if key.lower() in SENSITIVE_HEADER_KEYS:
            masked[key] = mask_value(value)
        else:
            masked[key] = value
    return masked


def mask_order_body(body: Dict[str, Any]) -> Dict[str, Any]:
    masked = dict(body or {})
    if "CANO" in masked:
        masked["CANO"] = mask_account(masked["CANO"])
    return masked


def classify_order_error(
    *,
    status_code: int | None = None,
    response_text: str = "",
    response_json: Dict[str, Any] | None = None,
    payload_errors: Iterable[str] | None = None,
    hashkey_ready: bool | None = None,
    orderable_cash: int | None = None,
    order_amount: int | None = None,
) -> str:
    payload_errors = list(payload_errors or [])
    text = response_text or ""
    data = response_json or {}
    msg = " ".join([text, str(data.get("msg1", "")), str(data.get("msg_cd", ""))])

    if status_code == 500:
        return ERROR_REAL_ORDER_500
    if hashkey_ready is False or any("hashkey" in e.lower() for e in payload_errors) or "hash" in msg.lower():
        return ERROR_HASHKEY
    if payload_errors:
        if any("CANO" in e or "ACNT_PRDT_CD" in e for e in payload_errors):
            return ERROR_ACCOUNT
        if any("tick" in e.lower() for e in payload_errors):
            return ERROR_PRICE_TICK
        return ERROR_INVALID_PAYLOAD
    if orderable_cash is not None and order_amount is not None and order_amount > orderable_cash:
        return ERROR_CASH
    if data.get("rt_cd") and data.get("rt_cd") != "0":
        return ERROR_REJECTED
    if status_code and status_code >= 400:
        return ERROR_REJECTED
    return ""


def validate_real_order_payload(body: Dict[str, Any]) -> Tuple[bool, list[str]]:
    errors: list[str] = []
    cano = str(body.get("CANO", "")).strip()
    product = str(body.get("ACNT_PRDT_CD", "")).strip()
    code = str(body.get("PDNO", "")).strip()
    ord_dvsn = str(body.get("ORD_DVSN", "")).strip()
    qty = str(body.get("ORD_QTY", "")).strip()
    price = str(body.get("ORD_UNPR", "")).strip()

    if not cano or not re.fullmatch(r"\d+", cano):
        errors.append("CANO must be numeric and non-empty")
    if not product or not re.fullmatch(r"\d{2}", product):
        errors.append("ACNT_PRDT_CD must be a 2-digit string")
    if not re.fullmatch(r"\d{6}", code):
        errors.append("PDNO must be a 6-digit stock code")
    if not ord_dvsn:
        errors.append("ORD_DVSN is required")
    if not qty.isdigit() or int(qty) <= 0:
        errors.append("ORD_QTY must be a positive numeric string")
    if not price.isdigit() or int(price) < 0:
        errors.append("ORD_UNPR must be a numeric string")
    if ord_dvsn == "00":
        if not price.isdigit() or int(price) <= 0:
            errors.append("ORD_UNPR must be positive for limit orders")
        elif not is_valid_tick_price(int(price)):
            errors.append("PRICE_TICK_ERROR: ORD_UNPR is not valid for KRX tick size")
    return not errors, errors


def make_order_preview(
    *,
    url: str,
    tr_id: str,
    body: Dict[str, Any],
    headers: Dict[str, Any] | None = None,
    hashkey_ready: bool | None = None,
) -> Dict[str, Any]:
    valid, errors = validate_real_order_payload(body)
    return {
        "request_url": url,
        "tr_id": tr_id,
        "ord_dvsn": body.get("ORD_DVSN", ""),
        "hashkey_ready": hashkey_ready,
        "payload_validation_ok": valid,
        "payload_errors": errors,
        "masked_headers": mask_headers(headers or {}),
        "masked_body": mask_order_body(body),
    }


def save_diagnosis_report(data: Dict[str, Any], prefix: str = "real_order_diagnosis") -> Dict[str, str]:
    reports = PROJECT_ROOT / "reports"
    ensure_dir(str(reports))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = reports / f"{prefix}_{ts}.txt"
    json_path = reports / f"{prefix}_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    lines = _format_text_report(data)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return {"txt_path": str(txt_path), "json_path": str(json_path)}


def save_verify_csv(rows: list[Dict[str, Any]], prefix: str = "real_order_verify") -> str:
    reports = PROJECT_ROOT / "reports"
    ensure_dir(str(reports))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = reports / f"{prefix}_{ts}.csv"
    fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else [
        "order_found", "stock_code", "order_no", "order_status", "ordered_qty",
        "filled_qty", "order_price", "rejected_reason",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return str(path)


def _format_text_report(data: Dict[str, Any]) -> list[str]:
    lines = [
        "REAL order diagnosis report",
        f"run_at: {data.get('run_at', '')}",
        f"verdict: {data.get('verdict', '')}",
        f"error_category: {data.get('error_category', '')}",
        "",
    ]
    for key, value in data.items():
        if key in {"response_text"}:
            text = str(value)
            lines.append(f"{key}: {text[:2000]}")
        elif key not in {"run_at", "verdict", "error_category"}:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}")
    return lines
