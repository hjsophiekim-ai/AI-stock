"""장중 매매 라벨 생성 모듈.

오전 10:30~11:30 매수 후 당일 +2%/+3%/+5% 도달 여부를 라벨링한다.
분봉 데이터가 없으므로 일봉 데이터에서 open을 entry_price로 근사:
  - entry_price ≈ open  (당일 시가, 10:30 매수 근사)
  - future_high = 당일 high
  - target_intraday_2pct = (high / open - 1) >= 0.02

실행:
    python src/make_intraday_labels.py
    python src/make_intraday_labels.py --input data/raw/daily_prices.csv
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/make_intraday_labels.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def make_intraday_labels(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 데이터로 장중 매매 라벨 생성.

    entry_price = open (당일 시가를 10:30 매수가로 근사)
    future_high = high (당일 고가)
    future_low = low (당일 저가)

    Args:
        df: date, stock_code, open, high, low, close, volume, trading_value 포함 DataFrame

    Returns:
        라벨 컬럼 추가된 DataFrame
    """
    df = df.copy()
    eps = 1e-9

    open_ = df["open"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    # entry_price: open을 10:30 매수가로 근사
    df["entry_price"] = open_

    # future_high / future_low: 당일 고가/저가 (entry 이후 실현 가능한 최대/최소)
    df["future_high_after_entry"] = high
    df["future_low_after_entry"] = low

    # 수익률
    df["max_future_return_pct"] = (high / (open_ + eps) - 1) * 100
    df["max_adverse_return_pct"] = (low / (open_ + eps) - 1) * 100

    # 메인 라벨
    df["target_intraday_2pct"] = (df["max_future_return_pct"] >= 2.0).astype(int)
    df["target_intraday_3pct"] = (df["max_future_return_pct"] >= 3.0).astype(int)
    df["target_intraday_5pct"] = (df["max_future_return_pct"] >= 5.0).astype(int)

    # 손절 전에 익절 여부 (고가 전에 저가 -2% 도달 시 손절로 간주)
    # 일봉으로는 순서를 알 수 없으므로 보수적 근사: 고가가 +2% AND 저가 > -2%
    df["good_trade_label"] = (
        (df["max_future_return_pct"] >= 2.0) &
        (df["max_adverse_return_pct"] > -2.0)
    ).astype(int)

    # intraday_stop_hit: 당일 저가가 entry -2% 이하 도달
    df["intraday_stop_hit_2pct"] = (df["max_adverse_return_pct"] <= -2.0).astype(int)

    # 당일 시가 대비 변동률 (레이블 참고용)
    df["open_to_close_return_pct"] = (close / (open_ + eps) - 1) * 100
    df["intraday_range_pct"] = (high / (low + eps) - 1) * 100

    return df


def build_intraday_dataset(
    daily_path: str,
    output_path: str,
) -> pd.DataFrame:
    """daily_prices.csv에서 장중 라벨 데이터셋 생성."""
    logger.info(f"일봉 데이터 로드: {daily_path}")
    df = pd.read_csv(daily_path, parse_dates=["date"])

    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    df[code_col] = df[code_col].astype(str).str.zfill(6)
    df = df.sort_values(["stock_code", "date"]).reset_index(drop=True)

    # 종목별 라벨 생성
    labeled_parts = []
    for code, grp in df.groupby(code_col):
        grp = grp.sort_values("date").reset_index(drop=True)
        grp_labeled = make_intraday_labels(grp)
        labeled_parts.append(grp_labeled)

    result = pd.concat(labeled_parts, ignore_index=True)
    result = result.sort_values(["date", code_col]).reset_index(drop=True)

    ensure_dir(str(Path(output_path).parent))
    result.to_csv(output_path, index=False)
    logger.info(
        f"장중 라벨 저장: {output_path} | "
        f"{len(result):,}행 | "
        f"2pct hit={result['target_intraday_2pct'].mean():.3f} | "
        f"3pct hit={result['target_intraday_3pct'].mean():.3f} | "
        f"5pct hit={result['target_intraday_5pct'].mean():.3f}"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="장중 매매 라벨 생성")
    parser.add_argument(
        "--input",
        default=str(PROJECT_ROOT / "data" / "raw" / "daily_prices.csv"),
        help="일봉 데이터 경로",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "data" / "processed" / "intraday_labeled_dataset.csv"),
        help="출력 경로",
    )
    args = parser.parse_args()

    df = build_intraday_dataset(args.input, args.output)
    total = len(df)
    hit2 = df["target_intraday_2pct"].mean()
    hit3 = df["target_intraday_3pct"].mean()
    hit5 = df["target_intraday_5pct"].mean()
    good = df["good_trade_label"].mean()

    print(f"장중 라벨 생성 완료: {total:,}행")
    print(f"  target_intraday_2pct 비율: {hit2:.3f} ({hit2*100:.1f}%)")
    print(f"  target_intraday_3pct 비율: {hit3:.3f} ({hit3*100:.1f}%)")
    print(f"  target_intraday_5pct 비율: {hit5:.3f} ({hit5*100:.1f}%)")
    print(f"  good_trade_label 비율:     {good:.3f} ({good*100:.1f}%)")
    print(f"저장: {args.output}")


if __name__ == "__main__":
    main()
