"""유틸리티 함수 모음: 로깅, 설정 로딩, 날짜 처리, 파일 저장."""

import logging
import os
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml


def load_config(config_path: str = "config.yaml") -> dict:
    """config.yaml을 로드하여 딕셔너리로 반환."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logger(
    name: str,
    log_file: Optional[str] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """로거를 설정하고 반환.

    Args:
        name: 로거 이름
        log_file: 로그 파일 경로 (None이면 콘솔만)
        level: 로그 레벨
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Windows CP949 환경에서 이모지/한글 인코딩 오류 방지
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    try:
        ch.stream = open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
    except Exception:
        pass
    logger.addHandler(ch)

    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


def get_today_str(fmt: str = "%Y%m%d") -> str:
    """오늘 날짜를 지정 포맷의 문자열로 반환."""
    return datetime.now().strftime(fmt)


def to_date_str(dt: Any, fmt: str = "%Y-%m-%d") -> str:
    """날짜 객체를 문자열로 변환."""
    if isinstance(dt, str):
        return dt
    if isinstance(dt, (datetime, date)):
        return dt.strftime(fmt)
    return str(dt)


def ensure_dir(path: str) -> None:
    """디렉토리가 없으면 생성."""
    os.makedirs(path, exist_ok=True)


def save_csv(df: pd.DataFrame, path: str, index: bool = False) -> None:
    """DataFrame을 CSV로 저장. 디렉토리 자동 생성."""
    ensure_dir(os.path.dirname(path))
    df.to_csv(path, index=index, encoding="utf-8-sig")


def load_csv(path: str, **kwargs) -> Optional[pd.DataFrame]:
    """CSV를 DataFrame으로 로드. 파일 없으면 None 반환."""
    if not os.path.exists(path):
        return None
    return pd.read_csv(path, **kwargs)


def is_preferred_stock(ticker: str) -> bool:
    """우선주 여부 판별 (종목코드 끝자리 기준).

    코스피 우선주는 마지막 자리가 5~9인 경우가 많음.
    완벽하지 않으므로 종목명 기반 필터와 병행 권장.
    """
    if len(ticker) >= 6:
        suffix = ticker[-1]
        return suffix in ("5", "6", "7", "8", "9")
    return False


def is_spac(name: str) -> bool:
    """스팩(SPAC) 여부 판별."""
    keywords = ("스팩", "SPAC", "기업인수목적")
    return any(kw in name.upper() for kw in keywords)


def is_etf_etn(name: str) -> bool:
    """ETF/ETN 여부 판별."""
    keywords = ("ETF", "ETN", "KODEX", "TIGER", "KINDEX", "KOSEF", "ARIRANG")
    return any(kw in name.upper() for kw in keywords)


def calc_trading_days_ago(n_years: int = 2) -> str:
    """n년 전 날짜를 'YYYYMMDD' 형식으로 반환."""
    from datetime import timedelta
    start = datetime.now() - timedelta(days=n_years * 365)
    return start.strftime("%Y%m%d")
