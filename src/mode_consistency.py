"""모드 일관성 검증 모듈.

MOCK 주문 직전:
  - base_url이 openapivts인지
  - token_url이 openapivts인지
  - key_type_used가 MOCK_APP_KEY인지
  - header appkey가 KIS_MOCK_APP_KEY와 일치하는지

REAL 주문 직전:
  - base_url이 openapi:9443인지
  - key_type_used가 REAL_APP_KEY인지
  - header appkey가 KIS_REAL_APP_KEY와 일치하는지
"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

from trade_mode import fingerprint_key, is_url_valid_for_mode, is_key_valid_for_mode


def check_mode_consistency(
    mode: str,
    base_url: str = "",
    token_url: str = "",
    key_type_used: str = "",
    appkey: str = "",
) -> Dict:
    """모드-URL-키 일관성 종합 검증."""
    mode = (mode or "").strip().upper()
    errors: List[str] = []

    if mode in ("MOCK", "REAL"):
        if not is_url_valid_for_mode(mode, base_url):
            errors.append(f"base_url이 {mode} 모드에 맞지 않음: {base_url}")
        if not is_url_valid_for_mode(mode, token_url):
            errors.append(f"token_url이 {mode} 모드에 맞지 않음: {token_url}")

        expected_type = "MOCK_APP_KEY" if mode == "MOCK" else "REAL_APP_KEY"
        if key_type_used and key_type_used != expected_type and key_type_used != "GENERIC_APP_KEY_FALLBACK":
            errors.append(f"key_type_used={key_type_used} (기대값={expected_type})")

        key_valid = is_key_valid_for_mode(mode, appkey) if appkey else None
        if key_valid is False:
            env_key = "KIS_MOCK_APP_KEY" if mode == "MOCK" else "KIS_REAL_APP_KEY"
            expected_fp = fingerprint_key(os.getenv(env_key, ""))
            errors.append(
                f"appkey 불일치: actual={fingerprint_key(appkey)} expected={expected_fp}"
            )
    else:
        key_valid = None

    return {
        "mode_consistency_valid": len(errors) == 0,
        "mode_consistency_errors": errors,
        "appkey_fingerprint": fingerprint_key(appkey) if appkey else "MISSING",
        "expected_appkey_fingerprint": (
            fingerprint_key(os.getenv("KIS_MOCK_APP_KEY" if mode == "MOCK" else "KIS_REAL_APP_KEY", ""))
            if mode in ("MOCK", "REAL") else "N/A"
        ),
        "app_key_mode_valid": key_valid is True or (key_valid is None and len(errors) == 0),
        "mode_url_valid": len([e for e in errors if "url" in e.lower()]) == 0,
    }
