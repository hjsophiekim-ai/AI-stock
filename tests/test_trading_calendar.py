"""TradingCalendar 테스트.

핵심 검증:
- 매수 시간대 (14:40~15:00) 판별
- 강제청산 시간 (09:30~) 판별
- 공휴일/주말 거래일 제외
"""

import pytest
import sys
import os
from datetime import date, datetime, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_calendar(tmp_path):
    import yaml
    cfg = {
        "strategy": {
            "buy_start_time": "14:40",
            "buy_end_time": "15:00",
            "after_hours_start_time": "15:40",
            "after_hours_end_time": "18:00",
            "pre_market_start_time": "08:30",
            "pre_market_end_time": "08:40",
            "regular_market_start_time": "09:00",
            "forced_exit_time_next_day": "09:30",
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
        "after_hours": {
            "enabled": True,
            "allow_pre_market": True,
            "allow_regular": True,
            "allow_closing_auction": False,
            "allow_after_close": False,
            "allow_after_hours_single": True,
            "real_order_confirmed": False,
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    from trading_calendar import TradingCalendar
    return TradingCalendar(str(p))


class TestBuyWindow:
    """매수 시간대 판별 테스트."""

    def test_inside_buy_window(self, tmp_path):
        """14:40~15:00 사이는 is_buy_window() True."""
        cal = _make_calendar(tmp_path)
        assert cal.is_buy_window(datetime(2026, 6, 9, 14, 40, 0)) is True
        assert cal.is_buy_window(datetime(2026, 6, 9, 14, 50, 0)) is True
        assert cal.is_buy_window(datetime(2026, 6, 9, 15, 0, 0)) is True

    def test_outside_buy_window_before(self, tmp_path):
        """14:40 이전은 False."""
        cal = _make_calendar(tmp_path)
        assert cal.is_buy_window(datetime(2026, 6, 9, 14, 39, 59)) is False

    def test_outside_buy_window_after(self, tmp_path):
        """15:00 이후는 False."""
        cal = _make_calendar(tmp_path)
        assert cal.is_buy_window(datetime(2026, 6, 9, 15, 1, 0)) is False


class TestForceExitTime:
    """강제청산 시간 판별 테스트."""

    def test_at_0930_is_force_exit(self, tmp_path):
        """09:30 정각은 강제청산 시간."""
        cal = _make_calendar(tmp_path)
        assert cal.is_force_exit_time(datetime(2026, 6, 9, 9, 30, 0)) is True

    def test_after_0930_is_force_exit(self, tmp_path):
        """09:31 이후도 강제청산 시간."""
        cal = _make_calendar(tmp_path)
        assert cal.is_force_exit_time(datetime(2026, 6, 9, 9, 35, 0)) is True

    def test_before_0930_not_force_exit(self, tmp_path):
        """09:29 이전은 강제청산 시간 아님."""
        cal = _make_calendar(tmp_path)
        assert cal.is_force_exit_time(datetime(2026, 6, 9, 9, 29, 59)) is False


class TestTradingDay:
    """거래일 판별 테스트."""

    def test_weekday_is_trading_day(self, tmp_path):
        """평일(공휴일 아닌 날)은 거래일."""
        cal = _make_calendar(tmp_path)
        assert cal.is_trading_day(date(2026, 6, 9)) is True  # 화요일

    def test_saturday_is_not_trading_day(self, tmp_path):
        """토요일은 거래일 아님."""
        cal = _make_calendar(tmp_path)
        assert cal.is_trading_day(date(2026, 6, 6)) is False  # 토요일

    def test_sunday_is_not_trading_day(self, tmp_path):
        """일요일은 거래일 아님."""
        cal = _make_calendar(tmp_path)
        assert cal.is_trading_day(date(2026, 6, 7)) is False  # 일요일

    def test_holiday_is_not_trading_day(self, tmp_path):
        """공휴일(광복절)은 거래일 아님."""
        cal = _make_calendar(tmp_path)
        assert cal.is_trading_day(date(2026, 8, 17)) is False  # 대체공휴일


class TestWindowNames:
    """current_window_name() 반환값 테스트."""

    def test_buy_window_name(self, tmp_path):
        cal = _make_calendar(tmp_path)
        assert cal.current_window_name(datetime(2026, 6, 9, 14, 45)) == "BUY_WINDOW"

    def test_after_hours_name(self, tmp_path):
        cal = _make_calendar(tmp_path)
        assert cal.current_window_name(datetime(2026, 6, 9, 16, 0)) == "AFTER_HOURS"

    def test_pre_market_name(self, tmp_path):
        cal = _make_calendar(tmp_path)
        assert cal.current_window_name(datetime(2026, 6, 9, 8, 35)) == "PRE_MARKET"

    def test_force_exit_name(self, tmp_path):
        cal = _make_calendar(tmp_path)
        # 09:30 이후이므로 FORCE_EXIT가 NEXT_MORNING보다 먼저 반환 (TradingCalendar 구현에 따름)
        result = cal.current_window_name(datetime(2026, 6, 9, 9, 31))
        assert result == "FORCE_EXIT"

    def test_idle_name(self, tmp_path):
        cal = _make_calendar(tmp_path)
        assert cal.current_window_name(datetime(2026, 6, 9, 12, 0)) == "IDLE"


class TestGetMarketSession:
    """get_market_session() 반환값 테스트."""

    def test_regular_session(self, tmp_path):
        from trading_calendar import SESSION_REGULAR
        cal = _make_calendar(tmp_path)
        assert cal.get_market_session(datetime(2026, 6, 9, 10, 0)) == SESSION_REGULAR
        assert cal.get_market_session(datetime(2026, 6, 9, 14, 45)) == SESSION_REGULAR
        assert cal.get_market_session(datetime(2026, 6, 9, 15, 19)) == SESSION_REGULAR

    def test_closing_auction_session(self, tmp_path):
        from trading_calendar import SESSION_CLOSING_AUCTION
        cal = _make_calendar(tmp_path)
        assert cal.get_market_session(datetime(2026, 6, 9, 15, 20)) == SESSION_CLOSING_AUCTION
        assert cal.get_market_session(datetime(2026, 6, 9, 15, 25)) == SESSION_CLOSING_AUCTION

    def test_after_close_session(self, tmp_path):
        from trading_calendar import SESSION_AFTER_CLOSE
        cal = _make_calendar(tmp_path)
        assert cal.get_market_session(datetime(2026, 6, 9, 15, 30)) == SESSION_AFTER_CLOSE
        assert cal.get_market_session(datetime(2026, 6, 9, 15, 45)) == SESSION_AFTER_CLOSE
        assert cal.get_market_session(datetime(2026, 6, 9, 15, 59)) == SESSION_AFTER_CLOSE

    def test_after_hours_single_session(self, tmp_path):
        from trading_calendar import SESSION_AFTER_HOURS_SINGLE
        cal = _make_calendar(tmp_path)
        assert cal.get_market_session(datetime(2026, 6, 9, 16, 0)) == SESSION_AFTER_HOURS_SINGLE
        assert cal.get_market_session(datetime(2026, 6, 9, 16, 17)) == SESSION_AFTER_HOURS_SINGLE
        assert cal.get_market_session(datetime(2026, 6, 9, 18, 0)) == SESSION_AFTER_HOURS_SINGLE

    def test_pre_market_session(self, tmp_path):
        from trading_calendar import SESSION_PRE_MARKET
        cal = _make_calendar(tmp_path)
        assert cal.get_market_session(datetime(2026, 6, 9, 8, 30)) == SESSION_PRE_MARKET
        assert cal.get_market_session(datetime(2026, 6, 9, 8, 45)) == SESSION_PRE_MARKET

    def test_closed_session_midnight(self, tmp_path):
        from trading_calendar import SESSION_CLOSED
        cal = _make_calendar(tmp_path)
        assert cal.get_market_session(datetime(2026, 6, 9, 0, 0)) == SESSION_CLOSED
        assert cal.get_market_session(datetime(2026, 6, 9, 18, 1)) == SESSION_CLOSED
        assert cal.get_market_session(datetime(2026, 6, 9, 8, 0)) == SESSION_CLOSED

    def test_boundary_regular_end(self, tmp_path):
        from trading_calendar import SESSION_CLOSING_AUCTION, SESSION_REGULAR
        cal = _make_calendar(tmp_path)
        assert cal.get_market_session(datetime(2026, 6, 9, 15, 19, 59)) == SESSION_REGULAR
        assert cal.get_market_session(datetime(2026, 6, 9, 15, 20, 0)) == SESSION_CLOSING_AUCTION


class TestIsSessionAllowed:
    """is_session_allowed() 허용 여부 테스트."""

    def test_regular_always_allowed(self, tmp_path):
        from trading_calendar import SESSION_REGULAR
        cal = _make_calendar(tmp_path)
        assert cal.is_session_allowed(SESSION_REGULAR) is True

    def test_after_hours_single_allowed(self, tmp_path):
        from trading_calendar import SESSION_AFTER_HOURS_SINGLE
        cal = _make_calendar(tmp_path)
        assert cal.is_session_allowed(SESSION_AFTER_HOURS_SINGLE) is True

    def test_closing_auction_not_allowed(self, tmp_path):
        from trading_calendar import SESSION_CLOSING_AUCTION
        cal = _make_calendar(tmp_path)
        assert cal.is_session_allowed(SESSION_CLOSING_AUCTION) is False

    def test_after_close_not_allowed(self, tmp_path):
        from trading_calendar import SESSION_AFTER_CLOSE
        cal = _make_calendar(tmp_path)
        assert cal.is_session_allowed(SESSION_AFTER_CLOSE) is False

    def test_closed_never_allowed(self, tmp_path):
        from trading_calendar import SESSION_CLOSED
        cal = _make_calendar(tmp_path)
        assert cal.is_session_allowed(SESSION_CLOSED) is False
