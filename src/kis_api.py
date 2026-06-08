"""한국투자증권 Open API 호출 래퍼 클래스.

공식 문서 기준 재확인 필요:
  모든 endpoint, TR_ID, 요청/응답 필드는 KIS Developers 공식 문서를 최종 기준으로 합니다.
  https://apiportal.koreainvestment.com/

주문 함수는 SafetyGate를 통과해야만 실행됩니다.
"""

import time
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import requests
from dotenv import load_dotenv

from kis_auth import KISAuth
from safety_gate import SafetyGate, TRADE_MODE_REAL, TRADE_MODE_MOCK
from trading_calendar import (
    SESSION_PRE_MARKET, SESSION_REGULAR, SESSION_CLOSING_AUCTION,
    SESSION_AFTER_CLOSE, SESSION_AFTER_HOURS_SINGLE, SESSION_CLOSED,
    TradingCalendar,
)
from utils import load_config, setup_logger

load_dotenv()
logger = setup_logger(__name__, "logs/api.log")


class KISApiClient:
    """KIS Open API 클라이언트.

    모드에 따라 모의투자 또는 실전투자 URL을 자동 선택합니다.
    주문 함수는 SafetyGate를 통과한 경우에만 실제 API를 호출합니다.
    """

    def __init__(
        self,
        config_path: str = "config.yaml",
        gate: Optional["SafetyGate"] = None,
    ) -> None:
        self._config_path = config_path
        self.cfg = load_config(config_path)
        self.auth = KISAuth(config_path)
        # 외부 gate 주입 허용 — runtime_mode가 적용된 gate를 공유하기 위해
        self.gate = gate if gate is not None else SafetyGate(config_path)
        self._base_url = self.gate.get_base_url()
        # REAL 모드에서만 실전 TR_ID 사용, MOCK/PAPER는 모의투자 TR_ID
        self._use_mock = (self.gate.mode != "REAL")
        self._timeout = self.cfg.get("kis", {}).get("request_timeout_seconds", 10)
        self._sleep = self.cfg.get("kis", {}).get("rate_limit_sleep_seconds", 0.25)
        self._max_retry = self.cfg.get("safety", {}).get("max_order_retries", 3)
        self._account_no, self._product_code = self.auth.get_account_info()
        self._api_error_count: int = 0
        self._max_api_errors = self.cfg.get("safety", {}).get("max_api_error_count", 5)

    # ------------------------------------------------------------------
    # 내부 공통 요청
    # ------------------------------------------------------------------

    def _get(self, path: str, tr_id: str, params: Dict) -> Dict:
        """GET 요청 공통 처리.

        Args:
            path: API 경로
            tr_id: 거래 ID
            params: 쿼리 파라미터

        Returns:
            응답 JSON 딕셔너리
        """
        url = f"{self._base_url}{path}"
        headers = self.auth.build_auth_headers(tr_id)
        for attempt in range(self._max_retry):
            try:
                time.sleep(self._sleep)
                resp = requests.get(url, headers=headers, params=params, timeout=self._timeout)
                resp.raise_for_status()
                data = resp.json()
                self._handle_response_error(data, tr_id)
                self._api_error_count = 0
                return data
            except requests.exceptions.RequestException as e:
                self._api_error_count += 1
                logger.warning(
                    "GET 요청 실패 (시도 %d/%d) tr_id=%s: %s",
                    attempt + 1, self._max_retry, tr_id, str(e)
                )
                if attempt < self._max_retry - 1:
                    time.sleep(1.5 ** attempt)
                else:
                    self._check_api_error_threshold()
                    raise
        return {}

    def _post(self, path: str, tr_id: str, body: Dict) -> Dict:
        """POST 요청 공통 처리 (주문용).

        SafetyGate는 호출 전에 이미 확인되어야 합니다.

        Args:
            path: API 경로
            tr_id: 거래 ID
            body: 요청 본문

        Returns:
            응답 JSON 딕셔너리
        """
        url = f"{self._base_url}{path}"
        headers = self.auth.build_auth_headers(tr_id)
        for attempt in range(self._max_retry):
            try:
                time.sleep(self._sleep)
                resp = requests.post(url, headers=headers, json=body, timeout=self._timeout)
                resp.raise_for_status()
                data = resp.json()
                self._handle_response_error(data, tr_id)
                self._api_error_count = 0
                return data
            except requests.exceptions.RequestException as e:
                self._api_error_count += 1
                logger.error(
                    "POST 요청 실패 (시도 %d/%d) tr_id=%s: %s",
                    attempt + 1, self._max_retry, tr_id, str(e)
                )
                if attempt < self._max_retry - 1:
                    time.sleep(1.5 ** attempt)
                else:
                    self._check_api_error_threshold()
                    raise
        return {}

    def _handle_response_error(self, data: Dict, tr_id: str) -> None:
        """API 응답 오류 코드 처리."""
        rt_cd = data.get("rt_cd", "")
        if rt_cd != "0":
            msg = data.get("msg1", "")
            logger.warning("API 응답 오류 tr_id=%s rt_cd=%s msg=%s", tr_id, rt_cd, msg)

    def _check_api_error_threshold(self) -> None:
        """API 오류가 임계치를 초과하면 신규 주문 중단."""
        if self._api_error_count >= self._max_api_errors:
            logger.critical(
                "⛔ API 오류가 %d회 발생했습니다. 신규 주문을 중단합니다.",
                self._api_error_count,
            )

    def is_api_error_limit_exceeded(self) -> bool:
        """API 오류 한도 초과 여부."""
        return self._api_error_count >= self._max_api_errors

    # ------------------------------------------------------------------
    # 시세 조회 (모의·실전 공통)
    # ------------------------------------------------------------------

    def get_current_price(self, stock_code: str) -> Dict:
        """주식 현재가 조회.

        공식 문서 기준 재확인 필요:
          GET /uapi/domestic-stock/v1/quotations/inquire-price
          TR_ID: FHKST01010100

        Args:
            stock_code: 종목코드 (6자리)

        Returns:
            현재가 정보 딕셔너리
        """
        # TR_ID: FHKST01010100 — 모의/실전 동일
        tr_id = "FHKST01010100"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id,
            params,
        )
        output = data.get("output", {})
        return {
            "stock_code": stock_code,
            "current_price": int(output.get("stck_prpr", 0) or 0),
            "open": int(output.get("stck_oprc", 0) or 0),
            "high": int(output.get("stck_hgpr", 0) or 0),
            "low": int(output.get("stck_lwpr", 0) or 0),
            "volume": int(output.get("acml_vol", 0) or 0),
            "trading_value": int(output.get("acml_tr_pbmn", 0) or 0),
            "change_rate": float(output.get("prdy_ctrt", 0) or 0),
            "stock_name": output.get("hts_kor_isnm", ""),
        }

    def get_daily_ohlcv(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """일봉 OHLCV 데이터 조회.

        공식 문서 기준 재확인 필요:
          GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice
          TR_ID: FHKST03010100

        Args:
            stock_code: 종목코드
            start_date: 시작일 'YYYYMMDD'
            end_date: 종료일 'YYYYMMDD'

        Returns:
            date, open, high, low, close, volume, trading_value DataFrame
        """
        tr_id = "FHKST03010100"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id,
            params,
        )
        records = []
        for row in data.get("output2", []):
            records.append({
                "date": row.get("stck_bsop_date", ""),
                "open": int(row.get("stck_oprc", 0) or 0),
                "high": int(row.get("stck_hgpr", 0) or 0),
                "low": int(row.get("stck_lwpr", 0) or 0),
                "close": int(row.get("stck_clpr", 0) or 0),
                "volume": int(row.get("acml_vol", 0) or 0),
                "trading_value": int(row.get("acml_tr_pbmn", 0) or 0),
            })
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
        return df.sort_values("date").reset_index(drop=True)

    def get_intraday_ohlcv(
        self,
        stock_code: str,
        interval: str = "5",
    ) -> pd.DataFrame:
        """분봉 데이터 조회.

        공식 문서 기준 재확인 필요:
          GET /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice
          TR_ID: FHKST03010200

        Args:
            stock_code: 종목코드
            interval: 분봉 단위 ('1', '5', '10', '15', '30', '60')

        Returns:
            datetime, open, high, low, close, volume DataFrame
        """
        tr_id = "FHKST03010200"
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_HOUR_1": str(interval),
            "FID_PW_DATA_INCU_YN": "N",
        }
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            tr_id,
            params,
        )
        records = []
        for row in data.get("output2", []):
            dt_str = row.get("stck_bsop_date", "") + row.get("stck_cntg_hour", "")
            records.append({
                "datetime": dt_str,
                "open": int(row.get("stck_oprc", 0) or 0),
                "high": int(row.get("stck_hgpr", 0) or 0),
                "low": int(row.get("stck_lwpr", 0) or 0),
                "close": int(row.get("stck_prpr", 0) or 0),
                "volume": int(row.get("cntg_vol", 0) or 0),
            })
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        df["datetime"] = pd.to_datetime(df["datetime"], format="%Y%m%d%H%M%S", errors="coerce")
        return df.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)

    # ------------------------------------------------------------------
    # 잔고 / 주문가능금액
    # ------------------------------------------------------------------

    def get_account_balance(self) -> Dict:
        """계좌 잔고 조회.

        공식 문서 기준 재확인 필요:
          GET /uapi/domestic-stock/v1/trading/inquire-balance
          TR_ID: TTTC8434R (실전) / VTTC8434R (모의)
        """
        tr_id = "VTTC8434R" if self._use_mock else "TTTC8434R"
        params = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "N",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        return self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id,
            params,
        )

    def get_positions(self) -> pd.DataFrame:
        """보유 종목 조회.

        Returns:
            stock_code, stock_name, quantity, avg_price, current_price,
            eval_amount, pnl_rate DataFrame
        """
        data = self.get_account_balance()
        records = []
        for row in data.get("output1", []):
            qty = int(row.get("hldg_qty", 0) or 0)
            if qty <= 0:
                continue
            records.append({
                "stock_code": row.get("pdno", ""),
                "stock_name": row.get("prdt_name", ""),
                "quantity": qty,
                "avg_price": float(row.get("pchs_avg_pric", 0) or 0),
                "current_price": int(row.get("prpr", 0) or 0),
                "eval_amount": int(row.get("evlu_amt", 0) or 0),
                "pnl_rate": float(row.get("evlu_pfls_rt", 0) or 0),
            })
        return pd.DataFrame(records) if records else pd.DataFrame()

    # ------------------------------------------------------------------
    # 세션별 주문구분 코드 해석
    # ------------------------------------------------------------------

    def resolve_order_division(self, order_session: str, side: str = "buy") -> Dict:
        """거래 세션에 따른 주문구분 코드(ORD_DVSN)와 TR_ID를 반환합니다.

        공식 문서 참조:
          KIS Developers https://apiportal.koreainvestment.com/
          POST /uapi/domestic-stock/v1/trading/order-cash

        확인된 코드 (KIS 공식 문서/샘플 기준):
          ORD_DVSN="00" — 지정가 (정규장, 공식 문서 확인됨)

        미확인 코드 (후보 코드, 공식 문서에서 반드시 재확인 필요):
          ORD_DVSN="60" — 장전시간외 후보 코드
          ORD_DVSN="61" — 시간외단일가 후보 코드
          ORD_DVSN="62" — 장후시간외 후보 코드

        REAL 모드에서 미확인 코드를 사용하려면:
          config.yaml: after_hours.real_order_confirmed: true 로 변경 필요

        Returns:
            dict:
                tr_id (str): 주문 TR_ID
                ord_dvsn (str): 주문구분 코드
                description (str): 코드 설명
                is_supported (bool): MOCK에서 시도 가능 여부
                is_confirmed_for_real (bool): REAL 모드에서 안전하게 사용 가능한지 여부
                session (str): 입력 세션명
        """
        is_mock = self._use_mock
        tr_buy = "VTTC0802U" if is_mock else "TTTC0802U"
        tr_sell = "VTTC0801U" if is_mock else "TTTC0801U"
        tr_id = tr_buy if side == "buy" else tr_sell

        if order_session == SESSION_REGULAR:
            return {
                "tr_id": tr_id,
                "ord_dvsn": "00",
                "description": "지정가 (정규장)",
                "is_supported": True,
                "is_confirmed_for_real": True,
                "session": order_session,
            }

        if order_session == SESSION_CLOSING_AUCTION:
            return {
                "tr_id": tr_id,
                "ord_dvsn": "",
                "description": "동시호가 — 주문 미지원",
                "is_supported": False,
                "is_confirmed_for_real": False,
                "session": order_session,
            }

        if order_session == SESSION_CLOSED:
            return {
                "tr_id": tr_id,
                "ord_dvsn": "",
                "description": "장 마감 — 주문 불가",
                "is_supported": False,
                "is_confirmed_for_real": False,
                "session": order_session,
            }

        # 미확인 코드 (PRE_MARKET / AFTER_CLOSE / AFTER_HOURS_SINGLE)
        # REAL 모드에서는 config.yaml after_hours.real_order_confirmed: true 필요
        real_confirmed = self.cfg.get("after_hours", {}).get("real_order_confirmed", False)

        candidate_codes = {
            SESSION_PRE_MARKET: ("60", "장전시간외 (후보 코드 — 공식 문서 재확인 필요)"),
            SESSION_AFTER_CLOSE: ("62", "장후시간외 (후보 코드 — 공식 문서 재확인 필요)"),
            SESSION_AFTER_HOURS_SINGLE: ("61", "시간외단일가 (후보 코드 — 공식 문서 재확인 필요)"),
        }

        if order_session in candidate_codes:
            code, desc = candidate_codes[order_session]
            return {
                "tr_id": tr_id,
                "ord_dvsn": code,
                "description": desc,
                "is_supported": True,  # MOCK에서 시도 가능
                "is_confirmed_for_real": real_confirmed,
                "session": order_session,
            }

        return {
            "tr_id": tr_id,
            "ord_dvsn": "",
            "description": f"알 수 없는 세션: {order_session}",
            "is_supported": False,
            "is_confirmed_for_real": False,
            "session": order_session,
        }

    def get_orderable_cash(self) -> float:
        """주문 가능 현금 조회.

        공식 문서 기준 재확인 필요:
          GET /uapi/domestic-stock/v1/trading/inquire-psbl-order
          TR_ID: TTTC8908R (실전) / VTTC8908R (모의)
        """
        tr_id = "VTTC8908R" if self._use_mock else "TTTC8908R"
        params = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_code,
            "PDNO": "005930",  # 아무 종목 (삼성전자) — 주문가능금액 조회용
            "ORD_UNPR": "0",
            "ORD_DVSN": "01",
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        }
        try:
            data = self._get(
                "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
                tr_id,
                params,
            )
            output = data.get("output", {})
            return float(output.get("ord_psbl_cash", 0) or 0)
        except Exception as e:
            logger.warning("주문가능금액 조회 실패: %s", str(e))
            return 0.0

    # ------------------------------------------------------------------
    # 주문 (SafetyGate 통과 후 실행)
    # ------------------------------------------------------------------

    def place_cash_buy_order(
        self,
        stock_code: str,
        quantity: int,
        price: int,
        order_type: str = "limit",
        order_session: Optional[str] = None,
    ) -> Dict:
        """현금 매수 주문.

        공식 문서 기준 재확인 필요:
          POST /uapi/domestic-stock/v1/trading/order-cash
          TR_ID: TTTC0802U (실전 매수) / VTTC0802U (모의 매수)
          주문구분(ORD_DVSN): 00=지정가(정규장, 확인됨), 61=시간외단일가 등(후보 코드, 확인 필요)

        Args:
            stock_code: 종목코드
            quantity: 주문 수량
            price: 주문 가격 (시장가이면 0)
            order_type: 'limit'=지정가, 'market'=시장가
            order_session: 거래 세션 (None이면 현재 시각으로 자동 판단)

        Raises:
            RuntimeError: SafetyGate 미통과 시
            ValueError: 주문 파라미터 오류 시
            NotImplementedError: REAL 모드에서 미확인 시간외 코드 사용 시
        """
        # SafetyGate: MOCK 또는 REAL 모드만 실제 API 호출 허용
        if not self.gate.is_mock_allowed():
            raise RuntimeError(
                "PAPER 모드에서는 실제 주문 API를 호출할 수 없습니다. "
                "order_manager.py를 통해 가상 주문을 사용하세요."
            )
        if self.gate.mode == TRADE_MODE_REAL:
            self.gate.assert_can_place_real_order()

        # 시장가 주문 허용 여부 확인
        allow_market = self.cfg.get("safety", {}).get("allow_market_order", False)
        if order_type == "market" and not allow_market:
            raise ValueError(
                "시장가 주문이 비활성화되어 있습니다. "
                "config.yaml의 safety.allow_market_order를 true로 설정하세요."
            )

        # 세션 자동 판단
        if order_session is None:
            cal = TradingCalendar(self._config_path)
            order_session = cal.get_market_session()

        resolved = self.resolve_order_division(order_session, "buy")

        if not resolved["is_supported"]:
            raise NotImplementedError(
                f"세션 '{order_session}'에서는 매수 주문을 지원하지 않습니다: "
                f"{resolved['description']}"
            )

        if self.gate.mode == TRADE_MODE_REAL and not resolved["is_confirmed_for_real"]:
            raise NotImplementedError(
                f"REAL 모드에서 세션 '{order_session}' 주문구분 코드({resolved['ord_dvsn']})는 "
                "공식 문서에서 확인되지 않았습니다. "
                "config.yaml: after_hours.real_order_confirmed: true 로 변경 후 재확인 필요"
            )

        if order_type == "market":
            ord_dvsn = "01"
        else:
            ord_dvsn = resolved["ord_dvsn"]

        tr_id = resolved["tr_id"]

        body = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_code,
            "PDNO": stock_code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
        }
        logger.info(
            "[주문] 매수 | 종목=%s | 수량=%d | 가격=%d | 세션=%s | ORD_DVSN=%s | TR_ID=%s | 모드=%s | 계좌=%s",
            stock_code, quantity, price, order_session, ord_dvsn, tr_id,
            self.gate.mode,
            self.gate.mask_account_no(self._account_no),
        )
        return self._post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            body,
        )

    def place_cash_sell_order(
        self,
        stock_code: str,
        quantity: int,
        price: int,
        order_type: str = "limit",
        order_session: Optional[str] = None,
    ) -> Dict:
        """현금 매도 주문.

        공식 문서 기준 재확인 필요:
          POST /uapi/domestic-stock/v1/trading/order-cash
          TR_ID: TTTC0801U (실전 매도) / VTTC0801U (모의 매도)

        Args:
            stock_code: 종목코드
            quantity: 주문 수량
            price: 주문 가격
            order_type: 'limit' 또는 'market'
            order_session: 거래 세션 (None이면 현재 시각으로 자동 판단)

        Raises:
            NotImplementedError: REAL 모드에서 미확인 시간외 코드 사용 시
        """
        if not self.gate.is_mock_allowed():
            raise RuntimeError("PAPER 모드에서는 실제 주문 API를 호출할 수 없습니다.")
        if self.gate.mode == TRADE_MODE_REAL:
            self.gate.assert_can_place_real_order()

        allow_market = self.cfg.get("safety", {}).get("allow_market_order", False)
        if order_type == "market" and not allow_market:
            raise ValueError("시장가 주문 비활성화 (config: safety.allow_market_order=false)")

        if order_session is None:
            cal = TradingCalendar(self._config_path)
            order_session = cal.get_market_session()

        resolved = self.resolve_order_division(order_session, "sell")

        if not resolved["is_supported"]:
            raise NotImplementedError(
                f"세션 '{order_session}'에서는 매도 주문을 지원하지 않습니다: "
                f"{resolved['description']}"
            )

        if self.gate.mode == TRADE_MODE_REAL and not resolved["is_confirmed_for_real"]:
            raise NotImplementedError(
                f"REAL 모드에서 세션 '{order_session}' 주문구분 코드({resolved['ord_dvsn']})는 "
                "공식 문서에서 확인되지 않았습니다. "
                "config.yaml: after_hours.real_order_confirmed: true 로 변경 후 재확인 필요"
            )

        if order_type == "market":
            ord_dvsn = "01"
        else:
            ord_dvsn = resolved["ord_dvsn"]

        tr_id = resolved["tr_id"]

        body = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_code,
            "PDNO": stock_code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
        }
        logger.info(
            "[주문] 매도 | 종목=%s | 수량=%d | 가격=%d | 세션=%s | ORD_DVSN=%s | TR_ID=%s | 모드=%s | 계좌=%s",
            stock_code, quantity, price, order_session, ord_dvsn, tr_id,
            self.gate.mode,
            self.gate.mask_account_no(self._account_no),
        )
        return self._post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            body,
        )

    def cancel_order(
        self,
        original_order_no: str,
        stock_code: str,
        quantity: int,
    ) -> Dict:
        """주문 취소.

        공식 문서 기준 재확인 필요:
          POST /uapi/domestic-stock/v1/trading/order-rvsecncl
          TR_ID: TTTC0803U (실전) / VTTC0803U (모의)
          ORD_DVSN: 취소=02, 정정=01

        Args:
            original_order_no: 원주문번호
            stock_code: 종목코드
            quantity: 취소 수량
        """
        if not self.gate.is_mock_allowed():
            raise RuntimeError("PAPER 모드에서는 취소 API를 호출할 수 없습니다.")

        tr_id = "VTTC0803U" if self._use_mock else "TTTC0803U"
        body = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_code,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": original_order_no,
            "ORD_DVSN": "02",  # 취소
            "RVSE_CNCL_DVSN_CD": "02",  # 취소
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "N",
        }
        logger.info("[주문] 취소 | 원주문번호=%s | 종목=%s | 수량=%d", original_order_no, stock_code, quantity)
        return self._post(
            "/uapi/domestic-stock/v1/trading/order-rvsecncl",
            tr_id,
            body,
        )

    def get_order_status(self, order_no: str) -> Dict:
        """주문 체결 상태 조회.

        공식 문서 기준 재확인 필요:
          GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld
          TR_ID: TTTC8001R (실전) / VTTC8001R (모의)
        """
        tr_id = "VTTC8001R" if self._use_mock else "TTTC8001R"
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_code,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "01",
            "ORD_GNO_BRNO": "",
            "ODNO": order_no,
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        return self._get(
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id,
            params,
        )

    def get_filled_orders(self) -> pd.DataFrame:
        """오늘 체결 내역 조회.

        공식 문서 기준 재확인 필요:
          GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld
          TR_ID: TTTC8001R (실전) / VTTC8001R (모의)
        """
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        tr_id = "VTTC8001R" if self._use_mock else "TTTC8001R"
        params = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_code,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._get(
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id,
            params,
        )
        records = []
        for row in data.get("output1", []):
            records.append({
                "order_no": row.get("odno", ""),
                "stock_code": row.get("pdno", ""),
                "stock_name": row.get("prdt_name", ""),
                "side": "buy" if row.get("sll_buy_dvsn_cd", "") == "02" else "sell",
                "order_qty": int(row.get("ord_qty", 0) or 0),
                "filled_qty": int(row.get("tot_ccld_qty", 0) or 0),
                "filled_price": float(row.get("avg_prvs", 0) or 0),
                "order_time": row.get("ord_tmd", ""),
            })
        return pd.DataFrame(records) if records else pd.DataFrame()

    # ------------------------------------------------------------------
    # 시간외 주문 (TODO — 공식 문서 확인 후 구현)
    # ------------------------------------------------------------------

    def place_after_hours_sell_order(
        self,
        stock_code: str,
        quantity: int,
        price: int,
    ) -> Dict:
        """시간외 단일가 매도 주문.

        TODO: 공식 문서 기준 재확인 필요.
          시간외 주문 TR_ID 및 ORD_DVSN 코드 확인 필요.
          현재 NotImplementedError로 처리.
        """
        raise NotImplementedError(
            "시간외 단일가 주문 TR_ID는 KIS 공식 문서 확인 후 구현 예정입니다. "
            "참고: https://apiportal.koreainvestment.com/apiservice/oauth2#L_5c87ba63-740a-4166-93ac-803510bb9c02"
        )

    def place_pre_market_sell_order(
        self,
        stock_code: str,
        quantity: int,
        price: int,
    ) -> Dict:
        """장전 시간외 매도 주문.

        TODO: 공식 문서 기준 재확인 필요.
          장전 시간외 TR_ID 확인 필요.
        """
        raise NotImplementedError(
            "장전 시간외 주문 TR_ID는 KIS 공식 문서 확인 후 구현 예정입니다."
        )
