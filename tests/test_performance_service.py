"""performance_service 테스트."""

import sys
import json
import pytest
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "services"))


def _make_order_df(rows):
    return pd.DataFrame(rows)


class TestRealizedPnl:
    def test_single_profitable_sell(self):
        """이익 매도 1건 실현손익."""
        import performance_service
        df = _make_order_df([{
            "side": "sell", "quantity": 10,
            "sell_price": 11000, "entry_price": 10000,
        }])
        pnl = performance_service.calc_realized_pnl(df)
        assert pnl == pytest.approx(10000.0)

    def test_single_losing_sell(self):
        """손실 매도 1건 실현손익."""
        import performance_service
        df = _make_order_df([{
            "side": "sell", "quantity": 5,
            "sell_price": 9000, "entry_price": 10000,
        }])
        pnl = performance_service.calc_realized_pnl(df)
        assert pnl == pytest.approx(-5000.0)

    def test_no_sell_no_pnl(self):
        """매도 없으면 실현손익 0."""
        import performance_service
        df = _make_order_df([{"side": "buy", "quantity": 10}])
        assert performance_service.calc_realized_pnl(df) == 0.0

    def test_empty_df_returns_zero(self):
        """빈 DataFrame → 0."""
        import performance_service
        assert performance_service.calc_realized_pnl(pd.DataFrame()) == 0.0


class TestUnrealizedPnl:
    def test_unrealized_pnl_calculation(self):
        """현재가 > 매수가이면 양수 평가손익."""
        import performance_service
        positions = [{"entry_price": 10000, "current_price": 11000, "quantity": 5}]
        pnl = performance_service.calc_unrealized_pnl(positions)
        assert pnl == pytest.approx(5000.0)

    def test_no_current_price_zero(self):
        """현재가 0이면 평가손익 0."""
        import performance_service
        positions = [{"entry_price": 10000, "current_price": 0, "quantity": 5}]
        assert performance_service.calc_unrealized_pnl(positions) == 0.0


class TestWinRate:
    def test_all_winning(self):
        """전부 이익이면 승률 100%."""
        import performance_service
        df = _make_order_df([
            {"side": "sell", "sell_price": 11000, "entry_price": 10000},
            {"side": "sell", "sell_price": 12000, "entry_price": 10000},
        ])
        rate, wins, total = performance_service.calc_win_rate(df)
        assert rate == pytest.approx(100.0)
        assert wins == 2
        assert total == 2

    def test_mixed_win_loss(self):
        """1승 1패 → 승률 50%."""
        import performance_service
        df = _make_order_df([
            {"side": "sell", "sell_price": 11000, "entry_price": 10000},
            {"side": "sell", "sell_price": 9000, "entry_price": 10000},
        ])
        rate, wins, total = performance_service.calc_win_rate(df)
        assert rate == pytest.approx(50.0)

    def test_empty_no_sell(self):
        """거래 없으면 (0, 0, 0)."""
        import performance_service
        rate, wins, total = performance_service.calc_win_rate(pd.DataFrame())
        assert total == 0


class TestCumulativeReturn:
    def test_positive_cumulative_return(self):
        """이익 거래 후 누적수익률 양수."""
        import performance_service
        df = _make_order_df([{
            "side": "sell", "quantity": 10,
            "sell_price": 11000, "entry_price": 10000,
        }])
        cr = performance_service.calc_cumulative_return(100_000_000, df)
        assert cr > 0
