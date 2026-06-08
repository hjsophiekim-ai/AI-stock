"""피처 생성 테스트.

미래 데이터 누수(data leakage)가 없는지 검증합니다.
"""

import sys
import os
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from make_features import make_daily_features, FEATURE_COLUMNS


def make_sample_ohlcv(n: int = 100) -> pd.DataFrame:
    """테스트용 OHLCV DataFrame 생성."""
    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    np.random.seed(42)
    close = 10000 + np.cumsum(np.random.randn(n) * 100)
    open_ = close * (1 + np.random.randn(n) * 0.005)
    high = np.maximum(close, open_) * (1 + abs(np.random.randn(n) * 0.003))
    low = np.minimum(close, open_) * (1 - abs(np.random.randn(n) * 0.003))
    volume = np.random.randint(100000, 1000000, n).astype(float)
    tv = close * volume

    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "trading_value": tv,
        "ticker": "000000",
        "name": "테스트종목",
        "market": "KOSPI",
    })


class TestNoDataLeakage:
    """데이터 누수 없음 검증."""

    def test_rolling_features_use_only_past_data(self):
        """롤링 피처가 과거 데이터만 사용하는지 확인."""
        df = make_sample_ohlcv(100)
        feat_df = make_daily_features(df)

        # ma20은 현재 포함 20일 이동평균 → 미래 데이터 미포함
        # 인덱스 20의 ma20은 인덱스 1~20의 평균이어야 함
        idx = 20
        expected_ma20 = df["close"].iloc[idx-19:idx+1].mean()
        actual_ma20 = feat_df["ma20"].iloc[idx]
        assert abs(expected_ma20 - actual_ma20) < 1e-6, \
            f"ma20 누수 의심: expected={expected_ma20:.2f}, actual={actual_ma20:.2f}"

    def test_ret_5d_uses_only_past(self):
        """5일 수익률이 미래 데이터를 쓰지 않는지 확인."""
        df = make_sample_ohlcv(100)
        feat_df = make_daily_features(df)

        idx = 10
        expected_ret5 = (df["close"].iloc[idx] - df["close"].iloc[idx-5]) / df["close"].iloc[idx-5]
        actual_ret5 = feat_df["ret_5d"].iloc[idx]
        assert abs(expected_ret5 - actual_ret5) < 1e-6, \
            f"ret_5d 누수 의심: expected={expected_ret5:.6f}, actual={actual_ret5:.6f}"

    def test_feature_not_using_future_close(self):
        """피처 값이 미래 종가와 완전 상관관계를 갖지 않는지 확인."""
        df = make_sample_ohlcv(200)
        feat_df = make_daily_features(df)
        feat_df = feat_df.dropna()

        # 1일 후 종가
        future_close = df["close"].shift(-1).reindex(feat_df.index)

        for col in ["ret_1d", "ma5", "vol_ratio"]:
            if col not in feat_df.columns:
                continue
            combined = pd.DataFrame({"feat": feat_df[col], "future": future_close}).dropna()
            if len(combined) < 10:
                continue
            corr = combined.corr().iloc[0, 1]
            assert abs(corr) < 0.95, \
                f"데이터 누수 의심: {col}와 미래 종가 상관계수={corr:.3f} (>= 0.95)"


class TestFeatureValues:
    """피처 값의 합리성 검증."""

    def test_is_bullish_binary(self):
        """양봉 여부가 0 또는 1인지 확인."""
        df = make_sample_ohlcv(50)
        feat_df = make_daily_features(df)
        assert feat_df["is_bullish"].dropna().isin([0, 1]).all(), \
            "is_bullish는 0 또는 1이어야 합니다"

    def test_price_to_ma_positive(self):
        """가격/이평 비율이 양수인지 확인."""
        df = make_sample_ohlcv(100)
        feat_df = make_daily_features(df).dropna()
        assert (feat_df["price_to_ma5"] > 0).all(), "price_to_ma5는 양수여야 합니다"
        assert (feat_df["price_to_ma20"] > 0).all(), "price_to_ma20는 양수여야 합니다"

    def test_vol_ratio_positive(self):
        """거래량 비율이 양수인지 확인."""
        df = make_sample_ohlcv(100)
        feat_df = make_daily_features(df).dropna()
        assert (feat_df["vol_ratio"] >= 0).all(), "vol_ratio는 0 이상이어야 합니다"

    def test_feature_columns_exist(self):
        """주요 피처 컬럼이 모두 생성되는지 확인."""
        df = make_sample_ohlcv(100)
        feat_df = make_daily_features(df)
        required = ["ret_1d", "ret_5d", "ma20", "vol_ratio", "is_bullish", "volatility_5d"]
        for col in required:
            assert col in feat_df.columns, f"필수 피처 컬럼 없음: {col}"

    def test_no_future_high_in_features(self):
        """피처에 미래 고가가 직접 포함되지 않는지 확인."""
        df = make_sample_ohlcv(100)
        feat_df = make_daily_features(df)
        # pos_in_20d_high는 현재가 / 과거 20일 최고가 → 미래 데이터 없음
        assert "pos_in_20d_high" in feat_df.columns
        # 값이 1 이하여야 함 (현재가 <= 20일 최고가)
        valid = feat_df["pos_in_20d_high"].dropna()
        assert (valid <= 1.01).all(), "pos_in_20d_high가 1을 크게 초과합니다 (미래 데이터 의심)"
