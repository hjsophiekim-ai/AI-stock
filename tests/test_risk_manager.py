"""리스크 매니저 테스트.

live_trade=false일 때 실제 주문 불가,
종목당 투자비중 초과 시 주문 거부를 검증합니다.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from risk_manager import RiskManager


def make_risk_manager(live_trade: bool = False, paper_trade: bool = True) -> RiskManager:
    """테스트용 RiskManager 생성."""
    rm = RiskManager.__new__(RiskManager)
    rm.live_trade = live_trade
    rm.paper_trade = paper_trade
    rm.risk = {
        "max_positions": 20,
        "max_position_weight": 0.05,
        "max_daily_loss_rate": -0.03,
        "max_market_index_drop_rate": -0.015,
        "min_price": 1000,
        "min_daily_trading_value": 3_000_000_000,
        "min_avg_20d_trading_value": 5_000_000_000,
        "exclude_preferred_stock": True,
        "exclude_spac": True,
        "exclude_etf_etn": True,
        "exclude_warning_stock": True,
        "exclude_halted_stock": True,
        "exclude_management_stock": True,
        "use_limit_order_only": True,
    }
    rm._total_capital = 100_000_000
    return rm


class TestLiveTradeSafety:
    """live_trade 안전장치 검증."""

    def test_paper_trade_mode_buy_allowed(self):
        """paper_trade=true, live_trade=false이면 buy는 허용 (가상)."""
        rm = make_risk_manager(live_trade=False, paper_trade=True)
        result = rm.check_order(
            ticker="005930",
            name="삼성전자",
            side="buy",
            price=70000,
            qty=10,
            current_positions={},
            daily_pnl_rate=0.0,
            market_drop_rate=0.0,
        )
        assert result.approved, f"paper_trade 모드에서 buy가 거부됨: {result.reason}"

    def test_no_trade_mode_buy_blocked(self):
        """live_trade=false, paper_trade=false이면 buy 거부."""
        rm = make_risk_manager(live_trade=False, paper_trade=False)
        result = rm.check_order(
            ticker="005930",
            name="삼성전자",
            side="buy",
            price=70000,
            qty=10,
            current_positions={},
        )
        assert not result.approved, "live_trade=false, paper_trade=false인데 buy가 허용됨"

    def test_sell_always_allowed(self):
        """매도는 live_trade 설정에 관계없이 허용 (청산 방지 없음)."""
        rm = make_risk_manager(live_trade=False, paper_trade=False)
        result = rm.check_order(
            ticker="005930",
            name="삼성전자",
            side="sell",
            price=70000,
            qty=10,
        )
        assert result.approved, f"매도가 거부됨: {result.reason}"


class TestPositionLimits:
    """포지션 한도 검증."""

    def test_max_position_count_exceeded(self):
        """최대 보유 종목 수 초과 시 거부."""
        rm = make_risk_manager(live_trade=False, paper_trade=True)
        # 이미 20개 보유 중
        mock_positions = {f"00000{i}": {} for i in range(20)}
        result = rm.check_order(
            ticker="005930",
            name="삼성전자",
            side="buy",
            price=70000,
            qty=10,
            current_positions=mock_positions,
        )
        assert not result.approved, "최대 보유 수 초과인데 주문이 허용됨"

    def test_max_weight_exceeded(self):
        """종목당 최대 비중 초과 시 거부."""
        rm = make_risk_manager(live_trade=False, paper_trade=True)
        # 총 1억원, 5% = 500만원, 70,000원 × 100주 = 700만원 → 초과
        result = rm.check_order(
            ticker="005930",
            name="삼성전자",
            side="buy",
            price=70000,
            qty=100,
            current_positions={},
            daily_pnl_rate=0.0,
            market_drop_rate=0.0,
        )
        assert not result.approved, "종목당 최대 비중 초과인데 주문이 허용됨"

    def test_within_position_limits(self):
        """한도 이내이면 허용."""
        rm = make_risk_manager(live_trade=False, paper_trade=True)
        # 70,000원 × 10주 = 70만원 → 5% = 500만원 이내
        result = rm.check_order(
            ticker="005930",
            name="삼성전자",
            side="buy",
            price=70000,
            qty=10,
            current_positions={},
            daily_pnl_rate=0.0,
            market_drop_rate=0.0,
        )
        assert result.approved, f"한도 이내인데 주문이 거부됨: {result.reason}"


class TestDailyLossLimit:
    """일일 손실 한도 검증."""

    def test_daily_loss_exceeded(self):
        """일일 손실 한도 초과 시 신규매수 거부."""
        rm = make_risk_manager(live_trade=False, paper_trade=True)
        result = rm.check_order(
            ticker="005930",
            name="삼성전자",
            side="buy",
            price=70000,
            qty=10,
            current_positions={},
            daily_pnl_rate=-0.035,  # -3.5% 손실 → 한도 -3% 초과
        )
        assert not result.approved, "일일 손실 한도 초과인데 주문이 허용됨"


class TestMarketDropFilter:
    """시장 급락 필터 검증."""

    def test_market_drop_blocks_buy(self):
        """지수 -1.5% 이상 하락 시 신규매수 거부."""
        rm = make_risk_manager(live_trade=False, paper_trade=True)
        result = rm.check_order(
            ticker="005930",
            name="삼성전자",
            side="buy",
            price=70000,
            qty=10,
            current_positions={},
            daily_pnl_rate=0.0,
            market_drop_rate=-0.02,  # -2% 하락
        )
        assert not result.approved, "지수 -2% 하락인데 매수가 허용됨"


class TestStockFilters:
    """종목 필터 검증."""

    def test_halted_stock_blocked(self):
        """거래정지 종목 거부."""
        rm = make_risk_manager(live_trade=False, paper_trade=True)
        result = rm.check_order(
            ticker="005930",
            name="삼성전자",
            side="buy",
            price=70000,
            qty=10,
            is_halted=True,
        )
        assert not result.approved, "거래정지 종목인데 주문이 허용됨"

    def test_preferred_stock_blocked(self):
        """우선주 거부."""
        rm = make_risk_manager(live_trade=False, paper_trade=True)
        result = rm.check_order(
            ticker="005935",  # 삼성전자우 (끝자리 5 = 우선주)
            name="삼성전자우",
            side="buy",
            price=60000,
            qty=10,
        )
        assert not result.approved, "우선주인데 주문이 허용됨"

    def test_min_price_blocked(self):
        """최소 주가 미달 거부."""
        rm = make_risk_manager(live_trade=False, paper_trade=True)
        result = rm.check_order(
            ticker="005930",
            name="테스트",
            side="buy",
            price=500,  # 1,000원 미만
            qty=10,
        )
        assert not result.approved, "최소 주가 미달인데 주문이 허용됨"
