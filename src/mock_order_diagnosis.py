"""Diagnose a one-share MOCK order path and save a report.

주문 전 헤더 appkey fingerprint 검증 포함.
"해당 앱키는 모의투자용 앱키가 아닙니다" 오류 시 어떤 키가 사용됐는지 확인 가능.
"""

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

# ── 경로 설정 ──────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent

if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

os.chdir(str(PROJECT_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
# ──────────────────────────────────────────────────────────────────────────

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from kis_api import KISApiClient
from kis_auth import fingerprint_key, get_kis_credentials
from price_tick import adjust_price_to_tick
from real_order_utils import normalize_stock_code
from safety_gate import SafetyGate
from trade_mode import get_expected_key_fingerprint_for_mode
from trading_calendar import SESSION_REGULAR
from utils import ensure_dir, load_config


def _mock_config_path(config_path: str) -> str:
    """MOCK 전용 임시 config 생성 (config.yaml 원본 불변)."""
    cfg = load_config(config_path)
    cfg["live_trade"] = False
    cfg["paper_trade"] = False
    cfg.setdefault("kis", {})["use_mock"] = True
    cfg["kis"]["mock_token_cache_file"] = "data/mock_token_cache.json"
    cfg["kis"]["token_cache_file"] = ""
    # 전용 키가 있으면 반드시 전용 키만 사용
    cfg["kis"]["mock_app_key_env"] = "KIS_MOCK_APP_KEY"
    cfg["kis"]["mock_app_secret_env"] = "KIS_MOCK_APP_SECRET"
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
        f"mode=MOCK (강제) config.yaml 무관",
        "",
        f"base_url: {result.get('base_url')}",
        f"token_url: {result.get('token_url')}",
        f"key_type_used: {result.get('key_type_used')}",
        f"token_cache_file: {result.get('token_cache_file')}",
        "",
        f"expected_appkey_fingerprint: {result.get('expected_appkey_fingerprint')}",
        f"header_appkey_fingerprint: {result.get('header_appkey_fingerprint')}",
        f"app_key_mode_valid: {result.get('app_key_mode_valid')}",
        f"mode_url_valid: {result.get('mode_url_valid')}",
        "",
        f"mock_key_ok: {result.get('mock_key_ok')}",
        f"mock_token_ok: {result.get('mock_token_ok')}",
        f"token_source: {result.get('token_source')}",
        f"token_recovered: {result.get('token_recovered')}",
        "",
        f"orderable_cash: {result.get('orderable_cash')}",
        f"stock_code: {result.get('stock_code')}",
        f"quantity: {result.get('quantity')}",
        f"current_price: {result.get('current_price')}",
        f"order_price: {result.get('order_price')}",
        "",
        f"api_called: {result.get('api_called')}",
        f"mock_order_called: {result.get('mock_order_called')}",
        f"real_order_called: {result.get('real_order_called')}",
        "",
        f"order_no: {result.get('order_no')}",
        f"rt_cd: {result.get('rt_cd')}",
        f"msg: {result.get('msg')}",
        f"success: {result.get('success')}",
    ]
    errors = result.get("diagnosis_errors", [])
    if errors:
        lines.append("")
        lines.append("DIAGNOSIS ERRORS:")
        for e in errors:
            lines.append(f"  [!] {e}")
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

    # 사전 키 fingerprint (API 호출 전)
    expected_fp = get_expected_key_fingerprint_for_mode("MOCK")
    creds_check = get_kis_credentials("MOCK")
    actual_fp_pre = creds_check.get("appkey_fingerprint", "MISSING")

    result: Dict[str, Any] = {
        "run_at": datetime.now().isoformat(),
        "requested_mode": "MOCK",
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
        "expected_appkey_fingerprint": expected_fp,
        "header_appkey_fingerprint": actual_fp_pre,
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
        "diagnosis_errors": [],
    }

    # 사전 진단: expected vs actual fingerprint
    if expected_fp != actual_fp_pre and expected_fp not in ("MISSING", "N/A") and actual_fp_pre not in ("MISSING",):
        result["diagnosis_errors"].append(
            f"사전 검증 실패: expected={expected_fp} actual={actual_fp_pre} — MOCK 주문 전 appkey 불일치!"
        )

    try:
        gate = SafetyGate(tmp_path, runtime_mode="mock")
        api = KISApiClient(tmp_path, gate=gate)
        result["resolved_mode"] = gate.mode

        meta = api.diagnostic_metadata()
        result.update(meta)
        result["header_appkey_fingerprint"] = meta.get("appkey_fingerprint", actual_fp_pre)
        result["mock_key_ok"] = bool(api.auth._app_key and api.auth._app_secret and api._account_no)

        # appkey fingerprint 일치 확인
        actual_header_fp = meta.get("appkey_fingerprint", "MISSING")
        if expected_fp not in ("MISSING", "N/A") and actual_header_fp != expected_fp:
            result["diagnosis_errors"].append(
                f"KISApiClient 헤더 appkey 불일치: expected={expected_fp} actual={actual_header_fp}"
            )
            result["app_key_mode_valid"] = False
        elif expected_fp not in ("MISSING", "N/A"):
            result["app_key_mode_valid"] = True

        try:
            api.auth.get_access_token()
            result["mock_token_ok"] = True
            result["token_source"] = api.auth.token_source
            result.update(api.diagnostic_metadata())
        except Exception as exc:
            result["msg"] = f"token failed: {exc}"
            result["diagnosis_errors"].append(f"토큰 발급 실패: {exc}")
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
        final_meta = api.diagnostic_metadata()
        result.update(final_meta)
        result["header_appkey_fingerprint"] = final_meta.get("appkey_fingerprint", result["header_appkey_fingerprint"])
        result["token_recovered"] = token_recovered
        result["rt_cd"] = resp.get("rt_cd", "")
        result["msg"] = resp.get("msg1", resp.get("raw_msg", ""))
        result["order_no"] = resp.get("output", {}).get("ODNO", "")
        result["success"] = bool(result["rt_cd"] == "0" and result["order_no"])
        result["raw_response"] = resp

        # 최종 fingerprint 재확인
        if result["rt_cd"] not in ("0",) and "모의투자용 앱키가 아닙니다" in result.get("msg", ""):
            result["diagnosis_errors"].append(
                f"'모의투자용 앱키가 아닙니다' 오류 발생! "
                f"expected={expected_fp} actual={result['header_appkey_fingerprint']} — "
                "REAL 키가 MOCK 서버로 전송됨"
            )

    except Exception as exc:
        result["msg"] = str(exc)
        result["diagnosis_errors"].append(f"예외: {exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    paths = _save_report(result)
    result.update(paths)
    return result


def _print_result(result: dict) -> None:
    sep = "-" * 60
    print("=" * 60)
    print("MOCK ORDER DIAGNOSIS")
    print("=" * 60)
    fields = [
        "requested_mode", "resolved_mode", "base_url", "token_url",
        "key_type_used", "token_cache_file",
        "expected_appkey_fingerprint", "header_appkey_fingerprint",
        "app_key_mode_valid", "mode_url_valid",
        "mock_key_ok", "mock_token_ok", "token_source",
        "orderable_cash", "stock_code", "quantity",
        "current_price", "order_price",
        "api_called", "mock_order_called", "real_order_called",
        "order_no", "rt_cd", "msg", "success",
    ]
    for f in fields:
        print(f"  {f:<40} = {result.get(f, '')}")
    errors = result.get("diagnosis_errors", [])
    if errors:
        print(sep)
        print("  DIAGNOSIS ERRORS:")
        for e in errors:
            print(f"    [!] {e}")
    print(sep)
    print(f"  json: {result.get('json_path', '')}")
    print(f"  txt:  {result.get('txt_path', '')}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="MOCK 주문 경로 진단 (1주)")
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_mock_order_diagnosis(args.stock_code, args.quantity, args.config)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_result(result)

    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
