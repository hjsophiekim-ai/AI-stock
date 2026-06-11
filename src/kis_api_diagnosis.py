# -*- coding: utf-8 -*-
"""KIS API 범용 진단 도구 — MOCK/REAL 모드 지원, 주문 API 절대 호출 안 함.

CLI:
  python src\\kis_api_diagnosis.py --mode mock --token
  python src\\kis_api_diagnosis.py --mode mock --refresh-token
  python src\\kis_api_diagnosis.py --mode mock --connection
  python src\\kis_api_diagnosis.py --mode mock --account
  python src\\kis_api_diagnosis.py --mode mock --cash
  python src\\kis_api_diagnosis.py --mode mock --all

  python src\\kis_api_diagnosis.py --mode real --token
  python src\\kis_api_diagnosis.py --mode real --refresh-token
  python src\\kis_api_diagnosis.py --mode real --all
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
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

from kis_auth import get_token_status, refresh_token, delete_token_cache, fingerprint_key
from utils import ensure_dir, load_config, setup_logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = setup_logger(__name__, "logs/api.log")


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def _print_result(label: str, ok: bool, detail: str = "") -> None:
    icon = "✅" if ok else "❌"
    line = f"  {icon} {label}"
    if detail:
        line += f" — {detail}"
    print(line)


def _make_api_client(mode: str):
    """mode별 KISApiClient 생성 (진단 전용 임시 config 사용)."""
    from safety_gate import SafetyGate
    from kis_api import KISApiClient

    cfg = load_config(str(PROJECT_ROOT / "config.yaml"))
    mode_u = mode.strip().upper()
    if mode_u == "REAL":
        cfg["live_trade"] = True
        cfg["paper_trade"] = False
        cfg.setdefault("kis", {})["use_mock"] = False
        cfg["kis"]["app_key_env"] = "KIS_REAL_APP_KEY"
        cfg["kis"]["app_secret_env"] = "KIS_REAL_APP_SECRET"
        cfg["kis"]["account_no_env"] = "KIS_ACCOUNT_NO"
        cfg["kis"]["token_cache_file"] = "data/real_token_cache.json"
        cfg.setdefault("safety", {})["confirm_live_trade"] = True
    else:
        cfg.setdefault("kis", {})["use_mock"] = True
        cfg["kis"]["app_key_env"] = "KIS_MOCK_APP_KEY"
        cfg["kis"]["app_secret_env"] = "KIS_MOCK_APP_SECRET"
        cfg["kis"]["account_no_env"] = "KIS_MOCK_ACCOUNT_NO"
        cfg["kis"]["token_cache_file"] = "data/mock_token_cache.json"

    fd, tmp_path = tempfile.mkstemp(prefix=f"kis_diag_{mode_u.lower()}_", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        gate = SafetyGate(tmp_path, runtime_mode=mode_u.lower())
        api = KISApiClient(tmp_path, gate=gate)
        return api, tmp_path
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def cmd_token_status(mode: str) -> Dict[str, Any]:
    """토큰 캐시 상태 조회 (원문 미포함)."""
    _print_section(f"[{mode.upper()}] 토큰 상태")
    status = get_token_status(mode)
    _print_result("캐시 파일", True, status.get("token_cache_file", ""))
    _print_result("캐시 존재", status.get("cache_exists", False))
    _print_result("토큰 유효", not status.get("is_expired", True),
                  f"만료: {status.get('expires_at_str', '')} / 남은 시간: {status.get('remaining_seconds', 0) // 3600}h {(status.get('remaining_seconds', 0) % 3600) // 60}m")
    _print_result("App Key", bool(status.get("app_key_masked") and status.get("app_key_masked") != "MISSING"),
                  status.get("app_key_masked", ""))
    _print_result("계좌번호", bool(status.get("account_masked")), status.get("account_masked", ""))
    return status


def cmd_refresh_token(mode: str) -> Dict[str, Any]:
    """토큰 캐시 삭제 후 새 토큰 발급."""
    _print_section(f"[{mode.upper()}] 토큰 재발급")
    print(f"  캐시 삭제 중: {get_token_status(mode).get('token_cache_file', '')}")
    result = refresh_token(mode)
    _print_result("토큰 발급", result.get("success", False),
                  result.get("error", f"만료: {result.get('expires_at_str', '')}"))
    return result


def cmd_connection(mode: str) -> Dict[str, Any]:
    """현재가 조회로 연결 확인."""
    _print_section(f"[{mode.upper()}] API 연결 확인")
    api, tmp_path = None, None
    try:
        api, tmp_path = _make_api_client(mode)
        result = api.get_current_price("005930")
        ok = bool(result and (result.get("current_price") or result.get("output")))
        _print_result("삼성전자 현재가 조회", ok, str(result.get("output", {}).get("stck_prpr", "")) + "원" if ok else str(result)[:100])
        return {"ok": ok, "mode": mode.upper()}
    except Exception as exc:
        _print_result("연결 실패", False, str(exc)[:200])
        return {"ok": False, "mode": mode.upper(), "error": str(exc)}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def cmd_account(mode: str) -> Dict[str, Any]:
    """계좌 잔고조회."""
    _print_section(f"[{mode.upper()}] 계좌 잔고조회")
    api, tmp_path = None, None
    try:
        api, tmp_path = _make_api_client(mode)
        bal = api.get_account_balance()
        ok = isinstance(bal, dict) and bool(bal.get("output1") or bal.get("output2"))
        broker_count = 0
        if ok:
            out1 = bal.get("output1", [])
            if isinstance(out1, list):
                broker_count = len(out1)
        _print_result("계좌 잔고조회", ok, f"보유종목 {broker_count}개")
        return {"ok": ok, "broker_count": broker_count, "mode": mode.upper()}
    except Exception as exc:
        _print_result("계좌 잔고조회 실패", False, str(exc)[:200])
        return {"ok": False, "mode": mode.upper(), "error": str(exc)}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def cmd_orderable_cash(mode: str) -> Dict[str, Any]:
    """주문가능금액 조회."""
    _print_section(f"[{mode.upper()}] 주문가능금액 조회")
    api, tmp_path = None, None
    try:
        api, tmp_path = _make_api_client(mode)
        cash = api.get_orderable_cash()
        ok = cash >= 0
        _print_result("주문가능금액", ok, f"{int(cash):,}원")
        return {"ok": ok, "orderable_cash_amount": int(cash), "mode": mode.upper()}
    except Exception as exc:
        _print_result("주문가능금액 조회 실패", False, str(exc)[:200])
        return {"ok": False, "mode": mode.upper(), "error": str(exc)}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def cmd_all(mode: str) -> Dict[str, Any]:
    """토큰 → 연결 → 계좌 → 주문가능금액 전체 점검."""
    _print_section(f"[{mode.upper()}] 전체 점검")
    results: Dict[str, Any] = {"mode": mode.upper(), "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # 1. 토큰 상태
    ts = cmd_token_status(mode)
    results["token_status"] = ts
    if ts.get("is_expired") or not ts.get("token_present"):
        print("\n  토큰이 없거나 만료됨 → 자동 재발급 시도")
        tr = cmd_refresh_token(mode)
        results["token_refreshed"] = tr.get("success", False)
        if not tr.get("success"):
            print(f"\n  토큰 재발급 실패 — 이후 단계 중단")
            results["verdict"] = "FAIL_NO_TOKEN"
            return results
    else:
        results["token_refreshed"] = False

    # 2. 연결
    conn = cmd_connection(mode)
    results["connection_ok"] = conn.get("ok", False)
    if not results["connection_ok"]:
        results["verdict"] = "FAIL_CONNECTION"
        return results

    # 3. 계좌
    acc = cmd_account(mode)
    results["account_ok"] = acc.get("ok", False)
    results["broker_count"] = acc.get("broker_count", 0)

    # 4. 주문가능금액
    cash = cmd_orderable_cash(mode)
    results["orderable_cash_ok"] = cash.get("ok", False)
    results["orderable_cash_amount"] = cash.get("orderable_cash_amount", 0)

    overall = results["connection_ok"] and results["account_ok"]
    results["verdict"] = f"{mode.upper()}_READY" if overall else f"{mode.upper()}_NOT_READY"

    _print_section(f"[{mode.upper()}] 최종 판정")
    _print_result("전체 준비상태", overall, results["verdict"])

    # 보고서 저장
    ensure_dir(str(PROJECT_ROOT / "reports"))
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = PROJECT_ROOT / "reports" / f"kis_diagnosis_{mode.lower()}_{ts_str}.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n  보고서: {report_path}")
    return results


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=True)

    parser = argparse.ArgumentParser(description="KIS API 진단 도구 (MOCK/REAL)")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock", help="진단 모드 (기본: mock)")
    parser.add_argument("--token", action="store_true", help="토큰 캐시 상태 조회")
    parser.add_argument("--refresh-token", action="store_true", dest="refresh_token", help="토큰 재발급")
    parser.add_argument("--connection", action="store_true", help="API 연결 확인")
    parser.add_argument("--account", action="store_true", help="계좌 잔고조회")
    parser.add_argument("--cash", action="store_true", help="주문가능금액 조회")
    parser.add_argument("--all", action="store_true", dest="run_all", help="전체 점검")
    args = parser.parse_args()

    mode = args.mode
    ran_any = False

    if args.token or (not any([args.refresh_token, args.connection, args.account, args.cash, args.run_all])):
        cmd_token_status(mode)
        ran_any = True
    if args.refresh_token:
        cmd_refresh_token(mode)
        ran_any = True
    if args.connection:
        cmd_connection(mode)
        ran_any = True
    if args.account:
        cmd_account(mode)
        ran_any = True
    if args.cash:
        cmd_orderable_cash(mode)
        ran_any = True
    if args.run_all:
        cmd_all(mode)
        ran_any = True

    if not ran_any:
        cmd_token_status(mode)


if __name__ == "__main__":
    main()
