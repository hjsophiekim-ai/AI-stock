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
        }
