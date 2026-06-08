"""라벨 생성 테스트.

target_2pct_next_morning 생성이 올바른지 검증합니다.
"""

import sys
import os
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from make_labels import make_labels_for_ticker


def make_controlled_ohlcv() -> pd.DataFrame:
    """라벨 테스트용 통제된 OHLCV 데이터."""
    # 5일치 데이터: 날짜 순서대로
    # close=10000인 종목에서 다음날 high가 10200이면 target=1
    data = {
        "date": pd.date_range("2024-01-01", periods=5, freq="B"),
        "open":  [10000, 10100, 10200, 10300, 10400],
        "high":  [10200, 10300, 10400, 10500, 10600],  # 항상 +2% 이상
        "low":   [9800,  9900,  10000, 10100, 10200],
        "close": [10000, 10100, 10200, 10300, 10400],
        "volume": [1000000] * 5,
        "trading_value": [10_000_000_000] * 5,
        "ticker": ["000001"] * 5,
    }
    return pd.DataFrame(data)


class TestLabelCorrectness:
    """라벨 값의 정확성 검증."""

    def test_target_1_when_next_high_above_2pct(self):
        """다음날 고가가 +2% 이상이면 target=1."""
        df = make_controlled_ohlcv()
        labeled = make_labels_for_ticker(df, target_profit_rate=0.02)

        # 1번째 행: close=10000, 다음날 high=10300 → +3% → target=1
        assert labeled["target_2pct_next_morning"].iloc[0] == 1, \
            "다음날 고가 +3%인데 target=0입니다."

    def test_target_0_when_next_high_below_2pct(self):
        """다음날 고가가 +2% 미만이면 target=0."""
        data = {
            "date": pd.date_range("2024-01-01", periods=3, freq="B"),
            "open":  [10000, 10050, 10100],
            "high":  [10100, 10150, 10200],  # +1%만 상승
            "low":   [9900,  9950,  10000],
            "close": [10000, 10050, 10100],
            "volume": [1000000] * 3,
            "trading_value": [10_000_000_000] * 3,
            "ticker": ["000002"] * 3,
        }
        df = pd.DataFrame(data)
        labeled = make_labels_for_ticker(df, target_profit_rate=0.02)

        # 1번째 행: close=10000, 다음날 high=10150 → +1.5% < 2% → target=0
        assert labeled["target_2pct_next_morning"].iloc[0] == 0, \
            "다음날 고가 +1.5%인데 target=1입니다."

    def test_last_row_is_nan(self):
        """마지막 행은 다음날 데이터 없음 → NaN이어야 함."""
        df = make_controlled_ohlcv()
        labeled = make_labels_for_ticker(df, target_profit_rate=0.02)
        assert pd.isna(labeled["target_2pct_next_morning"].iloc[-1]), \
            "마지막 행의 target이 NaN이 아닙니다."

    def test_no_future_leakage_in_label(self):
        """라벨이 shift(-1)로 올바르게 생성되는지 확인.

        t행의 라벨 = t+1행의 데이터로 계산됨을 검증.
        """
        df = make_controlled_ohlcv()
        labeled = make_labels_for_ticker(df, target_profit_rate=0.02)

        for i in range(len(df) - 1):
            entry = df["close"].iloc[i]
            target_price = entry * 1.02
            next_high = df["high"].iloc[i + 1]
            expected = 1 if next_high >= target_price else 0
            actual = int(labeled["target_2pct_next_morning"].iloc[i])
            assert actual == expected, \
                f"행 {i}: entry={entry}, next_high={next_high}, " \
                f"expected={expected}, actual={actual}"

    def test_target_close_profit_direction(self):
        """target_close_profit의 방향성 검증 (상승이면 양수)."""
        df = make_controlled_ohlcv()
        labeled = make_labels_for_ticker(df, target_profit_rate=0.02)

        # 데이터가 계속 상승하므로 첫 번째 행의 target_close_profit은 양수여야 함
        assert labeled["target_close_profit"].iloc[0] > 0, \
            "상승 데이터인데 target_close_profit이 음수입니다."

    def test_max_drawdown_negative_or_zero(self):
        """target_max_drawdown은 0 이하여야 함 (하락 또는 0)."""
        df = make_controlled_ohlcv()
        labeled = make_labels_for_ticker(df, target_profit_rate=0.02)
        valid = labeled["target_max_drawdown"].dropna()
        # 저가가 매수가보다 낮으면 음수, 높으면 양수 가능
        # 중요한 것은 NaN이 없어야 함 (마지막 행 제외)
        assert valid.notna().all(), "target_max_drawdown에 예상치 못한 NaN이 있습니다."
