"""모드별 키/URL/토큰 진단 스크립트.

사용법:
    python src\\mode_diagnosis.py --mode mock
    python src\\mode_diagnosis.py --mode real
    python src\\mode_diagnosis.py --mode paper
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── 경로 설정 ──────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent

if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

os.chdir(str(PROJECT_ROOT))

# ──────────────────────────────────────────────────────────────────────────

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from kis_auth import fingerprint_key, get_kis_credentials
from trade_mode import (
    get_expected_key_fingerprint_for_mode,
    get_base_url_for_mode,
    get_token_cache_for_mode,
    is_url_valid_for_mode,
    is_key_valid_for_mode,
    MOCK_BASE_URL,
    REAL_BASE_URL,
)


def _sep(char: str = "-", width: int = 60) -> str:
    return char * width


def diagnose_mode(mode: str) -> dict:
    mode_upper = (mode or "mock").strip().upper()
    creds = get_kis_credentials(mode_upper)
    result = {
        "run_at": datetime.now().isoformat(),
        "requested_mode": mode_upper,
        "resolved_mode": mode_upper,
        "base_url": creds.get("base_url", ""),
        "token_url": creds.get("token_url", ""),
        "key_type_used": creds.get("key_type_used", ""),
        "token_cache_file": creds.get("token_cache_file", ""),
        "expected_appkey_fingerprint": get_expected_key_fingerprint_for_mode(mode_upper),
        "actual_appkey_fingerprint": creds.get("appkey_fingerprint", "MISSING"),
        "mode_consistency_valid": False,
        "mode_url_valid": False,
        "app_key_mode_valid": False,
        "errors": [],
    }

    errors = []

    if mode_upper == "PAPER":
        result["mode_consistency_valid"] = True
        result["mode_url_valid"] = True
        result["app_key_mode_valid"] = True
        return result

    # URL 검증
    base_url = creds.get("base_url", "")
    token_url = creds.get("token_url", "")
    if not is_url_valid_for_mode(mode_upper, base_url):
        errors.append(f"base_url이 {mode_upper} 모드에 맞지 않음: {base_url}")
    if not is_url_valid_for_mode(mode_upper, token_url):
        errors.append(f"token_url이 {mode_upper} 모드에 맞지 않음: {token_url}")
    result["mode_url_valid"] = len([e for e in errors if "url" in e.lower()]) == 0

    # 키 검증
    appkey = creds.get("appkey", "")
    key_valid = is_key_valid_for_mode(mode_upper, appkey)
    expected_fp = get_expected_key_fingerprint_for_mode(mode_upper)
    actual_fp = fingerprint_key(appkey)

    if key_valid is False:
        errors.append(
            f"appkey 불일치: actual={actual_fp} expected={expected_fp}"
        )
    if not appkey:
        errors.append(f"{mode_upper} appkey가 비어있음 (환경변수 미설정?)")

    result["app_key_mode_valid"] = key_valid is True
    result["actual_appkey_fingerprint"] = actual_fp
    result["expected_appkey_fingerprint"] = expected_fp

    # key_type_used 검증
    expected_type = "MOCK_APP_KEY" if mode_upper == "MOCK" else "REAL_APP_KEY"
    kt = creds.get("key_type_used", "")
    if kt and kt not in (expected_type, "GENERIC_APP_KEY_FALLBACK"):
        errors.append(f"key_type_used={kt} (기대값={expected_type})")
    if kt == "GENERIC_APP_KEY_FALLBACK":
        errors.append(f"WARNING: {mode_upper} 모드가 GENERIC_APP_KEY_FALLBACK 사용 중 (전용 키 미설정)")
    if kt == "MISSING":
        errors.append(f"ERROR: {mode_upper} appkey/secret 완전 누락")

    result["errors"] = errors
    result["mode_consistency_valid"] = len(errors) == 0

    return result


def print_diagnosis(result: dict) -> None:
    mode = result.get("requested_mode", "")
    print(_sep("="))
    print(f"MODE DIAGNOSIS: {mode}")
    print(_sep("="))
    fields = [
        ("requested_mode", "requested_mode"),
        ("resolved_mode", "resolved_mode"),
        ("base_url", "base_url"),
        ("token_url", "token_url"),
        ("key_type_used", "key_type_used"),
        ("token_cache_file", "token_cache_file"),
        ("expected_appkey_fingerprint", "expected_appkey_fingerprint"),
        ("actual_appkey_fingerprint", "actual_appkey_fingerprint"),
        ("app_key_mode_valid", "app_key_mode_valid"),
        ("mode_url_valid", "mode_url_valid"),
        ("mode_consistency_valid", "mode_consistency_valid"),
    ]
    for label, key in fields:
        value = result.get(key, "")
        print(f"  {label:<35} = {value}")
    errors = result.get("errors", [])
    if errors:
        print(_sep())
        print("  ERRORS:")
        for e in errors:
            print(f"    [!] {e}")
    print(_sep())
    if result.get("mode_consistency_valid"):
        print(f"  RESULT: {mode} 모드 일관성 검증 통과")
    else:
        print(f"  RESULT: {mode} 모드 일관성 검증 실패 — 위 오류 확인 필요")
    print(_sep("="))


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS API 모드 진단")
    parser.add_argument("--mode", default="mock", choices=["mock", "real", "paper"])
    parser.add_argument("--json", action="store_true", help="JSON 형식으로 출력")
    args = parser.parse_args()

    result = diagnose_mode(args.mode)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_diagnosis(result)

    if not result.get("mode_consistency_valid"):
        sys.exit(1)


if __name__ == "__main__":
    main()
