"""라벨 생성 모듈.

target_2pct_next_day: 다음 거래일 고가가 오늘 종가 대비 +2% 이상 도달하면 1.
미래 데이터 누수 방지를 위해 shift(-1) 처리를 엄격히 적용합니다.
예측용 최신 데이터(latest_features.csv)도 별도 저장합니다.

실행:
    python src/make_labels.py
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from utils import ensure_dir, load_config, save_csv, setup_logger

logger = setup_logger(__name__, "logs/make_labels.log")
cfg = load_config("config.yaml")


def make_labels_for_ticker(
    df: pd.DataFrame,
    target_profit_rate: float = 0.02,
) -> pd.DataFrame:
    """단일 종목에 대해 라벨 생성.

    Args:
        df: 시간순 정렬된 단일 종목 DataFrame (date, close, high, low, open 포함)
        target_profit_rate: 목표 수익률 (기본 0.02 = 2%)

    Returns:
        라벨 컬럼이 추가된 DataFrame
    """
    df = df.copy().sort_values("date").reset_index(drop=True)
    close = df["close"].astype(float)
    eps = 1e-9

    # 다음 거래일 데이터 (shift(-1) — 미래 누수 없음)
    next_high = df["high"].shift(-1).astype(float)
    next_low = df["low"].shift(-1).astype(float)
    next_open = df["open"].shift(-1).astype(float)
    next_close = df["close"].shift(-1).astype(float)

    # 주 타깃: 다음 거래일 고가 기준 +2% 도달 여부
    target_price = close * (1 + target_profit_rate)
    df["target_2pct_next_day"] = (next_high >= target_price).astype(float)
    df["target_2pct_next_morning"] = df["target_2pct_next_day"]  # 하위 호환

    # 보조 타깃: 다음 거래일 종가 기준 +2%
    df["target_2pct_next_close"] = (next_close >= target_price).astype(float)

    # 수익률
    df["next_day_return"] = (next_close - close) / (close + eps)
    df["next_day_high_return"] = (next_high - close) / (close + eps)
    df["next_day_low_return"] = (next_low - close) / (close + eps)

    # 최대 하락률
    df["target_max_drawdown"] = (next_low - close) / (close + eps)

    # 마지막 행은 다음날 데이터 없음 → NaN
    label_cols = [
        "target_2pct_next_day", "target_2pct_next_morning",
        "target_2pct_next_close", "next_day_return",
        "next_day_high_return", "next_day_low_return", "target_max_drawdown",
    ]
    if len(df) > 0:
        df.loc[df.index[-1], label_cols] = np.nan

    return df


def make_all_labels(
    features_path: str,
    output_path: str,
    latest_path: str,
    target_profit_rate: float = 0.02,
) -> None:
    """전 종목 라벨 생성 및 저장.

    Args:
        features_path: 피처 CSV 경로
        output_path: 라벨 포함 CSV 출력 경로
        latest_path: 예측용 최신 피처 CSV 출력 경로
        target_profit_rate: 목표 수익률
    """
    logger.info(f"라벨 생성 시작: {features_path}")

    if not os.path.exists(features_path):
        logger.error(f"피처 파일 없음: {features_path}")
        logger.error("먼저 실행: python src/make_features.py")
        sys.exit(1)

    # 증분 스킵: 출력 파일이 입력보다 최신이면 재계산 불필요
    if os.path.exists(output_path) and os.path.exists(latest_path):
        if os.path.getmtime(output_path) > os.path.getmtime(features_path):
            logger.info(f"라벨 파일이 피처 파일보다 최신 — 재계산 생략: {output_path}")
            print("[SKIP] make_labels: 출력이 입력보다 최신, 건너뜀", flush=True)
            return

    df = pd.read_csv(features_path, parse_dates=["date"])

    # 컬럼 감지 (stock_code 또는 ticker)
    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    df[code_col] = df[code_col].astype(str).str.zfill(6)

    tickers = df[code_col].unique()
    logger.info(f"처리 종목 수: {len(tickers)}")

    all_labeled = []
    latest_rows = []

    for code in tickers:
        try:
            df_t = df[df[code_col] == code].copy()
            df_t = df_t.sort_values("date").reset_index(drop=True)

            if len(df_t) < 5:
                continue

            df_labeled = make_labels_for_ticker(df_t, target_profit_rate)
            all_labeled.append(df_labeled)

            # 최신 행 (라벨 없어도 예측용으로 저장)
            latest_rows.append(df_t.iloc[[-1]].copy())

        except Exception as e:
            logger.warning(f"라벨 생성 실패 [{code}]: {e}")

    if not all_labeled:
        logger.error("생성된 라벨 없음")
        return

    # 학습용 데이터 (라벨 있는 행만)
    result = pd.concat(all_labeled, ignore_index=True)
    result = result.dropna(subset=["target_2pct_next_day"])
    result = result.sort_values([code_col, "date"]).reset_index(drop=True)

    label_dist = result["target_2pct_next_day"].value_counts()
    total = len(result)
    pos_rate = label_dist.get(1, 0) / total * 100 if total > 0 else 0
    logger.info(
        f"라벨 분포: 양성(1)={label_dist.get(1,0):,}건 ({pos_rate:.1f}%), "
        f"음성(0)={label_dist.get(0,0):,}건 ({100-pos_rate:.1f}%)"
    )

    ensure_dir(os.path.dirname(output_path))
    save_csv(result, output_path)
    logger.info(
        f"라벨 저장: {output_path} "
        f"({len(result):,}행, {result[code_col].nunique()}개 종목)"
    )

    # 예측용 최신 피처 저장 (라벨 없어도 가능)
    if latest_rows:
        latest_df = pd.concat(latest_rows, ignore_index=True)
        latest_df[code_col] = latest_df[code_col].astype(str).str.zfill(6)
        ensure_dir(os.path.dirname(latest_path))
        save_csv(latest_df, latest_path)
        logger.info(f"최신 피처 저장: {latest_path} ({len(latest_df)}개 종목)")

    _verify_no_leakage(result)


def _verify_no_leakage(df: pd.DataFrame) -> None:
    """데이터 누수 간단 검증."""
    target_cols = [
        "target_2pct_next_day", "target_2pct_next_morning",
        "target_2pct_next_close", "next_day_return",
        "next_day_high_return", "next_day_low_return", "target_max_drawdown",
    ]
    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    skip_cols = target_cols + [code_col, "stock_name", "name", "market", "date"]
    feature_cols = [c for c in df.columns if c not in skip_cols]

    high_corr_found = False
    for tcol in ["target_2pct_next_day"]:
        if tcol not in df.columns:
            continue
        for fcol in feature_cols[:10]:  # 처음 10개만 빠르게 검증
            try:
                corr = df[[fcol, tcol]].dropna().corr().iloc[0, 1]
                if abs(corr) > 0.95:
                    logger.warning(f"데이터 누수 의심! {fcol} ↔ {tcol}: {corr:.3f}")
                    high_corr_found = True
            except Exception:
                pass

    if not high_corr_found:
        logger.info("데이터 누수 검증 완료 (이상 없음)")


def main() -> None:
    features_path = cfg["data"]["processed_features_path"]
    output_path = cfg["data"]["processed_labels_path"]
    latest_path = "data/processed/latest_features.csv"
    target_rate = cfg["strategy"].get("target_profit_rate", 0.02)

    logger.info("=== 라벨 생성 시작 ===")
    make_all_labels(features_path, output_path, latest_path, target_rate)
    logger.info("=== 라벨 생성 완료 ===")


if __name__ == "__main__":
    main()
