"""KIS Open API authentication with strict MOCK/REAL mode isolation."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
from dotenv import load_dotenv

from utils import ensure_dir, load_config, setup_logger

load_dotenv()
logger = setup_logger(__name__, "logs/api.log")

TOKEN_REFRESH_MARGIN_SEC = 3600
MOCK_BASE_URL = "https://openapivts.koreainvestment.com:29443"
REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"


def fingerprint_key(value: str) -> str:
    """API 키 일부만 표시 (로그/CSV 전용, 전체 키 절대 출력 금지)."""
    if not value:
        return "MISSING"
    if len(value) <= 10:
        return value[:2] + "****"
    return value[:6] + "****" + value[-4:]


def get_kis_credentials(mode: str) -> dict:
    """모드별 KIS 인증 정보를 단일 함수로 반환 (엄격한 모드-키 분리).

    MOCK 모드에서는 절대 KIS_REAL_APP_KEY를 읽지 않는다.
    REAL 모드에서는 절대 KIS_MOCK_APP_KEY를 읽지 않는다.
    KIS_MOCK_APP_KEY가 존재하면 MOCK에서 fallback(KIS_APP_KEY)을 사용하지 않는다.
    """
    load_dotenv()
    mode = (mode or "MOCK").strip().upper()

    if mode == "MOCK":
        appkey = os.getenv("KIS_MOCK_APP_KEY", "")
        appsecret = os.getenv("KIS_MOCK_APP_SECRET", "")
        if appkey and appsecret:
            key_type_used = "MOCK_APP_KEY"
        else:
            # KIS_MOCK_APP_KEY 미설정 시에만 fallback 허용
            fallback_key = os.getenv("KIS_APP_KEY", "")
            fallback_secret = os.getenv("KIS_APP_SECRET", "")
            if fallback_key and fallback_secret:
                logger.warning("MOCK mode using KIS_APP_KEY fallback (KIS_MOCK_APP_KEY missing)")
                appkey, appsecret = fallback_key, fallback_secret
                key_type_used = "GENERIC_APP_KEY_FALLBACK"
            else:
                key_type_used = "MISSING"
        account_no = os.getenv("KIS_MOCK_ACCOUNT_NO", "")
        product_code = os.getenv("KIS_MOCK_ACCOUNT_PRODUCT_CODE", "01")
        token_url = f"{MOCK_BASE_URL}/oauth2/tokenP"
        base_url = MOCK_BASE_URL
        token_cache_file = "data/mock_token_cache.json"

    elif mode == "REAL":
        appkey = os.getenv("KIS_REAL_APP_KEY", "")
        appsecret = os.getenv("KIS_REAL_APP_SECRET", "")
        if appkey and appsecret:
            key_type_used = "REAL_APP_KEY"
        else:
            # KIS_REAL_APP_KEY 미설정 시에만 fallback 허용
            fallback_key = os.getenv("KIS_APP_KEY", "")
            fallback_secret = os.getenv("KIS_APP_SECRET", "")
            if fallback_key and fallback_secret:
                logger.warning("REAL mode using KIS_APP_KEY fallback (KIS_REAL_APP_KEY missing)")
                appkey, appsecret = fallback_key, fallback_secret
                key_type_used = "GENERIC_APP_KEY_FALLBACK"
            else:
                key_type_used = "MISSING"
        account_no = os.getenv("KIS_ACCOUNT_NO", "")
        product_code = os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
        token_url = f"{REAL_BASE_URL}/oauth2/tokenP"
        base_url = REAL_BASE_URL
        token_cache_file = "data/real_token_cache.json"

    else:  # PAPER
        appkey = ""
        appsecret = ""
        key_type_used = "NONE"
        account_no = ""
        product_code = "01"
        token_url = None
        base_url = None
        token_cache_file = None

    return {
        "mode": mode,
        "appkey": appkey,
        "appsecret": appsecret,
        "key_type_used": key_type_used,
        "account_no": account_no,
        "product_code": product_code,
        "token_url": token_url,
        "base_url": base_url,
        "token_cache_file": token_cache_file,
        "appkey_fingerprint": fingerprint_key(appkey),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 모듈 레벨 토큰 유틸리티 (앱/CLI에서 KISAuth 인스턴스 없이 사용)
# ──────────────────────────────────────────────────────────────────────────────

def get_token_cache_path(mode: str = "mock") -> str:
    """mode별 토큰 캐시 파일 경로 반환."""
    creds = get_kis_credentials(mode)
    return creds.get("token_cache_file") or ""


def get_token_status(mode: str = "mock") -> Dict:
    """토큰 캐시 상태 조회 — 토큰 원문 절대 반환 안 함."""
    mode_u = (mode or "mock").strip().upper()
    creds = get_kis_credentials(mode_u)
    cache_file = creds.get("token_cache_file") or ""
    account_no = creds.get("account_no", "")
    result: Dict = {
        "mode": mode_u,
        "token_cache_file": cache_file,
        "app_key_masked": creds.get("appkey_fingerprint", ""),
        "account_masked": fingerprint_key(account_no),
        "cache_exists": False,
        "expires_at": None,
        "expires_at_str": "",
        "is_expired": True,
        "remaining_seconds": 0,
        "token_present": False,
    }
    if not cache_file:
        return result
    cache_path = Path(cache_file)
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as _f:
                cache = json.load(_f)
            expires_at = float(cache.get("expires_at", 0))
            token = cache.get("access_token", "")
            remaining = expires_at - time.time()
            import datetime as _dt
            result["cache_exists"] = True
            result["expires_at"] = expires_at
            result["expires_at_str"] = _dt.datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M:%S") if expires_at else ""
            result["is_expired"] = remaining <= 0
            result["remaining_seconds"] = max(0, int(remaining))
            result["token_present"] = bool(token)
        except Exception as exc:
            result["cache_read_error"] = str(exc)
    return result


def delete_token_cache(mode: str = "mock") -> bool:
    """해당 mode의 토큰 캐시 파일만 삭제. True=삭제됨, False=파일 없음."""
    cache_file = get_token_cache_path(mode)
    if not cache_file:
        return False
    cache_path = Path(cache_file)
    if cache_path.exists():
        cache_path.unlink()
        logger.info("Token cache deleted: %s (mode=%s)", cache_file, mode)
        return True
    return False


def refresh_token(mode: str = "mock") -> Dict:
    """해당 mode의 캐시 삭제 후 새 토큰 발급. 토큰 원문 반환 안 함."""
    delete_token_cache(mode)
    try:
        auth = KISAuth(runtime_mode=mode)
        token = auth._request_new_token()
        status = get_token_status(mode)
        return {"success": bool(token), "mode": (mode or "mock").upper(), "token_refreshed": True, **status}
    except Exception as exc:
        logger.error("refresh_token failed mode=%s: %s", mode, exc)
        return {"success": False, "mode": (mode or "mock").upper(), "token_refreshed": False, "error": str(exc)}


def get_token(mode: str = "mock", force_refresh: bool = False) -> str:
    """캐시 우선, force_refresh=True이면 캐시 삭제 후 새로 발급. 토큰 문자열 반환."""
    if force_refresh:
        delete_token_cache(mode)
    auth = KISAuth(runtime_mode=mode)
    return auth.get_access_token()


# ──────────────────────────────────────────────────────────────────────────────

def validate_final_order_headers(
    mode: str,
    headers: Dict[str, str],
) -> Optional[str]:
    """주문 직전 최종 헤더 appkey가 mode에 맞는 키인지 검증.

    Returns:
        None — 검증 통과
        str  — 차단 사유 (API 호출 금지)
    """
    mode = (mode or "").strip().upper()
    header_key = headers.get("appkey", "")

    if mode == "MOCK":
        expected_key = os.getenv("KIS_MOCK_APP_KEY", "")
        if not expected_key:
            # KIS_MOCK_APP_KEY 미설정: fallback 키가 사용됐을 수 있어 경고만
            logger.warning("validate_final_order_headers: KIS_MOCK_APP_KEY not set, cannot verify")
            return None
        if header_key != expected_key:
            return (
                f"MODE_KEY_MISMATCH: MOCK 주문인데 최종 헤더 appkey가 KIS_MOCK_APP_KEY와 일치하지 않습니다. "
                f"expected={fingerprint_key(expected_key)} actual={fingerprint_key(header_key)}"
            )

    elif mode == "REAL":
        expected_key = os.getenv("KIS_REAL_APP_KEY", "")
        if not expected_key:
            logger.warning("validate_final_order_headers: KIS_REAL_APP_KEY not set, cannot verify")
            return None
        if header_key != expected_key:
            return (
                f"MODE_KEY_MISMATCH: REAL 주문인데 최종 헤더 appkey가 KIS_REAL_APP_KEY와 일치하지 않습니다. "
                f"expected={fingerprint_key(expected_key)} actual={fingerprint_key(header_key)}"
            )

    return None


class KISAuth:
    """Issue and cache KIS tokens for exactly one selected mode."""

    def __init__(self, config_path: str = "config.yaml", runtime_mode: Optional[str] = None) -> None:
        self.cfg = load_config(config_path)
        self._timeout = int(self.cfg.get("kis", {}).get("request_timeout_seconds", 10))
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0
        self.token_source: str = ""

        self.mode: str = self._resolve_mode(runtime_mode)
        self._base_url: str = ""
        self._token_cache_path: Path = Path("data/mock_token_cache.json")
        self._app_key: str = ""
        self._app_secret: str = ""
        self.key_type_used: str = ""
        self.app_key_mode_valid: bool = False
        self._configure_for_mode(self.mode)

    def _resolve_mode(self, runtime_mode: Optional[str]) -> str:
        mode = (runtime_mode or "").strip().lower()
        if mode in {"mock", "real", "paper"}:
            return mode.upper()
        return "MOCK" if bool(self.cfg.get("kis", {}).get("use_mock", True)) else "REAL"

    def _configure_for_mode(self, mode: str) -> None:
        self.mode = mode.upper()
        kis = self.cfg.get("kis", {})
        if self.mode == "REAL":
            self._base_url = kis.get("base_url_real", REAL_BASE_URL)
            self._token_cache_path = Path(kis.get("real_token_cache_file", "data/real_token_cache.json"))
        else:
            self._base_url = kis.get("base_url_mock", MOCK_BASE_URL)
            self._token_cache_path = Path(kis.get("mock_token_cache_file", "data/mock_token_cache.json"))

        self._app_key, self._app_secret, self.key_type_used = self._load_credentials_for_mode(self.mode)
        self.app_key_mode_valid = (
            self.mode == "MOCK" and self.key_type_used in {"MOCK_APP_KEY", "GENERIC_APP_KEY_FALLBACK"}
        ) or (
            self.mode == "REAL" and self.key_type_used in {"REAL_APP_KEY", "GENERIC_APP_KEY_FALLBACK"}
        )
        # fingerprint: 실제 사용된 키 앞 6 / 뒤 4 (로그 전용)
        self.appkey_fingerprint: str = fingerprint_key(self._app_key)
        # expected fingerprint: 환경변수에서 직접 읽은 기대값
        if self.mode == "MOCK":
            self.expected_appkey_fingerprint: str = fingerprint_key(os.getenv("KIS_MOCK_APP_KEY", ""))
        elif self.mode == "REAL":
            self.expected_appkey_fingerprint = fingerprint_key(os.getenv("KIS_REAL_APP_KEY", ""))
        else:
            self.expected_appkey_fingerprint = "N/A"
        self.validate_mode_url_consistency()

    def _load_credentials_for_mode(self, mode: str) -> Tuple[str, str, str]:
        kis = self.cfg.get("kis", {})
        if mode == "REAL":
            key_env = kis.get("real_app_key_env", "KIS_REAL_APP_KEY")
            secret_env = kis.get("real_app_secret_env", "KIS_REAL_APP_SECRET")
            key_type = "REAL_APP_KEY"
        else:
            key_env = kis.get("mock_app_key_env", "KIS_MOCK_APP_KEY")
            secret_env = kis.get("mock_app_secret_env", "KIS_MOCK_APP_SECRET")
            key_type = "MOCK_APP_KEY"

        app_key = os.getenv(key_env, "")
        app_secret = os.getenv(secret_env, "")
        if not app_key or not app_secret:
            fallback_key_env = kis.get("app_key_env", "KIS_APP_KEY")
            fallback_secret_env = kis.get("app_secret_env", "KIS_APP_SECRET")
            fallback_key = os.getenv(fallback_key_env, "")
            fallback_secret = os.getenv(fallback_secret_env, "")
            if fallback_key and fallback_secret:
                logger.warning("%s mode is using generic KIS_APP_KEY fallback.", mode)
                return fallback_key, fallback_secret, "GENERIC_APP_KEY_FALLBACK"

        if not app_key or not app_secret:
            logger.warning("Missing KIS credentials for %s mode: %s / %s", mode, key_env, secret_env)
        return app_key, app_secret, key_type

    def get_base_url(self) -> str:
        return self._base_url

    @property
    def token_url(self) -> str:
        return f"{self._base_url}/oauth2/tokenP"

    @property
    def token_cache_file(self) -> str:
        return str(self._token_cache_path)

    def get_account_info(self) -> Tuple[str, str]:
        kis = self.cfg.get("kis", {})
        if self.mode == "MOCK":
            account_no = os.getenv(kis.get("mock_account_no_env", "KIS_MOCK_ACCOUNT_NO"), "")
            product_code = os.getenv(kis.get("mock_account_product_code_env", "KIS_MOCK_ACCOUNT_PRODUCT_CODE"), "01")
        else:
            account_no = os.getenv(kis.get("account_no_env", "KIS_ACCOUNT_NO"), "")
            product_code = os.getenv(kis.get("account_product_code_env", "KIS_ACCOUNT_PRODUCT_CODE"), "01")
        return account_no, product_code

    def validate_mode_url_consistency(self) -> None:
        if self.mode == "MOCK" and "openapivts.koreainvestment.com:29443" not in self._base_url:
            raise RuntimeError(f"MOCK mode cannot use non-MOCK KIS URL: {self._base_url}")
        if self.mode == "REAL" and "openapi.koreainvestment.com:9443" not in self._base_url:
            raise RuntimeError(f"REAL mode cannot use non-REAL KIS URL: {self._base_url}")

    def _get_cached_token(self) -> Optional[str]:
        if not self._token_cache_path.exists():
            return None
        try:
            with open(self._token_cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            expires_at = float(cache.get("expires_at", 0))
            token = cache.get("access_token", "")
            if token and time.time() < expires_at - TOKEN_REFRESH_MARGIN_SEC:
                self.token_source = "mock_token_cache" if self.mode == "MOCK" else "real_token_cache"
                return token
        except Exception as exc:
            logger.debug("Ignoring token cache read failure: %s", exc)
        return None

    def _save_token(self, access_token: str, expires_in: int) -> None:
        try:
            ensure_dir(str(self._token_cache_path.parent))
            with open(self._token_cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "access_token": access_token,
                        "expires_at": time.time() + expires_in,
                        "saved_at": time.time(),
                        "mode": self.mode,
                        "token_url": self.token_url,
                        "key_type_used": self.key_type_used,
                    },
                    f,
                )
        except Exception as exc:
            logger.warning("Token cache save failed: %s", exc)

    def _request_new_token(self) -> str:
        self.validate_mode_url_consistency()
        if not self._app_key or not self._app_secret:
            raise RuntimeError(f"KIS {self.mode} credentials are missing")

        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        headers = {"content-type": "application/json; charset=utf-8"}
        try:
            resp = requests.post(self.token_url, json=payload, headers=headers, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            text = ""
            response = getattr(exc, "response", None)
            if response is not None:
                text = getattr(response, "text", "") or ""
            logger.error(
                "KIS token request failed mode=%s token_url=%s key_type=%s response=%s",
                self.mode,
                self.token_url,
                self.key_type_used,
                text[:500],
            )
            raise RuntimeError(f"KIS token request failed ({self.mode}): {exc}") from exc

        token = data.get("access_token", "")
        if not token:
            msg = data.get("msg1") or data.get("error_description") or str(list(data.keys()))
            raise RuntimeError(f"KIS token response did not include access_token: {msg}")
        expires_in = int(data.get("expires_in", 86400))
        self._access_token = token
        self._expires_at = time.time() + expires_in
        self._save_token(token, expires_in)
        self.token_source = "fresh_mock_token" if self.mode == "MOCK" else "fresh_real_token"
        logger.info("KIS token issued mode=%s key_type=%s cache=%s", self.mode, self.key_type_used, self._token_cache_path)
        return token

    def get_access_token(self, mode: Optional[str] = None) -> str:
        if mode:
            resolved = self._resolve_mode(mode)
            if resolved != self.mode:
                self._access_token = None
                self._expires_at = 0.0
                self._configure_for_mode(resolved)

        if self._access_token and time.time() < self._expires_at - TOKEN_REFRESH_MARGIN_SEC:
            return self._access_token
        cached = self._get_cached_token()
        if cached:
            self._access_token = cached
            return cached
        return self._request_new_token()

    def invalidate_token_cache(self, include_legacy_mock: bool = True) -> None:
        self._access_token = None
        self._expires_at = 0.0
        paths = {self._token_cache_path}
        if include_legacy_mock and self.mode == "MOCK":
            paths.add(Path("data/mock_token_cache.json"))
            paths.add(Path("data/token_cache.json"))
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    logger.info("KIS token cache deleted: %s", path)
            except Exception as exc:
                logger.warning("KIS token cache delete failed: %s (%s)", path, exc)

    def build_auth_headers(self, tr_id: str, extra_headers: Optional[Dict] = None) -> Dict[str, str]:
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_access_token(self.mode.lower())}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def generate_hashkey(self, body: Dict) -> str:
        if not self._app_key or not self._app_secret:
            raise RuntimeError("KIS appkey/appsecret is missing; cannot generate hashkey")
        url = f"{self._base_url}/uapi/hashkey"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"KIS hashkey generation failed: {exc}") from exc
        hashkey = data.get("HASH") or data.get("hash") or data.get("hashkey") or ""
        if not hashkey:
            raise RuntimeError(f"KIS hashkey response did not include HASH: {list(data.keys())}")
        return str(hashkey)

    def diagnostic_metadata(self) -> Dict[str, object]:
        return {
            "base_url": self._base_url,
            "token_url": self.token_url,
            "key_type_used": self.key_type_used,
            "token_cache_file": str(self._token_cache_path),
            "token_source": self.token_source,
            "app_key_mode_valid": self.app_key_mode_valid,
            "appkey_fingerprint": getattr(self, "appkey_fingerprint", fingerprint_key(self._app_key)),
            "expected_appkey_fingerprint": getattr(self, "expected_appkey_fingerprint", ""),
        }
