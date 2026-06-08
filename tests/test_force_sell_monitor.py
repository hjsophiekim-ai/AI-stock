"""ForceSellMonitor 테스트.

핵심 검증:
- +2% 목표가 도달 시 매도 신호
- -3% 손절가 도달 시 매도 신호
- 강제청산 시간 도달 시 매도 신호
"""

import os
import sys
import pytest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_config(tmp_path):
    import yaml
    cfg = {
        "live_trade": False,
        "paper_trade": True,
        "safety": {"confirm_live_trade": False, "max_order_retries": 1, "max_api_error_count": 5},
        "kis": {"use_mock": True, "rate_limit_sleep_seconds": 0,
                "base_url_mock": "https://openapivts.koreainvestment.com:29443",
                "base_url_real": "https://openapi.koreainvestment.com:9443"},
        "risk": {"max_positions": 20, "max_position_weight": 0.1, "min_price": 1000,
                 "max_daily_loss_rate": -0.1, "max_market_index_drop_rate": -0.1,
                 "exclude_preferred_stock": True, "exclude_spac": True, "exclude_etf_etn": True},
        "strategy": {
            "target_profit_rate": 0.02, "stop_loss_rate": -0.03,
            "forced_exit_time_next_day": "09:30",
            "buy_start_time": "14:40", "buy_end_time": "15:00",
            "after_hours_start_time": "15:40", "after_hours_end_time": "18:00",
            "pre_market_start_time": "08:30", "pre_market_end_time": "08:40",
            "regular_market_start_time": "09:00",
        },
        "data": {
            "positions_file": str(tmp_path / "positions.json"),
            "raw_daily_path": str(tmp_path / "daily.csv"),
        },
        "paths": {"orders_dir": str(tmp_path / "orders")},
        "backtest": {"initial_capital": 100_000_000},
        "order": {"buy_price_adjustment_rate": 0.001, "sell_price_adjustment_rate": -0.001,
                  "cancel_unfilled_after_seconds": 20},
        "force_trade": {"enabled": True},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return str(p)


def _add_position(pos_mgr, entry_price=70_000, entry_time=None):
    from datetime import timedelta
    t = entry_time or (datetime.now() - timedelta(days=1))
    pos_mgr.update_position_after_buy("005930", "삼성전자", 10, float(entry_price), entry_time=t)


class TestTakeProfitSignal:
    """+2% 목표가 도달 시 매도 신호 생성."""

    def test_take_profit_at_2pct(self, tmp_path):
        cfg = _make_config(tmp_path)
        from position_manager import PositionManager
        pos_mgr = PositionManager(cfg)
        _add_position(pos_mgr, 70_000)

        target = pos_mgr.get_target_price(70_000)
        assert target == 71_400
        assert pos_mgr.should_take_profit("005930", target) is True

    def test_no_take_profit_below_target(self, tmp_path):
        cfg = _make_config(tmp_path)
        from position_manager import PositionManager
        pos_mgr = PositionManager(cfg)
        _add_position(pos_mgr, 70_000)
        assert pos_mgr.should_take_profit("005930", 71_000) is False


class TestStopLossSignal:
    """-3% 손절가 도달 시 매도 신호 생성."""

    def test_stop_loss_at_3pct(self, tmp_path):
        cfg = _make_config(tmp_path)
        from position_manager import PositionManager
        pos_mgr = PositionManager(cfg)
        _add_position(pos_mgr, 70_000)

        stop = pos_mgr.get_stop_loss_price(70_000)
        assert stop == 67_900
        assert pos_mgr.should_stop_loss("005930", stop) is True

    def test_no_stop_loss_above_stop_price(self, tmp_path):
        cfg = _make_config(tmp_path)
        from position_manager import PositionManager
        pos_mgr = PositionManager(cfg)
        _add_position(pos_mgr, 70_000)
        assert pos_mgr.should_stop_loss("005930", 68_000) is False


class TestForceExitSignal:
    """강제청산 시간 도달 시 매도 신호 생성."""

    def test_force_exit_after_0930_next_day(self, tmp_path):
        cfg = _make_config(tmp_path)
        from position_manager import PositionManager
        from datetime import timedelta
        pos_mgr = PositionManager(cfg)
        yesterday = datetime(2026, 6, 7, 14, 50)
        pos_mgr.update_position_after_buy("005930", "삼성전자", 10, 70_000, entry_time=yesterday)

        next_day_after = datetime(2026, 6, 8, 9, 31)
        assert pos_mgr.should_force_exit("005930", next_day_after) is True

    def test_no_force_exit_before_0930(self, tmp_path):
        cfg = _make_config(tmp_path)
        from position_manager import PositionManager
        from datetime import timedelta
        pos_mgr = PositionManager(cfg)
        yesterday = datetime(2026, 6, 7, 14, 50)
        pos_mgr.update_position_after_buy("005930", "삼성전자", 10, 70_000, entry_time=yesterday)

        before = datetime(2026, 6, 8, 9, 29)
        assert pos_mgr.should_force_exit("005930", before) is False


class TestForceSellMonitorPaperMode:
    """PAPER 모드 ForceSellMonitor 동작 테스트."""

    def test_no_api_call_in_paper_mode(self, tmp_path):
        """PAPER 모드에서 run_once() 호출 시 API 호출 없음."""
        cfg = _make_config(tmp_path)
        from force_sell_monitor import ForceSellMonitor
        from datetime import timedelta

        monitor = ForceSellMonitor(cfg)
        # 포지션 추가 (다음날 09:31 기준)
        yesterday = datetime(2026, 6, 7, 14, 50)
        monitor.pos_mgr.update_position_after_buy("005930", "삼성전자", 10, 70_000, entry_time=yesterday)

        cleared = monitor.run_once(now=datetime(2026, 6, 8, 9, 31))
        # PAPER 모드이므로 가상 처리 — API 호출 없음
        # (이 테스트는 오류 없이 실행되면 통과)
        assert isinstance(cleared, list)
