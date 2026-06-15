"""피처 생성 모듈.

일봉 OHLCV 데이터 기반으로 머신러닝 학습용 피처를 생성합니다.
미래 데이터 누수(data leakage) 방지를 철저히 적용합니다.
최소 120거래일 이상 데이터 있는 종목만 사용합니다.

실행:
    python src/make_features.py
"""

import os
import sys
from typing import Optional

import numpy as np
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(__file__))
from utils import ensure_dir, load_config, save_csv, setup_logger

logger = setup_logger(__name__, "logs/make_features.log")
cfg = load_config("config.yaml")

FEATURE_COLUMNS = [
    "return_1d", "return_3d", "return_5d", "return_10d", "return_20d",
    "ma_5", "ma_20", "ma_60", "ma_120",
    "close_to_ma5", "close_to_ma20", "close_to_ma60", "close_to_ma120",
    "volume_ma5", "volume_ma20",
    "volume_ratio_5", "volume_ratio_20",
    "trading_value", "trading_value_ma20",
    "high_low_range", "high_low_range_pct",
    "open_close_return", "is_bullish_candle",
    "upper_shadow_ratio", "lower_shadow_ratio",
    "volatility_5", "volatility_20",
    "close_to_20d_high", "close_to_20d_low",
    "close_to_60d_high", "close_to_60d_low",
    "consecutive_up_days", "consecutive_down_days",
    "gap_rate",
    "price_position_20", "price_position_60",
    "turnover_proxy",
    "momentum_score", "volume_score", "volatility_score",
]

MIN_TRADING_DAYS = 120


def _consecutive_direction(series: pd.Series, direction: str, window: int = 5) -> pd.Series:
    """최근 window일 내 연속 상승/하락 일수 계산 (벡터화)."""
    if direction == "up":
        mask = (series > 0).astype(int)
    else:
        mask = (series < 0).astype(int)

    cumsum = mask.cumsum()
    last_zero_cumsum = cumsum.where(mask == 0).ffill().fillna(0).astype(int)
    result = (cumsum - last_zero_cumsum).clip(0, window)
    result.iloc[:window] = 0
    return result


def make_daily_features(df: pd.DataFrame) -> pd.DataFrame:
    """단일 종목 일봉 DataFrame에서 피처 생성.

    Args:
        df: date, open, high, low, close, volume, trading_value 컬럼 포함
            (시간순 정렬 필수)

    Returns:
        피처 컬럼이 추가된 DataFrame
    """
    df = df.copy().sort_values("date").reset_index(drop=True)
    close = df["close"].astype(float)
    open_ = df["open"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    tv = df["trading_value"].astype(float)

    eps = 1e-9

    # 수익률
    df["return_1d"] = close.pct_change(1)
    df["return_3d"] = close.pct_change(3)
    df["return_5d"] = close.pct_change(5)
    df["return_10d"] = close.pct_change(10)
    df["return_20d"] = close.pct_change(20)

    # 이동평균
    df["ma_5"] = close.rolling(5).mean()
    df["ma_20"] = close.rolling(20).mean()
    df["ma_60"] = close.rolling(60).mean()
    df["ma_120"] = close.rolling(120).mean()

    # 현재가 / 이동평균
    df["close_to_ma5"] = close / (df["ma_5"] + eps)
    df["close_to_ma20"] = close / (df["ma_20"] + eps)
    df["close_to_ma60"] = close / (df["ma_60"] + eps)
    df["close_to_ma120"] = close / (df["ma_120"] + eps)

    # 거래량
    df["volume_ma5"] = volume.rolling(5).mean()
    df["volume_ma20"] = volume.rolling(20).mean()
    df["volume_ratio_5"] = volume / (df["volume_ma5"] + eps)
    df["volume_ratio_20"] = volume / (df["volume_ma20"] + eps)

    # 거래대금
    df["trading_value"] = tv
    df["trading_value_ma20"] = tv.rolling(20).mean()

    # 변동폭
    df["high_low_range"] = high - low
    df["high_low_range_pct"] = (high - low) / (low + eps)

    # 수익률 및 캔들
    df["open_close_return"] = (close - open_) / (open_ + eps)
    df["is_bullish_candle"] = (close > open_).astype(int)

    candle_range = high - low + eps
    body_top = close.where(close > open_, open_)
    body_bot = open_.where(close > open_, close)
    df["upper_shadow_ratio"] = (high - body_top) / candle_range
    df["lower_shadow_ratio"] = (body_bot - low) / candle_range

    # 변동성
    daily_ret = close.pct_change()
    df["volatility_5"] = daily_ret.rolling(5).std()
    df["volatility_20"] = daily_ret.rolling(20).std()

    # 최고/최저가 대비 위치
    high_20 = high.rolling(20).max()
    low_20 = low.rolling(20).min()
    high_60 = high.rolling(60).max()
    low_60 = low.rolling(60).min()

    df["close_to_20d_high"] = close / (high_20 + eps)
    df["close_to_20d_low"] = close / (low_20 + eps)
    df["close_to_60d_high"] = close / (high_60 + eps)
    df["close_to_60d_low"] = close / (low_60 + eps)

    # 연속 상승/하락
    df["consecutive_up_days"] = _consecutive_direction(daily_ret, "up", 5)
    df["consecutive_down_days"] = _consecutive_direction(daily_ret, "down", 5)

    # 갭(전일 종가 대비 당일 시가)
    prev_close = close.shift(1)
    df["gap_rate"] = (open_ / (prev_close + eps)) - 1

    # 가격 위치 (N일 범위 내 상대 위치)
    df["price_position_20"] = (close - low_20) / (high_20 - low_20 + eps)
    df["price_position_60"] = (close - low_60) / (high_60 - low_60 + eps)

    # 거래대금 회전율 프록시
    df["turnover_proxy"] = tv / (tv.rolling(20).mean() + eps)

    # 복합 점수 피처
    ret5 = df["return_5d"].fillna(0).clip(-0.3, 0.3)
    ret20 = df["return_20d"].fillna(0).clip(-0.5, 0.5)
    pp60 = df["price_position_60"].fillna(0.5)
    df["momentum_score"] = ret5 * 0.3 + ret20 * 0.3 + (pp60 - 0.5) * 0.4

    vr5 = df["volume_ratio_5"].fillna(1.0).clip(0, 10)
    vr20 = df["volume_ratio_20"].fillna(1.0).clip(0, 10)
    df["volume_score"] = (vr5 / 10.0) * 0.5 + (vr20 / 10.0) * 0.5

    vol20 = df["volatility_20"].fillna(0.02).clip(1e-5, 0.5)
    df["volatility_score"] = 0.02 / vol20  # 낮은 변동성일수록 높은 점수

    return df


def make_all_features(
    daily_path: str,
    output_path: str,
) -> None:
    """전 종목 피처 생성 및 저장."""
    logger.info(f"피처 생성 시작: {daily_path}")

    if not os.path.exists(daily_path):
        logger.error(f"일봉 데이터 없음: {daily_path}")
        logger.error("먼저 실행: python src/collect_daily_data.py --years 3 --limit 100")
        sys.exit(1)

    # 증분 스킵: 출력 파일이 입력보다 최신이면 재계산 불필요
    if os.path.exists(output_path):
        if os.path.getmtime(output_path) > os.path.getmtime(daily_path):
            logger.info(f"피처 파일이 일봉 데이터보다 최신 — 재계산 생략: {output_path}")
            print("[SKIP] make_features: 출력이 입력보다 최신, 건너뜀", flush=True)
            return

    # stock_code 컬럼 지원 (신규) + ticker 컬럼 (구버전 호환)
    daily_df = pd.read_csv(daily_path, parse_dates=["date"])

    code_col = "stock_code" if "stock_code" in daily_df.columns else "ticker"
    name_col = "stock_name" if "stock_name" in daily_df.columns else "name"

    if code_col not in daily_df.columns:
        logger.error("종목코드 컬럼(stock_code 또는 ticker)이 없습니다.")
        sys.exit(1)

    # 6자리 문자열 보장
    daily_df[code_col] = daily_df[code_col].astype(str).str.zfill(6)

    tickers = daily_df[code_col].unique()
    logger.info(f"처리 종목 수: {len(tickers)}")

    all_features = []

    for code in tqdm(tickers, desc="피처 생성"):
        try:
            df = daily_df[daily_df[code_col] == code].copy()
            df = df.sort_values("date").reset_index(drop=True)

            if len(df) < MIN_TRADING_DAYS:
                continue

            feat_df = make_daily_features(df)

            # 120일 MA가 있는 행만 유지
            feat_df = feat_df.dropna(subset=["ma_120", "return_20d"])

            # stock_code, stock_name 통일
            feat_df["stock_code"] = code
            if name_col in df.columns:
                feat_df["stock_name"] = df[name_col].iloc[0]

            all_features.append(feat_df)

        except Exception as e:
            logger.warning(f"피처 생성 실패 [{code}]: {e}")

    if not all_features:
        logger.error("생성된 피처 없음")
        return

    result = pd.concat(all_features, ignore_index=True)
    result = result.sort_values(["stock_code", "date"]).reset_index(drop=True)
    result["stock_code"] = result["stock_code"].astype(str).str.zfill(6)

    ensure_dir(os.path.dirname(output_path))
    save_csv(result, output_path)

    feat_count = len([c for c in FEATURE_COLUMNS if c in result.columns])
    logger.info(
        f"피처 저장 완료: {output_path} "
        f"({len(result):,}행, {result['stock_code'].nunique()}개 종목, "
        f"{feat_count}/{len(FEATURE_COLUMNS)}개 피처)"
    )


def main() -> None:
    daily_path = cfg["data"]["raw_daily_path"]
    output_path = cfg["data"]["processed_features_path"]

    logger.info("=== 피처 생성 시작 ===")
    make_all_features(daily_path, output_path)
    logger.info("=== 피처 생성 완료 ===")


if __name__ == "__main__":
    main()
