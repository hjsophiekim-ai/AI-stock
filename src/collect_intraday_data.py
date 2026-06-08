"""당일 분봉 데이터 수집.

한국투자증권 API 또는 pykrx를 사용해 당일 분봉 데이터를 수집합니다.
오후 2시 40분 기준 장중 흐름 분석에 활용됩니다.

실행:
    python src/collect_intraday_data.py
"""

import os
import sys
from datetime import datetime
from typing import Optional

import pandas as pd
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(__file__))
from utils import (
    ensure_dir,
    get_today_str,
    load_config,
    save_csv,
    setup_logger,
)

logger = setup_logger(__name__, "logs/collect_intraday_data.log")
cfg = load_config("config.yaml")


def get_ticker_list_from_daily() -> list:
    """수집된 일봉 데이터에서 종목 리스트를 가져옵니다."""
    path = cfg["data"]["raw_daily_path"]
    if not os.path.exists(path):
        logger.warning(f"일봉 데이터 없음: {path}. collect_daily_data.py를 먼저 실행하세요.")
        return []
    df = pd.read_csv(path, usecols=["ticker", "name"], nrows=100000)
    tickers = df.drop_duplicates("ticker")[["ticker", "name"]].values.tolist()
    return tickers


def fetch_intraday_pykrx(
    ticker: str,
    date_str: str,
    period_min: int = 5,
) -> Optional[pd.DataFrame]:
    """pykrx로 특정일 분봉 데이터 조회.

    Args:
        ticker: 종목코드
        date_str: 'YYYYMMDD'
        period_min: 분봉 단위

    Returns:
        datetime, open, high, low, close, volume 컬럼 DataFrame
    """
    try:
        from pykrx import stock
        df = stock.get_market_ohlcv_by_ticker(date_str, market="ALL")
        # pykrx 분봉 API: get_market_ohlcv (분봉은 별도 API 필요)
        # 실제 분봉은 KIS API 사용 권장
        # 여기서는 pykrx의 일봉만 활용하고 분봉은 KIS API로 수집
        return None
    except Exception as e:
        logger.debug(f"pykrx 분봉 조회 실패 [{ticker}]: {e}")
        return None


def fetch_intraday_kis(
    ticker: str,
    period_min: int = 5,
) -> Optional[pd.DataFrame]:
    """KIS API로 당일 분봉 데이터 조회.

    Args:
        ticker: 종목코드
        period_min: 분봉 단위 (1, 5, 10, 15, 30, 60)

    Returns:
        datetime, open, high, low, close, volume 컬럼 DataFrame
    """
    try:
        from kis_api import KISAPI
        api = KISAPI()
        df = api.get_intraday_ohlcv(ticker, period_min)
        return df
    except Exception as e:
        logger.debug(f"KIS 분봉 조회 실패 [{ticker}]: {e}")
        return None


def calc_intraday_features(
    df: pd.DataFrame,
    cutoff_time: str = "14:40",
) -> dict:
    """분봉 데이터에서 14:40 기준 피처 계산.

    Args:
        df: 분봉 DataFrame (datetime, open, high, low, close, volume)
        cutoff_time: 기준 시간 'HH:MM'

    Returns:
        피처 딕셔너리
    """
    if df is None or df.empty:
        return {}

    cutoff_hour, cutoff_min = map(int, cutoff_time.split(":"))

    # 14:40 이전 데이터만 사용
    mask = (df["datetime"].dt.hour < cutoff_hour) | (
        (df["datetime"].dt.hour == cutoff_hour)
        & (df["datetime"].dt.minute <= cutoff_min)
    )
    df_cut = df[mask].copy()

    if df_cut.empty:
        return {}

    open_price = df_cut.iloc[0]["open"]
    close_1440 = df_cut.iloc[-1]["close"]
    high_1440 = df_cut["high"].max()
    low_1440 = df_cut["low"].min()
    total_vol = df_cut["volume"].sum()

    # VWAP 계산
    typical_price = (df_cut["high"] + df_cut["low"] + df_cut["close"]) / 3
    vwap = (typical_price * df_cut["volume"]).sum() / (total_vol + 1e-9)

    features = {
        "intraday_ret_1440": (close_1440 - open_price) / (open_price + 1e-9),
        "pos_vs_intraday_high": close_1440 / (high_1440 + 1e-9),
        "intraday_vol_total": total_vol,
        "vwap_position": close_1440 / (vwap + 1e-9),
        "close_1440": close_1440,
        "high_1440": high_1440,
        "low_1440": low_1440,
    }
    return features


def collect_intraday_data(
    tickers: list,
    date_str: str,
    output_dir: str,
    period_min: int = 5,
) -> None:
    """전 종목 분봉 데이터 수집 및 저장.

    Args:
        tickers: [(ticker, name), ...] 리스트
        date_str: 수집 날짜 'YYYYMMDD'
        output_dir: 저장 디렉토리
        period_min: 분봉 단위
    """
    day_dir = os.path.join(output_dir, date_str)
    ensure_dir(day_dir)

    success_count = 0
    error_tickers = []

    # KIS API가 설정되어 있지 않으면 경고
    app_key = os.environ.get("KIS_APP_KEY", "")
    if not app_key:
        logger.warning(
            "KIS_APP_KEY 미설정. 분봉 데이터 수집을 위해 .env 파일에 API 키를 설정하세요. "
            "분봉 피처 없이도 일봉 기반으로 진행 가능합니다."
        )

    for ticker, name in tqdm(tickers[:100], desc="분봉 수집"):  # 테스트용 100개 제한
        out_path = os.path.join(day_dir, f"{ticker}.csv")
        if os.path.exists(out_path):
            continue

        try:
            df = fetch_intraday_kis(ticker, period_min)
            if df is not None and not df.empty:
                save_csv(df, out_path)
                success_count += 1
            else:
                logger.debug(f"분봉 데이터 없음: {ticker} ({name})")
        except Exception as e:
            error_tickers.append(f"{ticker}|{name}|{e}")

        import time
        time.sleep(cfg["kis"].get("request_delay", 0.1))

    if error_tickers:
        error_log = cfg["data"]["error_log_path"]
        ensure_dir(os.path.dirname(error_log))
        with open(error_log, "a", encoding="utf-8") as f:
            for msg in error_tickers:
                f.write(f"{datetime.now().isoformat()} | intraday | {msg}\n")

    logger.info(
        f"분봉 수집 완료: {success_count}개 성공, {len(error_tickers)}개 실패 → {day_dir}"
    )


def main() -> None:
    """메인 실행 함수."""
    today = get_today_str("%Y%m%d")
    output_dir = cfg["data"]["raw_intraday_dir"]
    period_min = 5  # 5분봉 기본

    logger.info(f"=== 분봉 데이터 수집 시작 ({today}) ===")

    tickers = get_ticker_list_from_daily()
    if not tickers:
        logger.error("종목 리스트 없음. collect_daily_data.py를 먼저 실행하세요.")
        sys.exit(1)

    collect_intraday_data(tickers, today, output_dir, period_min)
    logger.info("=== 분봉 데이터 수집 완료 ===")


if __name__ == "__main__":
    main()
