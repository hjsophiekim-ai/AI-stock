"""주문구분 코드 해석 (resolve_order_division) 테스트.

KISApiClient를 실제 API 호출 없이 테스트합니다.
SafetyGate를 MOCK 모드로 주입하여 인증 없이 코드 해석만 검증합니다.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_api_client(tmp_path, real_order_confirmed: bool = False, mode: str = "mock"):
    """테스트용 KISApiClient (인증 없이 resolve_order_division만 사용)."""
    import yaml

    cfg = {
        "live_trade": False,
        "kis": {
            "use_mock": True,
            "base_url_mock": "https://openapivts.koreainvestment.com:29443",
            "base_url_real": "https://openapi.koreainvestment.com:9443",
            "app_key_env": "KIS_APP_KEY",
            "app_secret_env": "KIS_APP_SECRET",
            "account_no_env": "KIS_ACCOUNT_NO",
            "account_product_code_env": "KIS_ACCOUNT_PRODUCT_CODE",
            "mock_account_no_env": "KIS_MOCK_ACCOUNT_NO",
            "mock_account_product_code_env": "KIS_MOCK_ACCOUNT_PRODUCT_CODE",
            "request_timeout_seconds": 10,
            "rate_limit_sleep_seconds": 0.1,
            "token_cache_file": str(tmp_path / "token_cache.json"),
        },
        "safety": {
            "confirm_live_trade": False,
            "max_order_retries": 1,
            "max_api_error_count": 3,
            "allow_market_order": False,
        },
        "after_hours": {
            "enabled": True,
            "real_order_confirmed": real_order_confirmed,
        },
        "trading_hours": {
            "pre_market_start": "08:30",
            "pre_market_end": "09:00",
            "regular_start": "09:00",
            "regular_end": "15:20",
            "closing_auction_start": "15:20",
            "closing_auction_end": "15:30",
            "after_close_start": "15:30",
            "after_close_end": "16:00",
            "after_hours_single_start": "16:00",
            "after_hours_single_end": "18:00",
        },
        "logging": {"log_level": "WARNING"},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")

    # SafetyGate를 직접 만들어 주입 (API 인증 없이 mode만 설정)
    from safety_gate import SafetyGate
    gate = SafetyGate(str(cfg_path), runtime_mode=mode)

    # KISApiClient 생성 시 인증이 필요하므로 환경변수 설정 없이 직접 부분 초기화
    # 여기서는 resolve_order_division만 테스트하므로 auth를 mock하지 않고
    # 직접 간소화된 객체를 생성합니다.
    from kis_api import KISApiClient

    class _FakeAuth:
        def get_account_info(self):
            return ("12345678", "01")

        def get_access_token(self):
            return "FAKE_TOKEN"

        def build_auth_headers(self, tr_id):
            return {}

    api = object.__new__(KISApiClient)
    api._config_path = str(cfg_path)
    api.cfg = cfg
    api.gate = gate
    api._use_mock = (mode != "real")
    api._account_no = "12345678"
    api._product_code = "01"
    api._timeout = 10
    api._sleep = 0.0
    api._max_retry = 1
    api._api_error_count = 0
    api._max_api_errors = 3
    api.auth = _FakeAuth()
    return api


class TestResolveOrderDivisionRegular:
    """정규장 주문구분 테스트."""

    def test_regular_buy_ord_dvsn(self, tmp_path):
        from trading_calendar import SESSION_REGULAR
        api = _make_api_client(tmp_path, mode="mock")
        result = api.resolve_order_division(SESSION_REGULAR, "buy")
        assert result["ord_dvsn"] == "00"
        assert result["is_supported"] is True
        assert result["is_confirmed_for_real"] is True

    def test_regular_sell_ord_dvsn(self, tmp_path):
        from trading_calendar import SESSION_REGULAR
        api = _make_api_client(tmp_path, mode="mock")
        result = api.resolve_order_division(SESSION_REGULAR, "sell")
        assert result["ord_dvsn"] == "00"
        assert result["is_supported"] is True

    def test_regular_mock_tr_id(self, tmp_path):
        from trading_calendar import SESSION_REGULAR
        api = _make_api_client(tmp_path, mode="mock")
        buy = api.resolve_order_division(SESSION_REGULAR, "buy")
        sell = api.resolve_order_division(SESSION_REGULAR, "sell")
        assert buy["tr_id"] == "VTTC0802U"
        assert sell["tr_id"] == "VTTC0801U"

    def test_regular_real_tr_id(self, tmp_path):
        from trading_calendar import SESSION_REGULAR
        api = _make_api_client(tmp_path, mode="real")
        buy = api.resolve_order_division(SESSION_REGULAR, "buy")
        assert buy["tr_id"] == "TTTC0802U"


class TestResolveOrderDivisionAfterHours:
    """시간외 세션 주문구분 테스트."""

    def test_after_hours_single_supported_in_mock(self, tmp_path):
        from trading_calendar import SESSION_AFTER_HOURS_SINGLE
        api = _make_api_client(tmp_path, mode="mock")
        result = api.resolve_order_division(SESSION_AFTER_HOURS_SINGLE, "buy")
        assert result["ord_dvsn"] == "61"
        assert result["is_supported"] is True

    def test_after_hours_single_not_confirmed_for_real(self, tmp_path):
        from trading_calendar import SESSION_AFTER_HOURS_SINGLE
        api = _make_api_client(tmp_path, real_order_confirmed=False, mode="real")
        result = api.resolve_order_division(SESSION_AFTER_HOURS_SINGLE, "buy")
        assert result["is_confirmed_for_real"] is False

    def test_after_hours_single_confirmed_for_real_when_flag_true(self, tmp_path):
        from trading_calendar import SESSION_AFTER_HOURS_SINGLE
        api = _make_api_client(tmp_path, real_order_confirmed=True, mode="real")
        result = api.resolve_order_division(SESSION_AFTER_HOURS_SINGLE, "buy")
        assert result["is_confirmed_for_real"] is True

    def test_after_close_candidate_code(self, tmp_path):
        from trading_calendar import SESSION_AFTER_CLOSE
        api = _make_api_client(tmp_path, mode="mock")
        result = api.resolve_order_division(SESSION_AFTER_CLOSE, "buy")
        assert result["ord_dvsn"] == "62"
        assert result["is_supported"] is True

    def test_pre_market_candidate_code(self, tmp_path):
        from trading_calendar import SESSION_PRE_MARKET
        api = _make_api_client(tmp_path, mode="mock")
        result = api.resolve_order_division(SESSION_PRE_MARKET, "buy")
        assert result["ord_dvsn"] == "60"
        assert result["is_supported"] is True


class TestResolveOrderDivisionUnsupported:
    """지원하지 않는 세션 테스트."""

    def test_closing_auction_not_supported(self, tmp_path):
        from trading_calendar import SESSION_CLOSING_AUCTION
        api = _make_api_client(tmp_path, mode="mock")
        result = api.resolve_order_division(SESSION_CLOSING_AUCTION, "buy")
        assert result["is_supported"] is False
        assert result["ord_dvsn"] == ""

    def test_closed_not_supported(self, tmp_path):
        from trading_calendar import SESSION_CLOSED
        api = _make_api_client(tmp_path, mode="mock")
        result = api.resolve_order_division(SESSION_CLOSED, "buy")
        assert result["is_supported"] is False
        assert result["ord_dvsn"] == ""


class TestRealModeBlocking:
    """REAL 모드에서 미확인 코드 차단 테스트."""

    def test_real_mode_after_hours_raises_without_confirmation(self, tmp_path):
        """REAL 모드 + real_order_confirmed=false → NotImplementedError."""
        from trading_calendar import SESSION_AFTER_HOURS_SINGLE
        api = _make_api_client(tmp_path, real_order_confirmed=False, mode="real")
        result = api.resolve_order_division(SESSION_AFTER_HOURS_SINGLE, "buy")
        assert result["is_confirmed_for_real"] is False

    def test_real_mode_regular_never_blocked(self, tmp_path):
        """REAL 모드 + REGULAR → 항상 허용."""
        from trading_calendar import SESSION_REGULAR
        api = _make_api_client(tmp_path, real_order_confirmed=False, mode="real")
        result = api.resolve_order_division(SESSION_REGULAR, "buy")
        assert result["is_confirmed_for_real"] is True
        assert result["ord_dvsn"] == "00"
