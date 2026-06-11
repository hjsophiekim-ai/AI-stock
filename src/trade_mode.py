"""거래 모드별 키 관리 유틸리티.

MOCK 모드 → KIS_MOCK_APP_KEY 전용
REAL 모드 → KIS_REAL_APP_KEY 전용
교차 사용 절대 금지.
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

MOCK_BASE_URL = "https://openapivts.koreainvestment.com:29443"
REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"

_MODE_ENV_MAP = {
    "MOCK": {
        "key_env": "KIS_MOCK_APP_KEY",
        "secret_env": "KIS_MOCK_APP_SECRET",
        "account_env": "KIS_MOCK_ACCOUNT_NO",
        "product_env": "KIS_MOCK_ACCOUNT_PRODUCT_CODE",
        "base_url": MOCK_BASE_URL,
        "token_cache": "data/mock_token_cache.json",
        "key_type": "MOCK_APP_KEY",
    },
    "REAL": {
        "key_env": "KIS_REAL_APP_KEY",
        "secret_env": "KIS_REAL_APP_SECRET",
        "account_env": "KIS_ACCOUNT_NO",
        "product_env": "KIS_ACCOUNT_PRODUCT_CODE",
        "base_url": REAL_BASE_URL,
        "token_cache": "data/real_token_cache.json",
        "key_type": "REAL_APP_KEY",
    },
}


def fingerprint_key(value: str) -> str:
    """키 앞 6자리 + **** + 뒤 4자리 (로그/CSV 전용)."""
    if not value:
        return "MISSING"
    if len(value) <= 10:
        return value[:2] + "****"
    return value[:6] + "****" + value[-4:]


def get_expected_key_fingerprint_for_mode(mode: str) -> str:
    """mode에 해당하는 환경변수 키의 fingerprint를 반환."""
    mode = (mode or "").strip().upper()
    mapping = _MODE_ENV_MAP.get(mode)
    if not mapping:
        return "N/A"
    return fingerprint_key(os.getenv(mapping["key_env"], ""))


def get_base_url_for_mode(mode: str) -> str:
    mode = (mode or "").strip().upper()
    return _MODE_ENV_MAP.get(mode, {}).get("base_url", MOCK_BASE_URL)


def get_token_cache_for_mode(mode: str) -> str:
    mode = (mode or "").strip().upper()
    return _MODE_ENV_MAP.get(mode, {}).get("token_cache", "data/mock_token_cache.json")


def is_url_valid_for_mode(mode: str, url: str) -> bool:
    mode = (mode or "").strip().upper()
    if mode == "MOCK":
        return "openapivts.koreainvestment.com:29443" in (url or "")
    if mode == "REAL":
        return "openapi.koreainvestment.com:9443" in (url or "")
    return True


def is_key_valid_for_mode(mode: str, appkey: str) -> Optional[bool]:
    """appkey가 mode에 맞는 환경변수 값과 일치하는지 확인.

    Returns:
        True  — 일치
        False — 불일치
        None  — 환경변수 미설정으로 확인 불가
    """
    mode = (mode or "").strip().upper()
    mapping = _MODE_ENV_MAP.get(mode)
    if not mapping:
        return None
    expected = os.getenv(mapping["key_env"], "")
    if not expected:
        return None
    return appkey == expected
