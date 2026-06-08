"""OrderManager 테스트.

핵심 검증:
- PAPER 모드에서 KIS API 호출이 일어나지 않아야 함
- 수량·금액 검증
- 부분 체결 처리
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_config(tmp_path, live_trade=False, use_mock=True, confirm=False):
    import yaml
    cfg = {
        "live_trade": live_trade,
        "paper_trade": not live_trade,
        "safety": {
            "confirm_live_trade": confirm,
            "max_api_error_count": 5,
            "stop_new_orders_on_api_error": True,
        },
        "kis": {
            "use_mock": use_mock,
            "base_url_mock": "https://openapivts.koreainvestment.com:29443",
            "base_url_real": "https://openapi.koreainvestment.com:9443",
            "rate_limit_sleep_seconds": 0,
        },
        "risk": {
            "max_positions": 20,
            "max_position_weight": 0.05,
            "min_price": 1000,
            "max_daily_loss_rate": -0.03,
            "max_market_index_drop_rate": -0.015,
            "exclude_preferred_stock": True,
            "exclude_spac": True,
            "exclude_etf_etn": True,
        },
        "strategy": {
            "buy_top_n": 20,
            "target_profit_rate": 0.02,
            "stop_loss_rate": -0.03,
            "forced_exit_time_next_day": "09:30",
        },
        "data": {
            "positions_file": str(tmp_path / "positions.json"),
        },
        "paths": {
            "order_log_dir": str(tmp_path / "reports/orders"),
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(__import__("yaml").dump(cfg), encoding="utf-8")
    return str(p)


class TestPaperModeNoApiCall:
    """PAPER 모드에서 KIS API를 절대 호출하지 않아야 한다."""

    def test_buy_in_paper_mode_does_not_call_api(self, tmp_path):
        """PAPER 모드 매수 — KIS API 미호출 확인."""
        config_path = _make_config(tmp_path, live_trade=False)
        from order_manager import OrderManager

        mgr = OrderManager(config_path)
        mock_api = MagicMock()
        mgr._api = mock_api

        result = mgr.buy_stock(
            stock_code="005930",
            stock_name="삼성전자",
            target_amount=500_000,
            current_price=70_000,
        )

        # PAPER 모드: API 호출 없음
        mock_api.place_cash_buy_order.assert_not_called()
        # PAPER 모드: 가상 성공으로 처리
        assert result.get("mode") == "PAPER"

    def test_sell_in_paper_mode_does_not_call_api(self, tmp_path):
        """PAPER 모드 매도 — KIS API 미호출 확인."""
        config_path = _make_config(tmp_path, live_trade=False)
        from order_manager import OrderManager
        from position_manager import PositionManager
        from datetime import datetime

        mgr = OrderManager(config_path)
        mock_api = MagicMock()
        mgr._api = mock_api

        # 포지션 수동 추가
        mgr.pos_mgr.update_position_after_buy(
            stock_code="005930",
            stock_name="삼성전자",
            quantity=7,
            entry_price=70_000,
        )

        result = mgr.sell_all_position("005930", reason="take_profit")
        mock_api.place_cash_sell_order.assert_not_called()
        assert result.get("mode") == "PAPER"


class TestQuantityValidation:
    """수량·금액 검증 테스트."""

    def test_quantity_zero_blocked(self, tmp_path):
        """계산된 수량이 0이면 매수 거부."""
        config_path = _make_config(tmp_path, live_trade=False)
        from order_manager import OrderManager

        mgr = OrderManager(config_path)
        # target_amount가 주가보다 작으면 수량 = 0
        result = mgr.buy_stock(
            stock_code="005930",
            stock_name="삼성전자",
            target_amount=100,       # 주가(70,000원)보다 작음
            current_price=70_000,
        )
        assert not result.get("success")

    def test_price_below_minimum_blocked(self, tmp_path):
        """주가가 최소가격 미달이면 매수 거부."""
        config_path = _make_config(tmp_path, live_trade=False)
        from order_manager import OrderManager

        mgr = OrderManager(config_path)
        result = mgr.buy_stock(
            stock_code="999999",
            stock_name="저가주",
            target_amount=100_000,
            current_price=500,   # min_price=1000 미달
        )
        assert not result.get("success")

    def test_preferred_stock_blocked(self, tmp_path):
        """우선주 코드이면 매수 거부 (ticker 끝자리 5~9)."""
        config_path = _make_config(tmp_path, live_trade=False)
        from order_manager import OrderManager

        mgr = OrderManager(config_path)
        result = mgr.buy_stock(
            stock_code="005935",  # 삼성전자 우선주
            stock_name="삼성전자우",
            target_amount=500_000,
            current_price=60_000,
        )
        assert not result.get("success")


class TestFailedTickers:
    """실패 종목 재시도 방지 테스트."""

    def test_failed_ticker_skipped(self, tmp_path):
        """한 번 실패한 종목은 같은 루프에서 재시도 없음."""
        config_path = _make_config(tmp_path, live_trade=False)
        from order_manager import OrderManager

        mgr = OrderManager(config_path)
        mgr._failed_tickers.add("005930")

        result = mgr.buy_stock(
            stock_code="005930",
            stock_name="삼성전자",
            target_amount=500_000,
            current_price=70_000,
        )
        assert not result.get("success")
        reason = result.get("reason", "")
        assert "실패" in reason or "재시도" in reason or "건너뜀" in reason or "루프" in reason

    def test_reset_failed_tickers(self, tmp_path):
        """reset_failed_tickers() 호출 시 목록 초기화."""
        config_path = _make_config(tmp_path, live_trade=False)
        from order_manager import OrderManager

        mgr = OrderManager(config_path)
        mgr._failed_tickers.add("005930")
        mgr.reset_failed_tickers()
        assert len(mgr._failed_tickers) == 0
