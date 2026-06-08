"""매매 전략 로직 테스트.

14:40 매수, +2% 익절, -3% 손절, 09:30 강제청산 규칙을 검증합니다.
"""

import sys
import os
from datetime import datetime, time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trade_rules import TradeRules, Position


class TestBuyWindow:
    """매수 시간대 검증."""

    def test_buy_allowed_at_1440(self):
        """14:40은 매수 가능 시간."""
        rules = TradeRules()
        now = datetime(2024, 1, 5, 14, 40)
        assert rules.is_buy_window(now), "14:40은 매수 가능해야 합니다"

    def test_buy_allowed_at_1450(self):
        """14:50은 매수 가능 시간."""
        rules = TradeRules()
        now = datetime(2024, 1, 5, 14, 50)
        assert rules.is_buy_window(now), "14:50은 매수 가능해야 합니다"

    def test_buy_not_allowed_at_1500(self):
        """15:00 이후는 매수 불가 (정확히 15:00은 경계값 — 허용)."""
        rules = TradeRules()
        now = datetime(2024, 1, 5, 15, 1)
        assert not rules.is_buy_window(now), "15:01은 매수 불가해야 합니다"

    def test_buy_not_allowed_at_0900(self):
        """09:00은 매수 불가."""
        rules = TradeRules()
        now = datetime(2024, 1, 5, 9, 0)
        assert not rules.is_buy_window(now), "09:00은 매수 불가해야 합니다"


class TestTakeProfit:
    """+2% 익절 조건 검증."""

    def _make_position(self, entry_price: float = 10000) -> Position:
        return Position(
            ticker="000001",
            name="테스트",
            entry_price=entry_price,
            qty=10,
            entry_time=datetime(2024, 1, 5, 14, 45),
        )

    def test_take_profit_at_exactly_2pct(self):
        """정확히 +2%에서 익절 조건 충족."""
        rules = TradeRules()
        pos = self._make_position(10000)
        assert rules.should_take_profit(pos, 10200), "+2% 도달 시 익절이어야 합니다"

    def test_take_profit_above_2pct(self):
        """+2% 초과에서도 익절 조건 충족."""
        rules = TradeRules()
        pos = self._make_position(10000)
        assert rules.should_take_profit(pos, 10300), "+3% 도달 시에도 익절이어야 합니다"

    def test_no_take_profit_below_2pct(self):
        """+2% 미달 시 익절 불가."""
        rules = TradeRules()
        pos = self._make_position(10000)
        assert not rules.should_take_profit(pos, 10199), "+1.99%에서는 익절 불가여야 합니다"


class TestStopLoss:
    """-3% 손절 조건 검증."""

    def _make_position(self, entry_price: float = 10000) -> Position:
        return Position(
            ticker="000001",
            name="테스트",
            entry_price=entry_price,
            qty=10,
            entry_time=datetime(2024, 1, 5, 14, 45),
        )

    def test_stop_loss_at_exactly_3pct(self):
        """정확히 -3%에서 손절 조건 충족."""
        rules = TradeRules()
        pos = self._make_position(10000)
        assert rules.should_stop_loss(pos, 9700), "-3% 도달 시 손절이어야 합니다"

    def test_stop_loss_below_3pct(self):
        """-3% 초과 하락에서도 손절."""
        rules = TradeRules()
        pos = self._make_position(10000)
        assert rules.should_stop_loss(pos, 9500), "-5% 도달 시에도 손절이어야 합니다"

    def test_no_stop_loss_above_3pct(self):
        """-2% 하락 시 손절 불가."""
        rules = TradeRules()
        pos = self._make_position(10000)
        assert not rules.should_stop_loss(pos, 9801), "-1.99% 하락 시 손절 불가여야 합니다"


class TestForcedExit:
    """다음날 09:30 강제청산 검증."""

    def _make_position(self, entry_day: datetime) -> Position:
        return Position(
            ticker="000001",
            name="테스트",
            entry_price=10000,
            qty=10,
            entry_time=entry_day,
        )

    def test_forced_exit_next_day_0930(self):
        """다음날 09:30에 강제청산 조건 충족."""
        rules = TradeRules()
        pos = self._make_position(datetime(2024, 1, 5, 14, 45))
        next_day_0930 = datetime(2024, 1, 8, 9, 30)  # 다음 거래일
        assert rules.should_force_exit(pos, next_day_0930), \
            "다음날 09:30에 강제청산이어야 합니다"

    def test_no_forced_exit_same_day(self):
        """같은 날은 강제청산 불가."""
        rules = TradeRules()
        pos = self._make_position(datetime(2024, 1, 5, 14, 45))
        same_day_0930 = datetime(2024, 1, 5, 9, 30)
        assert not rules.should_force_exit(pos, same_day_0930), \
            "같은 날 09:30에는 강제청산 불가여야 합니다"

    def test_no_forced_exit_before_0930_next_day(self):
        """다음날 09:30 이전에는 강제청산 불가."""
        rules = TradeRules()
        pos = self._make_position(datetime(2024, 1, 5, 14, 45))
        next_day_early = datetime(2024, 1, 8, 9, 0)
        assert not rules.should_force_exit(pos, next_day_early), \
            "다음날 09:00에는 강제청산 불가여야 합니다"


class TestExitConditionPriority:
    """매도 조건 우선순위 검증 (익절 > 손절 > 강제청산)."""

    def test_take_profit_overrides_forced_exit(self):
        """익절 조건이 강제청산보다 우선."""
        rules = TradeRules()
        pos = Position(
            ticker="000001",
            name="테스트",
            entry_price=10000,
            qty=10,
            entry_time=datetime(2024, 1, 5, 14, 45),
        )
        now = datetime(2024, 1, 8, 9, 31)
        result = rules.check_exit_condition(pos, 10200, now)
        assert result == "take_profit", f"익절 우선이어야 하는데 {result}입니다"
