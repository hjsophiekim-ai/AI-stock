"""환경변수(.env) 관리 서비스.

모의투자 / 실전투자 API 키를 분리 저장하고,
민감정보를 마스킹해서 반환합니다.
"""

import os
import re
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# 지원 키 목록
MOCK_KEYS = ["KIS_MOCK_APP_KEY", "KIS_MOCK_APP_SECRET", "KIS_MOCK_ACCOUNT_NO", "KIS_MOCK_ACCOUNT_PRODUCT_CODE"]
REAL_KEYS = ["KIS_REAL_APP_KEY", "KIS_REAL_APP_SECRET", "KIS_ACCOUNT_NO", "KIS_ACCOUNT_PRODUCT_CODE"]
LEGACY_KEYS = ["KIS_APP_KEY", "KIS_APP_SECRET"]  # 하위호환 (직접 사용 금지)


def load_env() -> Dict[str, str]:
    """현재 .env 파일을 딕셔너리로 반환 (값 없으면 빈 dict)."""
    result: Dict[str, str] = {}
    if not ENV_PATH.exists():
        return result
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip()
    return result


def save_env(env_dict: Dict[str, str]) -> None:
    """딕셔너리를 .env 파일로 저장. 기존 값은 merge."""
    existing = load_env()
    existing.update({k: v for k, v in env_dict.items() if v})  # 빈 값은 덮어쓰지 않음
    lines = ["# AI Stock 자동매매 — 환경변수 (자동 생성, Git 커밋 금지)\n"]
    for k, v in existing.items():
        lines.append(f"{k}={v}\n")
    ENV_PATH.write_text("".join(lines), encoding="utf-8")


def save_mock_keys(app_key: str, app_secret: str, account_no: str, product_code: str = "01") -> None:
    """모의투자 키 저장. 반드시 KIS_MOCK_APP_KEY / KIS_MOCK_APP_SECRET 으로 저장."""
    save_env({
        "KIS_MOCK_APP_KEY": app_key,
        "KIS_MOCK_APP_SECRET": app_secret,
        "KIS_MOCK_ACCOUNT_NO": account_no,
        "KIS_MOCK_ACCOUNT_PRODUCT_CODE": product_code,
    })


def save_real_keys(app_key: str, app_secret: str, account_no: str, product_code: str = "01") -> None:
    """실전투자 키 저장."""
    save_env({
        "KIS_REAL_APP_KEY": app_key,
        "KIS_REAL_APP_SECRET": app_secret,
        "KIS_ACCOUNT_NO": account_no,
        "KIS_ACCOUNT_PRODUCT_CODE": product_code,
    })


def mask(value: str, show: int = 4) -> str:
    """민감정보 마스킹. 앞 show글자만 표시."""
    if not value:
        return "(미설정)"
    if len(value) <= show:
        return "*" * len(value)
    return value[:show] + "*" * (len(value) - show)


def get_masked_env() -> Dict[str, str]:
    """마스킹된 .env 내용 반환."""
    raw = load_env()
    secret_keys = {"KIS_APP_SECRET", "KIS_REAL_APP_SECRET"}
    return {
        k: mask(v, show=0) if k in secret_keys else mask(v, show=4)
        for k, v in raw.items()
    }


def is_env_file_exists() -> bool:
    """Returns True if .env file exists."""
    return ENV_PATH.exists()


def check_mock_keys() -> Dict[str, bool]:
    """모의투자 필수 키 존재 여부 확인."""
    env = load_env()
    return {
        "KIS_MOCK_APP_KEY": bool(env.get("KIS_MOCK_APP_KEY")),
        "KIS_MOCK_APP_SECRET": bool(env.get("KIS_MOCK_APP_SECRET")),
        "KIS_MOCK_ACCOUNT_NO": bool(env.get("KIS_MOCK_ACCOUNT_NO")),
    }


def check_real_keys() -> Dict[str, bool]:
    """실전투자 필수 키 존재 여부 확인."""
    env = load_env()
    return {
        "KIS_REAL_APP_KEY": bool(env.get("KIS_REAL_APP_KEY")),
        "KIS_REAL_APP_SECRET": bool(env.get("KIS_REAL_APP_SECRET")),
        "KIS_ACCOUNT_NO": bool(env.get("KIS_ACCOUNT_NO")),
    }


def inject_to_os_env(env_path: Optional[str] = None) -> Dict[str, object]:
    """현재 .env 값을 os.environ에 주입하고 키 존재 여부를 반환.

    중요: KIS_MOCK_APP_KEY / KIS_MOCK_APP_SECRET / KIS_REAL_APP_KEY / KIS_REAL_APP_SECRET은
    절대 오버라이드하지 않는다. 각 모드별 전용 키가 항상 올바른 값을 유지해야 한다.
    전체 키 값은 절대 출력하지 않는다.
    """
    try:
        from dotenv import load_dotenv as _load_dotenv
        target = Path(env_path) if env_path else ENV_PATH
        _load_dotenv(target, override=False)
    except Exception:
        pass

    env = load_env()
    for k, v in env.items():
        os.environ.setdefault(k, v)

    check_keys = [
        "KIS_MOCK_APP_KEY", "KIS_MOCK_APP_SECRET",
        "KIS_REAL_APP_KEY", "KIS_REAL_APP_SECRET",
        "KIS_ACCOUNT_NO", "KIS_MOCK_ACCOUNT_NO", "DART_API_KEY",
    ]
    return {
        "loaded": ENV_PATH.exists(),
        "env_path": str(ENV_PATH),
        "mock_key": bool(os.environ.get("KIS_MOCK_APP_KEY")),
        "real_key": bool(os.environ.get("KIS_REAL_APP_KEY")),
        "dart_key": bool(os.environ.get("DART_API_KEY")),
        "keys_present": {k: bool(os.environ.get(k)) for k in check_keys},
    }
