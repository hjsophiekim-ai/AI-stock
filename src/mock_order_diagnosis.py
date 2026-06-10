"""Diagnose a one-share MOCK order path and save a report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import yaml

sys.path.insert(0, os.path.dirname(__file__))

from kis_api import KISApiClient
from price_tick import adjust_price_to_tick
from real_order_utils import normalize_stock_code
from safety_gate import SafetyGate
from trading_calendar import SESSION_REGULAR
from utils import ensure_dir, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _mock_config_path(config_path: str) -> str:
    cfg = load_config(config_path)
    cfg["live_trade"] = False
    cfg["paper_trade"] = False
    cfg.setdefault("kis", {})["use_mock"] = True
    cfg["kis"]["mock_token_cache_file"] = "data/mock_token_cache.json"
    cfg["kis"]["token_cache_file"] = ""
    cfg["kis"]["app_key_env"] = cfg["kis"].get("mock_app_key_env", cfg["kis"].get("app_key_env", "KIS_APP_KEY"))
    cfg["kis"]["app_secret_env"] = cfg["kis"].get("mock_app_secret_env", cfg["kis"].get("app_secret_env", "KIS_APP_SECRET"))
    fd, path = tempfile.mkstemp(prefix="ai_stock_mock_", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return path


def _save_report(result: Dict[str, Any]) -> Dict[str, str]:
    reports = PROJECT_ROOT / "reports"
    ensure_dir(str(reports))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = reports / f"mock_order_diagnosis_{ts}.json"
    txt_path = reports / f"mock_order_diagnosis_{ts}.txt"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    lines = [
        "MOCK order diagnosis report",
        f"run_at: {result.get('run_at')}",
        f"requested_mode: {result.get('requested_mode')}",
        f"resolved_mode: {result.get('resolved_mode')}",
        f"success: {result.get('success')}",
        f"token_source: {result.get('token_source')}",
        f"token_recovered: {result.get('token_recovered')}",
        f"orderable_cash: {result.get('orderable_cash')}",
        f"stock_code: {result.get('stock_code')}",
        f"quantity: {result.get('quantity')}",
        f"current_price: {result.get('current_price')}",
        f"order_price: {result.get('order_price')}",
        f"order_no: {result.get('order_no')}",
        f"rt_cd: {result.get('rt_cd')}",
        f"msg: {result.get('msg')}",
    ]
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return {"json_path": str(json_path), "txt_path": str(txt_path)}


def run_mock_order_diagnosis(
    stock_code: str,
    quantity: int = 1,
    config_path: str = "config.yaml",
) -> Dict[str, Any]:
    code = normalize_stock_code(stock_code)
    tmp_path = _mock_config_path(config_path)
    token_recovered = False
    result: Dict[str, Any] = {
        "run_at": datetime.now().isoformat(),
        "requested_mode": "mock",
        "resolved_mode": "",
        "stock_code": code,
        "quantity": int(quantity),
        "mock_key_ok": False,
        "mock_token_ok": False,
        "token_source": "",
        "base_url": "",
        "token_url": "",
        "key_type_used": "",
        "token_cache_file": "",
        "app_key_mode_valid": False,
        "mode_url_valid": False,
        "token_recovered": False,
        "current_price": 0,
        "orderable_cash": 0,
        "order_price": 0,
        "order_amount": 0,
        "api_called": False,
        "mock_order_called": False,
        "real_order_called": False,
        "order_no": "",
        "rt_cd": "",
        "msg": "",
        "success": False,
    }
    try:
        gate = SafetyGate(tmp_path, runtime_mode="mock")
        api = KISApiClient(tmp_path, gate=gate)
        result["resolved_mode"] = gate.mode
        result.update(api.diagnostic_metadata())
        result["mock_key_ok"] = bool(api.auth._app_key and api.auth._app_secret and api._account_no)

        try:
            api.auth.get_access_token()
            result["mock_token_ok"] = True
            result["token_source"] = api.auth.token_source
            result.update(api.diagnostic_metadata())
        except Exception as exc:
            result["msg"] = f"token failed: {exc}"
            paths = _save_report(result)
            result.update(paths)
            return result

        quote = api.get_current_price(code)
        current_price = int(quote.get("current_price", 0) or 0)
        result["current_price"] = current_price
        cash = int(float(api.get_orderable_cash() or 0))
        result["orderable_cash"] = cash
        order_price = adjust_price_to_tick(current_price, side="buy", method="floor") if current_price > 0 else 0
        result["order_price"] = order_price
        result["order_amount"] = int(order_price * int(quantity))

        result["api_called"] = True
        result["mock_order_called"] = True
        resp = api.place_cash_buy_order(code, int(quantity), order_price, "limit", order_session=SESSION_REGULAR)
        if api.auth.token_source.startswith("fresh_") and result["token_source"].endswith("_cache"):
            token_recovered = True
        result["token_source"] = api.auth.token_source or result["token_source"]
        result.update(api.diagnostic_metadata())
        result["token_recovered"] = token_recovered
        result["rt_cd"] = resp.get("rt_cd", "")
        result["msg"] = resp.get("msg1", resp.get("raw_msg", ""))
        result["order_no"] = resp.get("output", {}).get("ODNO", "")
        result["success"] = bool(result["rt_cd"] == "0" and result["order_no"])
        result["raw_response"] = resp
    except Exception as exc:
        result["msg"] = str(exc)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    paths = _save_report(result)
    result.update(paths)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    result = run_mock_order_diagnosis(args.stock_code, args.quantity, args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
