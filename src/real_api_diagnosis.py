# -*- coding: utf-8 -*-
"""REAL 계좌/시세/미체결 API 진단 도구 — 주문 API는 절대 호출하지 않음.

CLI:
  python src/real_api_diagnosis.py --balance
  python src/real_api_diagnosis.py --price --stock-code 005930
  python src/real_api_diagnosis.py --open-orders
  python src/real_api_diagnosis.py --all

잔고조회 파라미터 후보를 순차 테스트하여 성공 조합을 찾고 보고서에 저장합니다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

from kis_api import KISApiClient
from safety_gate import SafetyGate
from utils import ensure_dir, load_config, setup_logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = setup_logger(__name__, "logs/api.log")


# ══════════════════════════════════════════════════════════════════════════════
# REAL 전용 임시 config 생성
# ══════════════════════════════════════════════════════════════════════════════

def _real_cfg_path(config_path: str = "config.yaml") -> str:
    cfg = load_config(config_path)
    cfg["live_trade"] = True
    cfg["paper_trade"] = False
    cfg.setdefault("kis", {})["use_mock"] = False
    cfg["kis"]["app_key_env"] = "KIS_REAL_APP_KEY"
    cfg["kis"]["app_secret_env"] = "KIS_REAL_APP_SECRET"
    cfg["kis"]["account_no_env"] = "KIS_ACCOUNT_NO"
    cfg["kis"]["token_cache_file"] = "data/real_token_cache.json"
    cfg.setdefault("safety", {})["confirm_live_trade"] = True
    fd, path = tempfile.mkstemp(prefix="ai_stock_real_diag_", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return path


def _extract_exc_info(exc: Exception) -> Dict[str, Any]:
    """HTTPError 또는 일반 예외에서 진단 정보 추출."""
    info: Dict[str, Any] = {
        "error": str(exc),
        "error_type": type(exc).__name__,
    }
    for attr in ("http_status_code", "response_text", "response_json",
                 "request_url", "tr_id", "params_masked", "error_category",
                 "mode", "base_url", "key_type_used"):
        if hasattr(exc, attr):
            info[attr] = getattr(exc, attr)
    return info


# ══════════════════════════════════════════════════════════════════════════════
# 1. 토큰 확인
# ══════════════════════════════════════════════════════════════════════════════

def _check_token(api: KISApiClient) -> Dict[str, Any]:
    try:
        token = api.auth.get_access_token()
        return {"ok": bool(token), "token_prefix": (token or "")[:8] + "****"}
    except Exception as exc:
        return {"ok": False, **_extract_exc_info(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# 2. 잔고조회 파라미터 후보 순차 테스트
# ══════════════════════════════════════════════════════════════════════════════

# 파라미터 후보 조합
_BALANCE_PARAM_CANDIDATES: List[Dict[str, str]] = []
for _inqr in ("02", "01"):
    for _unpr in ("01", "02"):
        for _prcs in ("01", "00"):
            for _ofl in ("", "N"):
                _BALANCE_PARAM_CANDIDATES.append({
                    "INQR_DVSN": _inqr,
                    "UNPR_DVSN": _unpr,
                    "PRCS_DVSN": _prcs,
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": _ofl,
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                })


def _diagnose_balance(api: KISApiClient) -> Dict[str, Any]:
    """잔고조회 파라미터 후보 순차 테스트."""
    account_no = api._account_no
    product_code = api._product_code
    tr_id = "TTTC8434R"  # REAL only
    results: List[Dict[str, Any]] = []
    success_combo: Optional[Dict[str, Any]] = None

    for i, candidate in enumerate(_BALANCE_PARAM_CANDIDATES):
        params = {
            "CANO": account_no,
            "ACNT_PRDT_CD": product_code,
            **candidate,
        }
        attempt_info: Dict[str, Any] = {
            "combo_index": i,
            "params_masked": {k: ("****" if k in ("CANO", "ACNT_PRDT_CD") else v) for k, v in params.items()},
        }
        try:
            data = api._get(
                "/uapi/domestic-stock/v1/trading/inquire-balance",
                tr_id,
                params,
            )
            rt_cd = str(data.get("rt_cd", ""))
            attempt_info["rt_cd"] = rt_cd
            attempt_info["msg"] = data.get("msg1", "")
            attempt_info["output1_count"] = len(data.get("output1", []))
            attempt_info["output2_keys"] = list((data.get("output2") or [{}])[0].keys())[:10] if data.get("output2") else []
            if rt_cd == "0":
                attempt_info["ok"] = True
                success_combo = attempt_info
                results.append(attempt_info)
                break  # 성공 시 추가 테스트 중단
            else:
                attempt_info["ok"] = False
        except Exception as exc:
            attempt_info["ok"] = False
            attempt_info.update(_extract_exc_info(exc))
        results.append(attempt_info)

    return {
        "tr_id": tr_id,
        "tried_combinations": len(results),
        "success_combination": success_combo,
        "recommended_params": success_combo.get("params_masked") if success_combo else None,
        "all_results": results,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3. 현재가 조회 진단
# ══════════════════════════════════════════════════════════════════════════════

def _diagnose_price(api: KISApiClient, stock_code: str = "005930") -> Dict[str, Any]:
    tr_id = "FHKST01010100"
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": stock_code.zfill(6),
    }
    try:
        data = api._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id,
            params,
        )
        rt_cd = str(data.get("rt_cd", ""))
        output = data.get("output", {})
        return {
            "tr_id": tr_id,
            "stock_code": stock_code,
            "ok": rt_cd == "0",
            "rt_cd": rt_cd,
            "msg": data.get("msg1", ""),
            "current_price": output.get("stck_prpr", ""),
            "stock_name": output.get("hts_kor_isnm", ""),
        }
    except Exception as exc:
        return {
            "tr_id": tr_id,
            "stock_code": stock_code,
            "ok": False,
            **_extract_exc_info(exc),
        }


# ══════════════════════════════════════════════════════════════════════════════
# 4. 미체결 조회 진단 (주문 없음)
# ══════════════════════════════════════════════════════════════════════════════

_OPEN_ORDER_CANDIDATES: List[Dict[str, str]] = [
    {"INQR_DVSN_1": "0", "INQR_DVSN_2": "0"},
    {"INQR_DVSN_1": "1", "INQR_DVSN_2": "0"},
    {"INQR_DVSN_1": "0", "INQR_DVSN_2": "1"},
    {"INQR_DVSN_1": "1", "INQR_DVSN_2": "1"},
]


def _diagnose_open_orders(api: KISApiClient) -> Dict[str, Any]:
    tr_id = "TTTC8036R"  # REAL only
    results: List[Dict[str, Any]] = []
    success_combo: Optional[Dict[str, Any]] = None

    for i, candidate in enumerate(_OPEN_ORDER_CANDIDATES):
        params = {
            "CANO": api._account_no,
            "ACNT_PRDT_CD": api._product_code,
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            **candidate,
        }
        attempt_info: Dict[str, Any] = {
            "combo_index": i,
            "params_masked": {k: ("****" if k in ("CANO", "ACNT_PRDT_CD") else v) for k, v in params.items()},
        }
        try:
            data = api._get(
                "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
                tr_id,
                params,
            )
            rt_cd = str(data.get("rt_cd", ""))
            raw_msg = str(data.get("msg1", "") or "")
            attempt_info["rt_cd"] = rt_cd
            attempt_info["msg"] = raw_msg
            attempt_info["output_count"] = len(data.get("output", []))
            if rt_cd == "0":
                attempt_info["ok"] = True
                success_combo = attempt_info
                results.append(attempt_info)
                break
            elif "해당업무가 제공되지 않습니다" in raw_msg:
                attempt_info["ok"] = False
                attempt_info["query_status"] = "UNSUPPORTED"
            else:
                attempt_info["ok"] = False
                attempt_info["query_status"] = "ERROR"
        except Exception as exc:
            attempt_info["ok"] = False
            attempt_info.update(_extract_exc_info(exc))
        results.append(attempt_info)

    return {
        "tr_id": tr_id,
        "tried_combinations": len(results),
        "success_combination": success_combo,
        "all_results": results,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. 전체 진단 실행 및 보고서 저장
# ══════════════════════════════════════════════════════════════════════════════

def run_real_api_diagnosis(
    check_balance: bool = True,
    check_price: bool = True,
    check_open_orders: bool = True,
    stock_code: str = "005930",
    config_path: str = "config.yaml",
) -> Dict[str, Any]:
    """REAL API 진단 실행. 주문 API는 절대 호출하지 않음."""
    load_dotenv(PROJECT_ROOT / ".env")
    result: Dict[str, Any] = {
        "run_at": datetime.now().isoformat(),
        "mode": "REAL",
        "stock_code_for_price": stock_code,
        "token": {},
        "balance": {},
        "price": {},
        "open_orders": {},
        "overall_ok": False,
        "ready_for_balance": False,
        "ready_for_price": False,
        "summary": "",
    }

    real_key = os.environ.get("KIS_REAL_APP_KEY") or os.environ.get("KIS_APP_KEY", "")
    real_secret = os.environ.get("KIS_REAL_APP_SECRET", "")
    real_account = os.environ.get("KIS_ACCOUNT_NO", "")
    result["api_key_present"] = bool(real_key and real_secret and real_account)

    if not result["api_key_present"]:
        result["summary"] = "REAL API 키 없음 — .env 파일에 KIS_REAL_APP_KEY/KIS_REAL_APP_SECRET/KIS_ACCOUNT_NO 필요"
        return result

    tmp_path = _real_cfg_path(config_path)
    try:
        gate = SafetyGate(tmp_path, runtime_mode="real")
        api = KISApiClient(tmp_path, gate=gate)

        # 토큰 확인
        token_info = _check_token(api)
        result["token"] = token_info
        if not token_info.get("ok"):
            result["summary"] = f"REAL 토큰 발급 실패: {token_info.get('error', '')}"
            return result

        # 잔고조회 진단
        if check_balance:
            bal_result = _diagnose_balance(api)
            result["balance"] = bal_result
            result["ready_for_balance"] = bal_result.get("success_combination") is not None

        # 현재가 조회 진단
        if check_price:
            price_result = _diagnose_price(api, stock_code)
            result["price"] = price_result
            result["ready_for_price"] = bool(price_result.get("ok"))

        # 미체결조회 진단
        if check_open_orders:
            oo_result = _diagnose_open_orders(api)
            result["open_orders"] = oo_result

        # 종합 판단
        balance_ok = result["ready_for_balance"] if check_balance else True
        price_ok = result["ready_for_price"] if check_price else True
        result["overall_ok"] = balance_ok and price_ok
        parts = []
        if check_balance:
            parts.append(f"잔고조회={'OK' if result['ready_for_balance'] else 'FAIL'}")
        if check_price:
            parts.append(f"현재가조회={'OK' if result['ready_for_price'] else 'FAIL'}")
        if check_open_orders:
            oo_ok = result["open_orders"].get("success_combination") is not None
            parts.append(f"미체결조회={'OK' if oo_ok else 'UNSUPPORTED/FAIL'}")
        result["summary"] = "REAL 진단 결과: " + " | ".join(parts)

    except Exception as exc:
        result["init_error"] = str(exc)
        result["summary"] = f"초기화 오류: {exc}"
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return result


def save_diagnosis_report(result: Dict[str, Any]) -> Dict[str, str]:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = PROJECT_ROOT / "reports" / f"real_api_diagnosis_{ts}.json"
    txt_path = PROJECT_ROOT / "reports" / f"real_api_diagnosis_{ts}.txt"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    lines = [
        "=" * 60,
        "  REAL API 진단 보고서",
        "=" * 60,
        f"실행시각: {result.get('run_at')}",
        f"종합 결과: {'OK' if result.get('overall_ok') else 'FAIL'}",
        f"요약: {result.get('summary', '')}",
        "",
        "── 토큰 ──────────────────────────────",
        f"  토큰: {'OK' if result.get('token', {}).get('ok') else 'FAIL'}",
    ]
    bal = result.get("balance", {})
    if bal:
        lines += [
            "",
            "── 잔고조회 ──────────────────────────",
            f"  테스트 조합: {bal.get('tried_combinations', 0)}개",
            f"  성공 조합: {'있음' if bal.get('success_combination') else '없음'}",
        ]
        if bal.get("success_combination"):
            sc = bal["success_combination"]
            lines.append(f"  성공 파라미터: {sc.get('params_masked', {})}")
        else:
            for r in (bal.get("all_results") or [])[:3]:
                lines.append(f"  - 조합 {r.get('combo_index')}: rt_cd={r.get('rt_cd','')} msg={r.get('msg','')}")
                if r.get("http_status_code"):
                    lines.append(f"    HTTP {r['http_status_code']} url={r.get('request_url','')} ")
                    lines.append(f"    body={str(r.get('response_text',''))[:300]}")
    price = result.get("price", {})
    if price:
        lines += [
            "",
            "── 현재가 조회 ───────────────────────",
            f"  {result.get('stock_code_for_price','')} 종목: {'OK' if price.get('ok') else 'FAIL'}",
        ]
        if not price.get("ok"):
            lines.append(f"  HTTP {price.get('http_status_code','')} msg={price.get('error','')}")
            lines.append(f"  body: {str(price.get('response_text',''))[:300]}")
        else:
            lines.append(f"  현재가={price.get('current_price','')} 종목명={price.get('stock_name','')}")
    oo = result.get("open_orders", {})
    if oo:
        lines += [
            "",
            "── 미체결 조회 ───────────────────────",
            f"  성공 조합: {'있음' if oo.get('success_combination') else '없음 (UNSUPPORTED 포함)'}",
        ]
    lines += ["", f"보고서: {json_path}"]

    txt_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json_path": str(json_path), "txt_path": str(txt_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="REAL API 진단 (주문 없음)")
    parser.add_argument("--balance", action="store_true", help="잔고조회 진단")
    parser.add_argument("--price", action="store_true", help="현재가조회 진단")
    parser.add_argument("--open-orders", dest="open_orders", action="store_true", help="미체결조회 진단")
    parser.add_argument("--all", action="store_true", help="전체 진단")
    parser.add_argument("--stock-code", default="005930", help="현재가조회 종목코드 (기본: 005930)")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    do_balance = args.all or args.balance
    do_price = args.all or args.price
    do_oo = args.all or args.open_orders

    if not any([do_balance, do_price, do_oo]):
        parser.print_help()
        return

    print(f"\nREAL API 진단 시작 (주문 없음) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"stock_code={args.stock_code}  balance={do_balance}  price={do_price}  open_orders={do_oo}")
    print("-" * 60)

    result = run_real_api_diagnosis(
        check_balance=do_balance,
        check_price=do_price,
        check_open_orders=do_oo,
        stock_code=args.stock_code,
        config_path=args.config,
    )

    print(f"\n{result.get('summary', '')}")
    print(f"종합: {'OK ✓' if result.get('overall_ok') else 'FAIL ✗'}")

    if do_balance:
        bal = result.get("balance", {})
        sc = bal.get("success_combination")
        if sc:
            print(f"\n[잔고조회] 성공 파라미터 조합 (combo #{sc.get('combo_index')})")
            print(f"  {sc.get('params_masked', {})}")
        else:
            print(f"\n[잔고조회] 성공 조합 없음 (테스트 {bal.get('tried_combinations', 0)}개)")
            for r in (bal.get("all_results") or [])[:5]:
                status = f"HTTP {r.get('http_status_code', '')}" if r.get("http_status_code") else f"rt_cd={r.get('rt_cd', '')}"
                print(f"  combo {r.get('combo_index')}: {status} msg={r.get('msg', '')} err={r.get('error', '')[:100]}")
                if r.get("response_text"):
                    print(f"    body_snippet: {str(r.get('response_text',''))[:200]}")

    if do_price:
        price = result.get("price", {})
        if price.get("ok"):
            print(f"\n[현재가] {args.stock_code} 현재가={price.get('current_price')} 종목명={price.get('stock_name')}")
        else:
            print(f"\n[현재가] FAIL HTTP={price.get('http_status_code','')} msg={price.get('error','')}")
            if price.get("response_text"):
                print(f"  body: {str(price.get('response_text',''))[:300]}")

    if do_oo:
        oo = result.get("open_orders", {})
        if oo.get("success_combination"):
            print(f"\n[미체결조회] 성공")
        else:
            first = (oo.get("all_results") or [{}])[0]
            print(f"\n[미체결조회] {first.get('query_status', 'FAIL')} msg={first.get('msg','')}")

    paths = save_diagnosis_report(result)
    print(f"\n보고서 저장: {paths['json_path']}")


if __name__ == "__main__":
    main()
