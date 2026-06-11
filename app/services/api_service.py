"""KIS API 연결 테스트 및 조회 서비스."""

import os
import sys
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from env_service import inject_to_os_env


def _get_api_client(config_path: Optional[str] = None, runtime_mode: Optional[str] = None):
    """KISApiClient 인스턴스 생성."""
    inject_to_os_env()
    config_path = config_path or str(PROJECT_ROOT / "config.yaml")
    from kis_api import KISApiClient
    return KISApiClient(config_path, runtime_mode=runtime_mode)


def test_env_vars() -> Dict:
    """필수 환경변수 존재 여부 확인."""
    inject_to_os_env()
    keys = ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_MOCK_ACCOUNT_NO"]
    result = {}
    for k in keys:
        result[k] = bool(os.environ.get(k))
    all_ok = all(result.values())
    return {"success": all_ok, "checks": result,
            "message": "환경변수 OK" if all_ok else "일부 환경변수 미설정"}


def test_token() -> Dict:
    """접근토큰 발급 테스트."""
    inject_to_os_env()
    try:
        config_path = str(PROJECT_ROOT / "config.yaml")
        from kis_auth import KISAuth
        auth = KISAuth(config_path)
        token = auth.get_access_token()
        if token:
            return {"success": True, "message": "토큰 발급 성공", "token_preview": token[:10] + "..."}
        return {"success": False, "message": "토큰 발급 실패: 빈 응답"}
    except Exception as e:
        return {"success": False, "message": f"토큰 발급 오류: {e}"}


def test_balance() -> Dict:
    """계좌 잔고 조회 테스트."""
    try:
        api = _get_api_client()
        balance = api.get_account_balance()
        return {"success": True, "message": "잔고 조회 성공", "data": balance}
    except Exception as e:
        return {"success": False, "message": f"잔고 조회 오류: {e}", "data": {}}


def test_orderable_cash() -> Dict:
    """주문가능금액 조회 테스트."""
    try:
        api = _get_api_client()
        cash = api.get_orderable_cash()
        return {"success": True, "message": "주문가능금액 조회 성공", "cash": cash}
    except Exception as e:
        return {"success": False, "message": f"주문가능금액 조회 오류: {e}", "cash": 0}


def test_current_price(stock_code: str = "005930") -> Dict:
    """현재가 조회 테스트."""
    try:
        api = _get_api_client()
        info = api.get_current_price(stock_code)
        price = info.get("current_price", 0)
        return {"success": True, "message": f"{stock_code} 현재가: {price:,}원", "price": price, "data": info}
    except Exception as e:
        return {"success": False, "message": f"현재가 조회 오류: {e}", "price": 0}


def get_broker_positions(mode: str = None) -> Dict:
    """KIS 계좌 보유 종목 직접 조회. mode='mock'/'real' 로 모드별 API key/URL 선택."""
    try:
        api = _get_api_client(runtime_mode=mode)
        df = api.get_positions()
        if df is None or df.empty:
            return {"success": True, "message": "보유 종목 없음", "positions": [], "mode": mode}
        return {
            "success": True,
            "message": f"{len(df)}개 종목 조회",
            "positions": df.to_dict("records"),
            "mode": mode,
        }
    except Exception as e:
        return {"success": False, "message": f"보유종목 조회 오류: {e}", "positions": [], "mode": mode}


def run_full_connection_test(stock_code: str = "005930") -> Dict:
    """7단계 API 연결 테스트 실행."""
    inject_to_os_env()
    try:
        config_path = str(PROJECT_ROOT / "config.yaml")
        from api_connection_test import APIConnectionTester
        tester = APIConnectionTester(config_path)
        results = tester.run(stock_code=stock_code)
        return {"success": True, "results": results, "message": "테스트 완료"}
    except Exception as e:
        return {"success": False, "results": [], "message": f"테스트 오류: {e}"}
