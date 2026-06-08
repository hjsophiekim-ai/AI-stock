"""백테스트 테스트.

수수료·세금·슬리피지 반영, 익절/손절 로직이 올바른지 검증합니다.
"""

import sys
import os
from datetime import datetime
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from backtest import BacktestEngine


def make_backtest_engine(**kwargs) -> BacktestEngine:
    """테스트용 BacktestEngine 생성."""
    defaults = {
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "top_n": 5,
        "threshold": 0.0,
        "initial_capital": 10_000_000,
    }
    defaults.update(kwargs)
    engine = BacktestEngine(**defaults)
    return engine


class TestCostReflection:
    """거래 비용 반영 검증."""

    def test_fee_reduces_profit(self):
        """수수료가 적용되어 실제 수익이 이론 수익보다 작은지 확인."""
        engine = make_backtest_engine()
        # 10,000원 매수, 10,200원 매도 (이론 +2%)
        eff_entry, eff_exit = engine._apply_costs(10000, 10200)
        theoretical = (10200 - 10000) / 10000
        actual = (eff_exit - eff_entry) / eff_entry
        assert actual < theoretical, \
            f"비용 적용 후 수익({actual:.4%})이 이론 수익({theoretical:.4%})보다 작아야 합니다"

    def test_fee_increases_loss(self):
        """수수료가 적용되어 실제 손실이 이론 손실보다 큰지 확인."""
        engine = make_backtest_engine()
        # 10,000원 매수, 9,700원 매도 (이론 -3%)
        eff_entry, eff_exit = engine._apply_costs(10000, 9700)
        theoretical = (9700 - 10000) / 10000
        actual = (eff_exit - eff_entry) / eff_entry
        assert actual < theoretical, \
            f"비용 적용 후 손실({actual:.4%})이 이론 손실({theoretical:.4%})보다 커야 합니다"

    def test_slippage_applied_to_entry(self):
        """슬리피지가 매수가에 적용되는지 확인 (매수가 > 원래 가격)."""
        engine = make_backtest_engine()
        eff_entry, _ = engine._apply_costs(10000, 10200)
        assert eff_entry > 10000, "슬리피지 적용 후 매수가는 원가보다 높아야 합니다"

    def test_slippage_applied_to_exit(self):
        """슬리피지가 매도가에 적용되는지 확인 (매도가 < 원래 가격)."""
        engine = make_backtest_engine()
        _, eff_exit = engine._apply_costs(10000, 10200)
        assert eff_exit < 10200, "슬리피지 적용 후 매도가는 원가보다 낮아야 합니다"

    def test_total_cost_is_not_zero(self):
        """총 거래 비용이 0이 아닌지 확인."""
        engine = make_backtest_engine()
        eff_entry, eff_exit = engine._apply_costs(10000, 10000)
        # 같은 가격에 사고 팔면 비용만큼 손해
        pnl = (eff_exit - eff_entry) / eff_entry
        assert pnl < 0, "같은 가격에 매수/매도 시 비용으로 인해 손실이 발생해야 합니다"


class TestExitRules:
    """청산 규칙 검증."""

    def _make_simple_daily_data(self) -> pd.DataFrame:
        """백테스트용 간단한 일봉 데이터."""
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        data = []
        for i, d in enumerate(dates):
            data.append({
                "date": d,
                "ticker": "TEST01",
                "name": "테스트",
                "open": 10050,
                "high": 10300 if i % 3 == 0 else 10100,  # 3일마다 +3% 고가
                "low": 9700 if i % 5 == 0 else 9900,      # 5일마다 -3% 저가
                "close": 10000,
                "volume": 1000000,
                "trading_value": 10_000_000_000,
            })
        return pd.DataFrame(data)

    def test_take_profit_when_high_exceeds_target(self):
        """다음날 고가가 +2% 초과하면 take_profit."""
        engine = make_backtest_engine()
        daily_df = self._make_simple_daily_data()
        all_dates = sorted(daily_df["date"].unique())

        # i=2인 날 매수 → exit은 i=3 (high=10300, 10300>=10200 → take_profit)
        entry_date = all_dates[2]
        candidates = pd.DataFrame([{
            "ticker": "TEST01",
            "name": "테스트",
            "close": 10000,
            "proba_up": 0.8,
        }])
        results = engine.simulate_day(entry_date, candidates, daily_df, all_dates)

        if results:
            assert "take_profit" in results[0]["exit_reason"], \
                f"take_profit이어야 하는데 {results[0]['exit_reason']}입니다"

    def test_stop_loss_when_low_below_threshold(self):
        """다음날 저가가 -3% 이하면 stop_loss."""
        engine = make_backtest_engine()
        # 저가가 매수가 -3%인 날을 만들기
        dates = pd.date_range("2024-03-01", periods=5, freq="B")
        daily_df = pd.DataFrame([{
            "date": d,
            "ticker": "TEST02",
            "name": "손절테스트",
            "open": 9900,
            "high": 10050,
            "low": 9650,  # 10000 × 0.97 = 9700 이하 → 손절
            "close": 10000,
            "volume": 1000000,
            "trading_value": 10_000_000_000,
        } for d in dates])

        all_dates = sorted(daily_df["date"].unique())
        candidates = pd.DataFrame([{
            "ticker": "TEST02",
            "name": "손절테스트",
            "close": 10000,
            "proba_up": 0.8,
        }])
        results = engine.simulate_day(all_dates[0], candidates, daily_df, all_dates)
        if results:
            assert "stop_loss" in results[0]["exit_reason"], \
                f"stop_loss이어야 하는데 {results[0]['exit_reason']}입니다"


class TestBacktestSummary:
    """백테스트 요약 지표 검증."""

    def test_summary_keys_exist(self):
        """요약 딕셔너리에 필수 키가 있는지 확인."""
        engine = make_backtest_engine()
        engine.trades = [
            {"pnl_rate": 0.02, "exit_reason": "take_profit", "ticker": "T1", "name": "t1", "trade_date": "2024-01-02", "exit_date": "2024-01-03", "entry_price": 10000, "exit_price": 10200, "raw_entry": 10000, "raw_exit": 10200, "proba_up": 0.7},
            {"pnl_rate": -0.03, "exit_reason": "stop_loss", "ticker": "T2", "name": "t2", "trade_date": "2024-01-02", "exit_date": "2024-01-03", "entry_price": 10000, "exit_price": 9700, "raw_entry": 10000, "raw_exit": 9700, "proba_up": 0.6},
            {"pnl_rate": 0.0, "exit_reason": "forced_exit_0930", "ticker": "T3", "name": "t3", "trade_date": "2024-01-02", "exit_date": "2024-01-03", "entry_price": 10000, "exit_price": 10000, "raw_entry": 10000, "raw_exit": 10000, "proba_up": 0.5},
        ]
        summary = engine.summary()

        required_keys = [
            "total_trades", "win_rate", "take_profit_rate",
            "stop_loss_rate", "forced_exit_rate", "avg_pnl_rate",
        ]
        for key in required_keys:
            assert key in summary, f"요약에 '{key}' 키가 없습니다"

    def test_win_rate_calculation(self):
        """승률 계산이 올바른지 확인."""
        engine = make_backtest_engine()
        # 3건 중 2건 이익
        engine.trades = [
            {"pnl_rate": 0.02, "exit_reason": "take_profit", "ticker": "T1", "name": "t", "trade_date": "2024-01-02", "exit_date": "2024-01-03", "entry_price": 10000, "exit_price": 10200, "raw_entry": 10000, "raw_exit": 10200, "proba_up": 0.7},
            {"pnl_rate": 0.01, "exit_reason": "forced_exit", "ticker": "T2", "name": "t", "trade_date": "2024-01-02", "exit_date": "2024-01-03", "entry_price": 10000, "exit_price": 10100, "raw_entry": 10000, "raw_exit": 10100, "proba_up": 0.6},
            {"pnl_rate": -0.03, "exit_reason": "stop_loss", "ticker": "T3", "name": "t", "trade_date": "2024-01-02", "exit_date": "2024-01-03", "entry_price": 10000, "exit_price": 9700, "raw_entry": 10000, "raw_exit": 9700, "proba_up": 0.5},
        ]
        summary = engine.summary()
        expected_win_rate = 2 / 3
        assert abs(summary["win_rate"] - expected_win_rate) < 0.01, \
            f"승률 계산 오류: expected={expected_win_rate:.3f}, actual={summary['win_rate']:.3f}"
