"""한국투자증권 Open API 인증 모듈.

접근토큰 발급·캐싱·갱신을 담당합니다.
민감정보(APP_KEY, APP_SECRET)는 절대 로그에 남기지 않습니다.

공식 문서 기준 재확인 필요:
  - 토큰 발급 endpoint: POST /oauth2/tokenP
  - 토큰 유효기간: 약 24시간 (86400초)
  - 모의투자 URL: https://openapivts.koreainvestment.com:29443
  - 실전투자 URL: https://openapi.koreainvestment.com:9443
"""

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

# 토큰 만료 여유시간 (초): 만료 60분 전에 재발급
TOKEN_REFRESH_MARGIN_SEC = 3600


class KISAuth:
    """KIS API 인증 토큰 관리.

    토큰을 파일에 캐싱하여 불필요한 재발급 요청을 방지합니다.
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        self._app_key, self._app_secret = self._load_credentials()
        self._base_url = self.get_base_url()
        self._token_cache_path = Path(
            self.cfg.get("kis", {}).get("token_cache_file", "data/token_cache.json")
        )
        self._timeout = self.cfg.get("kis", {}).get("request_timeout_seconds", 10)
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0

    def get_base_url(self) -> str:
        """현재 설정(use_mock)에 맞는 KIS API 기본 URL 반환."""
        kis = self.cfg.get("kis", {})
        use_mock = kis.get("use_mock", True)
        if use_mock:
            return kis.get("base_url_mock", "https://openapivts.koreainvestment.com:29443")
        return kis.get("base_url_real", "https://openapi.koreainvestment.com:9443")

    def _load_credentials(self) -> Tuple[str, str]:
        """환경변수에서 App Key, App Secret 로드.

        민감정보를 로그에 남기지 않습니다.

        Returns:
            (app_key, app_secret) 튜플

        Raises:
            EnvironmentError: 환경변수 미설정 시
        """
        kis = self.cfg.get("kis", {})
        key_env = kis.get("app_key_env", "KIS_APP_KEY")
        secret_env = kis.get("app_secret_env", "KIS_APP_SECRET")
        app_key = os.getenv(key_env, "")
        app_secret = os.getenv(secret_env, "")
        if not app_key or not app_secret:
            logger.warning(
                f"환경변수 {key_env} 또는 {secret_env}가 설정되지 않았습니다. "
                ".env 파일을 확인하세요."
            )
        return app_key, app_secret

    def get_account_info(self) -> Tuple[str, str]:
        """현재 모드에 맞는 계좌번호와 상품코드 반환.

        Returns:
            (account_no, product_code) 튜플
        """
        kis = self.cfg.get("kis", {})
        use_mock = kis.get("use_mock", True)
        if use_mock:
            account_no = os.getenv(
                kis.get("mock_account_no_env", "KIS_MOCK_ACCOUNT_NO"), ""
            )
            product_code = os.getenv(
                kis.get("mock_account_product_code_env", "KIS_MOCK_ACCOUNT_PRODUCT_CODE"), "01"
            )
        else:
            account_no = os.getenv(
                kis.get("account_no_env", "KIS_ACCOUNT_NO"), ""
            )
            product_code = os.getenv(
                kis.get("account_product_code_env", "KIS_ACCOUNT_PRODUCT_CODE"), "01"
            )
        return account_no, product_code

    def _get_cached_token(self) -> Optional[str]:
        """파일 캐시에서 유효한 토큰 로드.

        Returns:
            유효한 토큰 문자열, 없으면 None
        """
        if not self._token_cache_path.exists():
            return None
        try:
            with open(self._token_cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            expires_at = float(cache.get("expires_at", 0))
            if time.time() < expires_at - TOKEN_REFRESH_MARGIN_SEC:
                token = cache.get("access_token", "")
                if token:
                    logger.debug("캐시에서 유효한 토큰 로드 성공")
                    return token
        except Exception as e:
            logger.debug(f"토큰 캐시 읽기 실패 (무시): {e}")
        return None

    def _save_token(self, access_token: str, expires_in: int) -> None:
        """토큰을 파일 캐시에 저장. 민감정보 제외."""
        try:
            ensure_dir(str(self._token_cache_path.parent))
            cache = {
                "access_token": access_token,
                "expires_at": time.time() + expires_in,
                "saved_at": time.time(),
            }
            with open(self._token_cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
        except Exception as e:
            logger.warning(f"토큰 캐시 저장 실패 (무시): {e}")

    def _request_new_token(self) -> str:
        """KIS API에서 신규 접근토큰 발급.

        공식 문서 기준 재확인 필요:
          POST /oauth2/tokenP

        Returns:
            access_token 문자열

        Raises:
            RuntimeError: 토큰 발급 실패 시
        """
        if not self._app_key or not self._app_secret:
            raise RuntimeError(
                "API 키 미설정. .env 파일에 KIS_APP_KEY와 KIS_APP_SECRET을 설정하세요."
            )
        url = f"{self._base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        headers = {"content-type": "application/json; charset=utf-8"}
        try:
            resp = requests.post(
                url, json=payload, headers=headers, timeout=self._timeout
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token", "")
            if not token:
                raise RuntimeError(
                    f"토큰 발급 응답에 access_token 없음: {list(data.keys())}"
                )
            expires_in = int(data.get("expires_in", 86400))
            self._access_token = token
            self._expires_at = time.time() + expires_in
            self._save_token(token, expires_in)
            logger.info("KIS 접근토큰 발급 성공 (유효기간: %d초)", expires_in)
            return token
        except requests.exceptions.RequestException as e:
            logger.error("KIS 토큰 발급 네트워크 오류: %s", str(e))
            raise RuntimeError(f"KIS 토큰 발급 실패 (네트워크): {e}") from e
        except Exception as e:
            logger.error("KIS 토큰 발급 오류: %s", str(e))
            raise RuntimeError(f"KIS 토큰 발급 실패: {e}") from e

    def get_access_token(self) -> str:
        """유효한 접근토큰 반환. 만료 시 자동 재발급.

        Returns:
            유효한 access_token 문자열
        """
        # 1. 인메모리 토큰 유효성 확인
        if (
            self._access_token
            and time.time() < self._expires_at - TOKEN_REFRESH_MARGIN_SEC
        ):
            return self._access_token
        # 2. 파일 캐시 확인
        cached = self._get_cached_token()
        if cached:
            self._access_token = cached
            return cached
        # 3. 신규 발급
        return self._request_new_token()

    def build_auth_headers(
        self,
        tr_id: str,
        extra_headers: Optional[Dict] = None,
    ) -> Dict[str, str]:
        """공통 인증 요청 헤더 생성.

        Args:
            tr_id: 거래 ID (각 API마다 다름)
            extra_headers: 추가 헤더 딕셔너리

        Returns:
            headers 딕셔너리
        """
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_access_token()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers
