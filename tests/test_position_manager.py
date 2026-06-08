"""PositionManager 테스트.

핵심 검증:
- 목표가 +2%, 손절가 -3% 계산
- should_force_exit() 시간 로직
"""

import pytest
import sys
import os
from datetime import datetime, date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_pos_mgr(tmp_path, target_rate=0.02, stop_rate=-0.03):
    import yaml
    cfg = {
        "strategy": {
            "target_profit_rate": target_rate,
            "stop_loss_rate": stop_rate,
            "forced_exit_time_next_day": "09:30",
        },
        "data": {
            "positions_file": str(tmp_path / "positions.json"),
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    from position_manager import PositionManager
    return PositionManager(str(p))


class TestTargetAndStopPrices:
    """목표가·손절가 계산 테스트."""

    def test_target_price_plus_2pct(self, tmp_path):
        """목표가 = 매수가 × 1.02."""
        mgr = _make_pos_mgr(tmp_path)
        assert mgr.get_target_price(10_000) == 10_200

    def test_stop_loss_price_minus_3pct(self, tmp_path):
        """손절가 = 매수가 × 0.97."""
        mgr = _make_pos_mgr(tmp_path)
        assert mgr.get_stop_loss_price(10_000) == 9_700

    def test_target_price_stored_on_buy(self, tmp_path):
        """매수 후 포지션에 목표가·손절가가 저장되어야 함."""
        mgr = _make_pos_mgr(tmp_path)
        pos = mgr.update_position_after_buy(
            stock_code="005930",
            stock_name="삼성전자",
            quantity=10,
            entry_price=70_000,
        )
        assert pos.target_price == 71_400
        assert pos.stop_price == 67_900

    def test_take_profit_signal(self, tmp_path):
        """현재가 >= 목표가이면 should_take_profit() True."""
        mgr = _make_pos_mgr(tmp_path)
        mgr.update_position_after_buy("005930", "삼성전자", 10, 70_000)
        assert mgr.should_take_profit("005930", 71_400) is True
        assert mgr.should_take_profit("005930", 71_000) is False

    def test_stop_loss_signal(self, tmp_path):
        """현재가 <= 손절가이면 should_stop_loss() True."""
        mgr = _make_pos_mgr(tmp_path)
        mgr.update_position_after_buy("005930", "삼성전자", 10, 70_000)
        assert mgr.should_stop_loss("005930", 67_900) is True
        assert mgr.should_stop_loss("005930", 68_000) is False


class TestForceExitTiming:
    """강제청산 시간 로직 테스트."""

    def _buy_yesterday(self, mgr):
        """어제 매수한 포지션 추가."""
        from datetime import timedelta
        yesterday = datetime.now() - timedelta(days=1)
        pos = mgr.update_position_after_buy("005930", "삼성전자", 10, 70_000, entry_time=yesterday)
        return pos

    def test_no_force_exit_on_entry_day(self, tmp_path):
        """매수 당일에는 강제청산 조건 False."""
        mgr = _make_pos_mgr(tmp_path)
        now = datetime(2026, 6, 8, 9, 30, 0)
        mgr.update_position_after_buy(
            "005930", "삼성전자", 10, 70_000,
            entry_time=datetime(2026, 6, 8, 14, 50, 0),
        )
        assert mgr.should_force_exit("005930", now) is False

    def test_force_exit_after_0930_next_day(self, tmp_path):
        """다음날 09:30 이후에는 강제청산 조건 True."""
        mgr = _make_pos_mgr(tmp_path)
        mgr.update_position_after_buy(
            "005930", "삼성전자", 10, 70_000,
            entry_time=datetime(2026, 6, 7, 14, 50, 0),
        )
        now = datetime(2026, 6, 8, 9, 31, 0)
        assert mgr.should_force_exit("005930", now) is True

    def test_no_force_exit_before_0930_next_day(self, tmp_path):
        """다음날 09:30 이전에는 강제청산 조건 False."""
        mgr = _make_pos_mgr(tmp_path)
        mgr.update_position_after_buy(
            "005930", "삼성전자", 10, 70_000,
            entry_time=datetime(2026, 6, 7, 14, 50, 0),
        )
        now = datetime(2026, 6, 8, 9, 29, 0)
        assert mgr.should_force_exit("005930", now) is False


class TestPositionPersistence:
    """포지션 파일 저장·복원 테스트."""

    def test_save_and_load(self, tmp_path):
        """매수 후 저장하고 다시 로드하면 동일한 데이터여야 함."""
        import yaml
        cfg = {
            "strategy": {
                "target_profit_rate": 0.02,
                "stop_loss_rate": -0.03,
                "forced_exit_time_next_day": "09:30",
            },
            "data": {"positions_file": str(tmp_path / "positions.json")},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg), encoding="utf-8")

        from position_manager import PositionManager

        mgr1 = PositionManager(str(p))
        mgr1.update_position_after_buy("005930", "삼성전자", 10, 70_000)
        mgr1.save_local_positions()

        mgr2 = PositionManager(str(p))
        pos = mgr2.get_position("005930")
        assert pos is not None
        assert pos.quantity == 10
        assert pos.entry_price == 70_000

    def test_position_removed_after_sell(self, tmp_path):
        """매도 후 포지션이 제거되어야 함."""
        mgr = _make_pos_mgr(tmp_path)
        mgr.update_position_after_buy("005930", "삼성전자", 10, 70_000)
        assert mgr.has_position("005930")
        mgr.update_position_after_sell("005930", 71_400, "take_profit")
        assert not mgr.has_position("005930")
