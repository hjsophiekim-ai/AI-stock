"""장중 매매용 피처 생성 모듈.

오전 10:30~11:30 매수 결정에 사용할 수 있는 피처만 생성한다.
분봉 데이터가 없으므로 일봉 데이터를 사용하되, 전일까지 데이터만 feature로 사용.

중요 원칙:
  - 당일 open/high/low/close는 label 계산에만 사용, feature에서는 제외
  - 전일 데이터(shift(1) = 어제) 기반 피처 사용
  - 당일 gap_rate(시가 기준) 는 포함 (매수 전에 알 수 있음)

실행:
    python src/make_intraday_features.py
    python src/make_intraday_features.py --date 20260615
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(__file__))
from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/make_intraday_features.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 장중 예측 피처 컬럼 정의
INTRADAY_FEATURE_COLUMNS = [
    # 전일 수익률
    "prev_return_1d", "prev_return_3d", "prev_return_5d", "prev_return_20d",
    # 전일 거래량/거래대금
    "prev_volume_ratio_5", "prev_volume_ratio_20",
    "prev_trading_value", "prev_trading_value_ratio_20",
    # 전일 기술적 지표
    "prev_close_to_ma5", "prev_close_to_ma20",
    "prev_volatility_20",
    "prev_price_position_20", "prev_price_position_60",
    "prev_momentum_score",
    # 당일 시가 기반 (매수 전에 알 수 있음)
    "gap_rate",                     # 당일 시가 / 전일 종가 - 1
    "prev_close_to_20d_high",       # 전일 종가 vs 20일 고점
    "prev_close_to_20d_low",
    # 이동평균 위치
    "prev_ma5_gt_ma20",             # 5MA > 20MA
    "prev_ma20_gt_ma60",            # 20MA > 60MA
    # 연속 상승/하락
    "prev_consecutive_up_days",
    "prev_consecutive_down_days",
    # 전일 캔들
    "prev_is_bullish_candle",
    "prev_upper_shadow_ratio",
    "prev_lower_shadow_ratio",
    "prev_high_low_range_pct",
    # 거래대금 가속도
    "trading_value_acceleration",   # 당일 vs 20일 평균
]


def make_intraday_features_for_ticker(df: pd.DataFrame) -> pd.DataFrame:
    """단일 종목 일봉 데이터에서 장중 피처 생성.

    Args:
        df: date, open, high, low, close, volume, trading_value 포함, 시간순 정렬

    Returns:
        장중 피처 컬럼 추가된 DataFrame
    """
    df = df.copy().sort_values("date").reset_index(drop=True)
    eps = 1e-9

    close = df["close"].astype(float)
    open_ = df["open"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    tv = df["trading_value"].astype(float)

    # ── 이동평균 (종목별 계산) ────────────────────────────────────────────────
    ma5 = close.rolling(5, min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=1).mean()
    ma60 = close.rolling(60, min_periods=1).mean()
    vol_ma5 = volume.rolling(5, min_periods=1).mean()
    vol_ma20 = volume.rolling(20, min_periods=1).mean()
    tv_ma20 = tv.rolling(20, min_periods=1).mean()
    vol20 = close.pct_change().rolling(20, min_periods=5).std()

    # ── 전일 종가 대비 수익률 (shift(1) = 어제 종가) ─────────────────────────
    prev_close = close.shift(1)
    df["prev_return_1d"] = (close / (prev_close + eps) - 1).shift(1)
    df["prev_return_3d"] = (close / close.shift(3).clip(lower=eps) - 1).shift(1)
    df["prev_return_5d"] = (close / close.shift(5).clip(lower=eps) - 1).shift(1)
    df["prev_return_20d"] = (close / close.shift(20).clip(lower=eps) - 1).shift(1)

    # ── 전일 거래량/거래대금 ─────────────────────────────────────────────────
    df["prev_volume_ratio_5"] = (volume / (vol_ma5 + eps)).shift(1)
    df["prev_volume_ratio_20"] = (volume / (vol_ma20 + eps)).shift(1)
    df["prev_trading_value"] = tv.shift(1)
    df["prev_trading_value_ratio_20"] = (tv / (tv_ma20 + eps)).shift(1)

    # ── 전일 기술적 지표 ─────────────────────────────────────────────────────
    df["prev_close_to_ma5"] = (close / (ma5 + eps) - 1).shift(1)
    df["prev_close_to_ma20"] = (close / (ma20 + eps) - 1).shift(1)
    df["prev_volatility_20"] = vol20.shift(1)

    high_20 = high.rolling(20, min_periods=5).max()
    low_20 = low.rolling(20, min_periods=5).min()
    high_60 = high.rolling(60, min_periods=10).max()
    low_60 = low.rolling(60, min_periods=10).min()
    rng_20 = (high_20 - low_20).clip(lower=eps)
    rng_60 = (high_60 - low_60).clip(lower=eps)

    df["prev_price_position_20"] = ((close - low_20) / rng_20).shift(1)
    df["prev_price_position_60"] = ((close - low_60) / rng_60).shift(1)
    df["prev_close_to_20d_high"] = (close / (high_20 + eps) - 1).shift(1)
    df["prev_close_to_20d_low"] = (close / (low_20 + eps) - 1).shift(1)

    df["prev_ma5_gt_ma20"] = (ma5 > ma20).astype(float).shift(1)
    df["prev_ma20_gt_ma60"] = (ma20 > ma60).astype(float).shift(1)

    # ── 전일 연속 방향 ────────────────────────────────────────────────────────
    ret = close.pct_change()
    up_mask = (ret > 0).astype(int)
    down_mask = (ret < 0).astype(int)
    cum_up = up_mask.cumsum()
    cum_down = down_mask.cumsum()
    last_zero_up = cum_up.where(up_mask == 0).ffill().fillna(0).astype(int)
    last_zero_down = cum_down.where(down_mask == 0).ffill().fillna(0).astype(int)
    df["prev_consecutive_up_days"] = (cum_up - last_zero_up).clip(0, 10).shift(1)
    df["prev_consecutive_down_days"] = (cum_down - last_zero_down).clip(0, 10).shift(1)

    # ── 전일 캔들 형태 ────────────────────────────────────────────────────────
    body = (close - open_).abs()
    candle_range = (high - low).clip(lower=eps)
    df["prev_is_bullish_candle"] = (close > open_).astype(float).shift(1)
    df["prev_upper_shadow_ratio"] = ((high - close.clip(lower=open_)) / candle_range).shift(1)
    df["prev_lower_shadow_ratio"] = ((open_.clip(upper=close) - low) / candle_range).shift(1)
    df["prev_high_low_range_pct"] = ((high - low) / (prev_close + eps)).shift(1)

    # ── 모멘텀 종합 스코어 (전일) ─────────────────────────────────────────────
    prev_pos20 = df["prev_price_position_20"]
    prev_vol_ratio = df["prev_volume_ratio_20"]
    prev_ret5 = df["prev_return_5d"]
    df["prev_momentum_score"] = (
        prev_pos20.clip(0, 1) * 0.4 +
        prev_vol_ratio.clip(0, 3) / 3 * 0.3 +
        (prev_ret5 + 0.1).clip(0, 0.2) / 0.2 * 0.3
    )

    # ── 당일 갭 (매수 전에 알 수 있음) ──────────────────────────────────────
    df["gap_rate"] = open_ / (prev_close + eps) - 1

    # ── 거래대금 가속도 (전일 기준 — 당일 데이터 누수 방지) ──────────────────
    df["trading_value_acceleration"] = (tv / (tv_ma20 + eps)).shift(1)

    return df


def build_intraday_features(
    daily_path: str,
    output_path: str,
    latest_output_path: str = None,
    date_str: str = None,
) -> pd.DataFrame:
    """daily_prices.csv에서 장중 피처 데이터셋 생성."""
    logger.info(f"일봉 데이터 로드: {daily_path}")
    df = pd.read_csv(daily_path, parse_dates=["date"])

    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    df[code_col] = df[code_col].astype(str).str.zfill(6)
    df = df.sort_values(["stock_code", "date"]).reset_index(drop=True)

    results = []
    for code, grp in tqdm(df.groupby(code_col), desc="피처 생성", disable=False):
        grp = grp.sort_values("date").reset_index(drop=True)
        if len(grp) < 25:
            continue
        feat_grp = make_intraday_features_for_ticker(grp)
        results.append(feat_grp)

    result = pd.concat(results, ignore_index=True)
    result = result.sort_values(["date", code_col]).reset_index(drop=True)

    ensure_dir(str(Path(output_path).parent))
    result.to_csv(output_path, index=False)
    logger.info(f"장중 피처 저장: {output_path} | {len(result):,}행")

    # 최신 날짜 데이터만 별도 저장 (당일 예측용)
    if latest_output_path:
        target_date = date_str or result["date"].max()
        if isinstance(target_date, str):
            import datetime
            try:
                target_date = pd.to_datetime(target_date)
            except Exception:
                target_date = result["date"].max()
        latest = result[result["date"] == target_date]
        if latest.empty:
            latest = result[result["date"] == result["date"].max()]
        ensure_dir(str(Path(latest_output_path).parent))
        latest.to_csv(latest_output_path, index=False)
        logger.info(f"최신 장중 피처 저장: {latest_output_path} | {len(latest):,}행")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="장중 매매용 피처 생성")
    parser.add_argument(
        "--input",
        default=str(PROJECT_ROOT / "data" / "raw" / "daily_prices.csv"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "data" / "processed" / "intraday_features.csv"),
    )
    parser.add_argument(
        "--latest-output",
        default=str(PROJECT_ROOT / "data" / "processed" / "intraday_latest_features.csv"),
    )
    parser.add_argument("--date", default=None, help="최신 피처 날짜 (기본: 가장 최근)")
    args = parser.parse_args()

    df = build_intraday_features(
        args.input,
        args.output,
        latest_output_path=args.latest_output,
        date_str=args.date,
    )
    print(f"장중 피처 생성 완료: {len(df):,}행")
    print(f"피처 컬럼 수: {len([c for c in INTRADAY_FEATURE_COLUMNS if c in df.columns])}/{len(INTRADAY_FEATURE_COLUMNS)}")
    print(f"저장: {args.output}")


if __name__ == "__main__":
    main()
