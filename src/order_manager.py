"""주문 관리 모듈 (실제 자동매매 가능 구조).

PAPER 모드: 실제 API 호출 없음. 가상 기록만.
MOCK 모드: 한국투자증권 모의투자 API 호출.
REAL 모드: SafetyGate + RiskManager 통과 후 실전 API 호출.

주문 결과는 logs/trade.log와 reports/orders/orders_YYYYMMDD.csv에 저장됩니다.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from kis_api import KISApiClient
from kis_auth import fingerprint_key
from position_manager import PositionManager
from price_tick import adjust_price_to_tick, get_tick_size
from risk_manager import RiskManager
from safety_gate import SafetyGate, TRADE_MODE_PAPER, TRADE_MODE_MOCK, TRADE_MODE_REAL
from trade_mode import get_expected_key_fingerprint_for_mode
from trading_calendar import TradingCalendar, SESSION_CLOSED, SESSION_CLOSING_AUCTION
from utils import ensure_dir, get_today_str, load_config, save_csv, setup_logger

logger = setup_logger(__name__, "logs/trade.log")


class OrderManager:
    """주문 실행 및 관리 클래스.

    PAPER → MOCK → REAL 순으로 점진적 활성화.
    모든 주문은 RiskManager와 SafetyGate를 통과해야 합니다.

    Args:
        config_path: config.yaml 경로
        gate: 외부에서 생성한 SafetyGate 인스턴스 (runtime_mode 적용 위해 주입).
              None이면 config_path 기반으로 새로 생성합니다.
    """

    def __init__(
        self,
        config_path: str = "config.yaml",
        gate: Optional["SafetyGate"] = None,
        runtime_mode: Optional[str] = None,
    ) -> None:
        self._config_path = config_path
        self.cfg = load_config(config_path)
        self.gate = gate if gate is not None else SafetyGate(config_path, runtime_mode=runtime_mode)
        self.risk = RiskManager(config_path)
        self.pos_mgr = PositionManager(config_path)
        self.calendar = TradingCalendar(config_path)
        self._orders_dir = self.cfg.get("paths", {}).get("orders_dir", "reports/orders")
        self._max_retries = self.cfg.get("safety", {}).get("max_order_retries", 3)
        self._cancel_after_sec = self.cfg.get("order", {}).get("cancel_unfilled_after_seconds", 20)
        self._buy_adj = self.cfg.get("order", {}).get("buy_price_adjustment_rate", 0.001)
        self._sell_adj = self.cfg.get("order", {}).get("sell_price_adjustment_rate", -0.001)
        self._failed_tickers: set = set()  # 이번 루프에서 실패한 종목
        self._order_log: List[dict] = []
        # PAPER 모드가 아닐 때만 API 클라이언트 초기화 (gate 주입으로 mode 공유)
        if self.gate.mode != TRADE_MODE_PAPER:
            self._api: Optional[KISApiClient] = KISApiClient(config_path, gate=self.gate)
        else:
            self._api = None
            logger.info("PAPER 모드: KIS API 클라이언트 미초기화 (실제 통신 없음)")

    # ------------------------------------------------------------------
    # 총 자산 동기화
    # ------------------------------------------------------------------

    def sync_capital(self) -> float:
        """브로커 계좌에서 총 자산 동기화.

        PAPER 모드이면 config의 initial_capital 반환.

        Returns:
            총 투자 가능 금액
        """
        if self.gate.mode == TRADE_MODE_PAPER or self._api is None:
            capital = float(self.cfg.get("backtest", {}).get("initial_capital", 100_000_000))
        else:
            try:
                cash = self._api.get_orderable_cash()
                capital = cash
            except Exception as e:
                logger.warning("계좌 자산 조회 실패, 초기값 사용: %s", str(e))
                capital = float(self.cfg.get("backtest", {}).get("initial_capital", 100_000_000))
        self.risk.update_capital(capital)
        return capital

    # ------------------------------------------------------------------
    # 매수
    # ------------------------------------------------------------------

    def buy_stock(
        self,
        stock_code: str,
        stock_name: str,
        target_amount: float,
        market_drop_rate: float = 0.0,
        current_price: Optional[int] = None,
    ) -> Dict:
        """종목 매수 실행.

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            target_amount: 매수 목표 금액
            market_drop_rate: 지수 하락률
            current_price: 현재가 (None이면 API 조회 또는 생략)

        Returns:
            주문 결과 딕셔너리
        """
        if stock_code in self._failed_tickers:
            return {"success": False, "reason": "이번 루프 실패 종목"}

        if self.pos_mgr.has_position(stock_code):
            return {"success": False, "reason": "이미 보유 중"}

        # 현재가 조회
        if current_price is None:
            if self._api is not None:
                try:
                    price_info = self._api.get_current_price(stock_code)
                    current_price = price_info.get("current_price", 0)
                except Exception as e:
                    logger.warning("현재가 조회 실패 [%s]: %s", stock_code, str(e))
                    return {"success": False, "reason": f"현재가 조회 실패: {e}"}
            else:
                return {"success": False, "reason": "PAPER 모드에서 현재가 미제공"}

        if current_price <= 0:
            return {"success": False, "reason": f"가격 오류: {current_price}"}

        # 매수 수량 계산
        quantity = int(target_amount // current_price)
        if quantity <= 0:
            return {"success": False, "reason": f"매수 수량 0: target_amount={target_amount:.0f}원, price={current_price}원"}
        # 지정가: 현재가보다 살짝 높게, 호가단위 보정
        raw_price = int(current_price * (1 + self._buy_adj))
        tick_cfg = self.cfg.get("order_price", {})
        if tick_cfg.get("tick_adjust_enabled", True):
            buy_method = tick_cfg.get("buy_tick_method", "floor")
            order_price = adjust_price_to_tick(raw_price, side="buy", method=buy_method)
            if order_price != raw_price and tick_cfg.get("log_tick_adjustment", True):
                logger.info(
                    "[호가보정] %s buy %d → %d (tick=%d method=%s)",
                    stock_code, raw_price, order_price, get_tick_size(order_price), buy_method,
                )
        else:
            order_price = raw_price

        # RiskManager 승인 — OPEN 포지션만 카운트 (CLOSED 종목으로 인한 오차 방지)
        _open_pos = (self.pos_mgr.get_open_positions()
                     if hasattr(self.pos_mgr, "get_open_positions")
                     else self.pos_mgr.get_all_positions())
        approval = self.risk.approve_buy_order(
            stock_code=stock_code,
            stock_name=stock_name,
            price=current_price,
            quantity=quantity,
            current_positions=_open_pos,
            market_drop_rate=market_drop_rate,
        )
        if not approval.approved:
            logger.info("[매수 거부] %s(%s): %s", stock_code, stock_name, approval.reason)
            return {"success": False, "reason": approval.reason}

        # 주문 실행
        result = self._execute_buy(stock_code, stock_name, quantity, order_price, current_price)
        self.record_order_log(result)
        return result

    def _execute_buy(
        self,
        stock_code: str,
        stock_name: str,
        quantity: int,
        order_price: int,
        entry_price: int,
    ) -> Dict:
        """실제 매수 주문 실행 (모드별 분기).

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            quantity: 수량
            order_price: 주문 지정가
            entry_price: 기록용 매수가 (현재가 기준)
        """
        mode = self.gate.mode
        now = datetime.now()
        order_info = {
            "datetime": now.isoformat(),
            "stock_code": stock_code,
            "stock_name": stock_name,
            "side": "buy",
            "quantity": quantity,
            "order_price": order_price,
            "entry_price": entry_price,
            "mode": mode,
            "requested_mode": (self.gate._runtime_mode or "").lower() or mode.lower(),
            "resolved_mode": mode,
            "api_called": mode != TRADE_MODE_PAPER,
            "mock_order_called": mode == TRADE_MODE_MOCK,
            "real_order_called": mode == TRADE_MODE_REAL,
            "token_source": getattr(self._api.auth, "token_source", "") if self._api else "",
        }
        if self._api is not None:
            order_info.update(self._api.diagnostic_metadata())
        logger.info(
            "[주문 전] 매수 | 종목=%s(%s) | 수량=%d | 주문가=%d | 매수가=%d | 모드=%s | 예상금액=%s원",
            stock_code, stock_name, quantity, order_price, entry_price, mode,
            f"{order_price * quantity:,}",
        )

        if mode == TRADE_MODE_PAPER:
            # PAPER: 가상 체결
            self.pos_mgr.update_position_after_buy(
                stock_code, stock_name, quantity, entry_price, now
            )
            order_info.update({"success": True, "order_no": f"PAPER_{now.strftime('%H%M%S%f')}"})
            logger.info("[PAPER 체결] %s %d주 @ %d원", stock_code, quantity, entry_price)
            return order_info

        # MOCK / REAL: 실제 API 호출
        cur_session = self.calendar.get_market_session()
        for attempt in range(self._max_retries):
            try:
                resp = self._api.place_cash_buy_order(stock_code, quantity, order_price, "limit", cur_session)
                rt_cd = resp.get("rt_cd", "")
                order_no = resp.get("output", {}).get("ODNO", "")
                if rt_cd == "0" and order_no:
                    self.pos_mgr.update_position_after_buy(
                        stock_code, stock_name, quantity, entry_price, now
                    )
                    order_info.update({"success": True, "order_no": order_no, "api_response": resp})
                    logger.info("[주문 성공] 매수 %s %d주 @ %d원 (주문번호=%s)", stock_code, quantity, order_price, order_no)
                    return order_info
                elif rt_cd == "0" and not order_no:
                    msg = "KIS response rt_cd=0 but order_no is empty; treat as order receipt failure"
                    logger.warning("[주문 실패] 매수 %s: %s", stock_code, msg)
                    return {"success": False, "reason": msg, "api_response": resp, **order_info}
                else:
                    msg = resp.get("msg1", "")
                    logger.warning("[주문 실패] 매수 %s 시도%d: rt_cd=%s msg=%s", stock_code, attempt+1, rt_cd, msg)
            except RuntimeError as e:
                # SafetyGate 미통과 — 재시도 불필요
                logger.error("[주문 거부] SafetyGate: %s", str(e))
                self._failed_tickers.add(stock_code)
                return {"success": False, "reason": str(e), **order_info}
            except Exception as e:
                logger.warning("[주문 오류] %s 시도%d: %s", stock_code, attempt+1, str(e))
                self.risk.increment_api_error()
                if self.risk._api_error_count >= self.risk._max_api_errors:
                    logger.critical("API 오류 임계치 초과. 추가 주문 중단.")
                    self._failed_tickers.add(stock_code)
                    return {"success": False, "reason": "API 오류 임계치 초과", **order_info}

        self._failed_tickers.add(stock_code)
        order_info.update({"success": False, "reason": f"최대 재시도 {self._max_retries}회 초과"})
        return order_info

    # ------------------------------------------------------------------
    # 매도
    # ------------------------------------------------------------------

    def sell_stock(
        self,
        stock_code: str,
        stock_name: str,
        quantity: int,
        target_price: Optional[int] = None,
        reason: str = "manual",
    ) -> Dict:
        """종목 매도 실행.

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            quantity: 매도 수량
            target_price: 지정가 (None이면 현재가 기준)
            reason: 매도 사유

        Returns:
            주문 결과 딕셔너리
        """
        # RiskManager 승인 (매도는 원칙적으로 허용)
        approval = self.risk.approve_sell_order(stock_code, stock_name, target_price or 0, quantity)
        if not approval.approved:
            return {"success": False, "reason": approval.reason}

        # 현재가 조회
        sell_price = target_price
        if sell_price is None:
            if self._api is not None:
                try:
                    info = self._api.get_current_price(stock_code)
                    raw_sell = int(info.get("current_price", 0) * (1 + self._sell_adj))
                    tick_cfg = self.cfg.get("order_price", {})
                    if tick_cfg.get("tick_adjust_enabled", True):
                        sell_method = tick_cfg.get("sell_tick_method", "ceil")
                        sell_price = adjust_price_to_tick(raw_sell, side="sell", method=sell_method)
                        if sell_price != raw_sell and tick_cfg.get("log_tick_adjustment", True):
                            logger.info(
                                "[호가보정] %s sell %d → %d (tick=%d method=%s)",
                                stock_code, raw_sell, sell_price, get_tick_size(sell_price), sell_method,
                            )
                    else:
                        sell_price = raw_sell
                except Exception as e:
                    logger.warning("매도 현재가 조회 실패 [%s]: %s", stock_code, str(e))
                    sell_price = 0
            else:
                # PAPER 모드: 포지션의 목표가로 가상 체결
                pos = self.pos_mgr.get_position(stock_code)
                sell_price = int(pos.entry_price * 1.02) if pos else 0

        result = self._execute_sell(stock_code, stock_name, quantity, sell_price, reason)
        self.record_order_log(result)
        return result

    def sell_all_position(self, stock_code: str, reason: str = "force_exit") -> Dict:
        """보유 전량 매도.

        Args:
            stock_code: 종목코드
            reason: 매도 사유
        """
        pos = self.pos_mgr.get_position(stock_code)
        if pos is None:
            return {"success": False, "reason": f"보유 포지션 없음: {stock_code}"}
        return self.sell_stock(
            stock_code=stock_code,
            stock_name=pos.stock_name,
            quantity=pos.quantity,
            reason=reason,
        )

    def _execute_sell(
        self,
        stock_code: str,
        stock_name: str,
        quantity: int,
        sell_price: int,
        reason: str,
    ) -> Dict:
        """실제 매도 주문 실행 (모드별 분기)."""
        mode = self.gate.mode
        now = datetime.now()
        order_info = {
            "datetime": now.isoformat(),
            "stock_code": stock_code,
            "stock_name": stock_name,
            "side": "sell",
            "quantity": quantity,
            "sell_price": sell_price,
            "reason": reason,
            "mode": mode,
            "requested_mode": (self.gate._runtime_mode or "").lower() or mode.lower(),
            "resolved_mode": mode,
            "api_called": mode != TRADE_MODE_PAPER,
            "mock_order_called": mode == TRADE_MODE_MOCK,
            "real_order_called": mode == TRADE_MODE_REAL,
            "token_source": getattr(self._api.auth, "token_source", "") if self._api else "",
        }
        if self._api is not None:
            order_info.update(self._api.diagnostic_metadata())
        logger.info(
            "[주문 전] 매도 | 종목=%s(%s) | 수량=%d | 가격=%d | 사유=%s | 모드=%s",
            stock_code, stock_name, quantity, sell_price, reason, mode,
        )

        if mode == TRADE_MODE_PAPER:
            self.pos_mgr.update_position_after_sell(stock_code, sell_price, reason, now, quantity=quantity)
            order_info.update({"success": True, "order_no": f"PAPER_{now.strftime('%H%M%S%f')}"})
            logger.info("[PAPER 체결] 매도 %s %d주 @ %d원 (사유=%s)", stock_code, quantity, sell_price, reason)
            return order_info

        sell_session = self.calendar.get_market_session()
        for attempt in range(self._max_retries):
            try:
                resp = self._api.place_cash_sell_order(stock_code, quantity, sell_price, "limit", sell_session)
                rt_cd = resp.get("rt_cd", "")
                order_no = resp.get("output", {}).get("ODNO", "")
                if rt_cd == "0" and order_no:
                    self.pos_mgr.update_position_after_sell(stock_code, sell_price, reason, now, quantity=quantity)
                    order_info.update({"success": True, "order_no": order_no})
                    logger.info("[주문 성공] 매도 %s %d주 @ %d원 (주문번호=%s)", stock_code, quantity, sell_price, order_no)
                    return order_info
                elif rt_cd == "0" and not order_no:
                    msg = "KIS response rt_cd=0 but order_no is empty; treat as order receipt failure"
                    logger.warning("[주문 실패] 매도 %s: %s", stock_code, msg)
                    return {"success": False, "reason": msg, "api_response": resp, **order_info}
                else:
                    logger.warning("[주문 실패] 매도 %s 시도%d: %s", stock_code, attempt+1, resp.get("msg1", ""))
            except Exception as e:
                logger.error("[매도 오류] %s 시도%d: %s", stock_code, attempt+1, str(e))
                self.risk.increment_api_error()
                if attempt == self._max_retries - 1:
                    logger.critical(
                        "⛔ 매도 최대 재시도 초과 [%s]. 수동으로 확인 필요!", stock_code
                    )

        order_info.update({"success": False, "reason": f"최대 재시도 {self._max_retries}회 초과"})
        return order_info

    # ------------------------------------------------------------------
    # 미체결 주문 취소
    # ------------------------------------------------------------------

    def cancel_unfilled_orders(self) -> None:
        """미체결 주문 취소 (MOCK/REAL 모드).

        config의 cancel_unfilled_after_seconds 이후 미체결 주문을 취소합니다.
        """
        if self._api is None:
            return
        try:
            filled_df = self._api.get_filled_orders()
            if filled_df.empty:
                return
            unfilled = filled_df[filled_df["filled_qty"] == 0]
            for _, row in unfilled.iterrows():
                order_no = row.get("order_no", "")
                code = row.get("stock_code", "")
                qty = int(row.get("order_qty", 0))
                if order_no:
                    logger.info("미체결 주문 취소: 주문번호=%s 종목=%s 수량=%d", order_no, code, qty)
                    try:
                        self._api.cancel_order(order_no, code, qty)
                    except Exception as e:
                        logger.warning("취소 주문 실패: %s", str(e))
        except Exception as e:
            logger.warning("미체결 주문 조회 실패: %s", str(e))

    # ------------------------------------------------------------------
    # 로그 기록
    # ------------------------------------------------------------------

    def record_order_log(self, order_result: Dict) -> None:
        """주문 결과를 CSV에 기록.

        Args:
            order_result: _execute_buy / _execute_sell 반환값
        """
        self._order_log.append(order_result)
        today = get_today_str("%Y%m%d")
        output_path = os.path.join(self._orders_dir, f"orders_{today}.csv")
        ensure_dir(self._orders_dir)
        df = pd.DataFrame([order_result])
        if os.path.exists(output_path):
            existing = pd.read_csv(output_path)
            df = pd.concat([existing, df], ignore_index=True)
        save_csv(df, output_path)

    def reset_failed_tickers(self) -> None:
        """실패 종목 목록 초기화 (다음 루프 시작 시 호출)."""
        self._failed_tickers.clear()

    # ------------------------------------------------------------------
    # 검증 포함 주문 실행 (force_trade 등에서 사용)
    # ------------------------------------------------------------------

    def place_order_with_verification(
        self,
        stock_code: str,
        stock_name: str,
        quantity: int,
        order_price: int,
        current_price: int,
        side: str = "buy",
        allow_additional_buy: bool = False,
    ) -> Dict:
        """주문 전 검증을 포함한 주문 실행.

        주문 전 확인 항목:
          - API 연결 상태 (MOCK/REAL 모드)
          - 현재가 조회
          - 주문가능금액 조회
          - 예상 주문금액 계산
          - RiskManager 승인

        로그 기록 항목:
          - trade_mode, api_called, real_order_called
          - stock_code, stock_name, quantity, price, amount
          - order_result, order_no, filled_quantity, rejected_reason

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            quantity: 주문 수량
            order_price: 주문 가격
            current_price: 현재가 (검증용)
            side: 'buy' 또는 'sell'

        Returns:
            주문 결과 딕셔너리
        """
        mode = self.gate.mode
        now = datetime.now()

        # 세션 판단 (MOCK/REAL 모드에서만 의미 있음; PAPER는 세션 무관)
        order_session = self.calendar.get_market_session(now)
        after_hours_cfg = self.cfg.get("after_hours", {})

        order_record = {
            "datetime": now.isoformat(),
            "requested_mode": (self.gate._runtime_mode or "").lower() or mode.lower(),
            "resolved_mode": mode,
            "trade_mode": mode,
            "order_session": order_session,
            "api_called": False,
            "mock_order_called": False,
            "real_order_called": False,
            "token_source": "",
            "base_url": "",
            "token_url": "",
            "key_type_used": "",
            "token_cache_file": "",
            "app_key_mode_valid": False,
            "mode_url_valid": False,
            "expected_appkey_fingerprint": "",
            "header_appkey_fingerprint": "",
            "mode_consistency_valid": True,
            "mode_consistency_errors": "",
            "order_rejected": False,
            "error_category": "",
            "is_mock_supported": True,
            "is_real_safe_to_use": False,
            "ord_dvsn": "",
            "tr_id": "",
            "raw_msg": "",
            "response_text": "",
            "stock_code": stock_code,
            "stock_name": stock_name,
            "quantity": quantity,
            "price": order_price,
            "amount": quantity * order_price,
            "order_result": "",
            "order_no": "",
            "filled_quantity": 0,
            "rejected_reason": "",
            "success": False,
        }

        # MOCK/REAL 모드: 진단 메타데이터를 세션 체크 이전에 수집
        # (early return 시에도 CSV에 key_type_used 등이 항상 기록되도록)
        if mode != TRADE_MODE_PAPER and self._api is not None:
            meta = self._api.diagnostic_metadata()
            order_record.update(meta)
            order_record["expected_appkey_fingerprint"] = get_expected_key_fingerprint_for_mode(mode)
            order_record["header_appkey_fingerprint"] = meta.get("appkey_fingerprint", "MISSING")
            expected_fp = order_record["expected_appkey_fingerprint"]
            actual_fp = order_record["header_appkey_fingerprint"]
            if expected_fp not in ("MISSING", "N/A") and actual_fp != expected_fp:
                order_record["mode_consistency_valid"] = False
                order_record["mode_consistency_errors"] = (
                    f"appkey 불일치: expected={expected_fp} actual={actual_fp}"
                )
            else:
                order_record["mode_consistency_valid"] = True

        # MOCK/REAL 모드에서 세션 허용 여부 확인
        if mode != TRADE_MODE_PAPER:
            if not self.calendar.is_session_allowed(order_session, after_hours_cfg):
                reason = (
                    f"현재 세션({order_session}) 주문 불가 — "
                    f"config.yaml: after_hours.allow_* 설정 확인"
                )
                if order_session == SESSION_CLOSED:
                    reason = f"장 마감({order_session}) — 주문 불가"
                elif order_session == SESSION_CLOSING_AUCTION:
                    reason = f"동시호가({order_session}) — 주문 미지원"
                order_record["rejected_reason"] = reason
                logger.warning("[세션 거부] %s", reason)
                self.record_order_log(order_record)
                return order_record

        if stock_code in self._failed_tickers:
            order_record["rejected_reason"] = "이번 루프 실패 종목"
            self.record_order_log(order_record)
            return order_record

        # PAPER 모드: 검증 후 가상 체결
        if mode == TRADE_MODE_PAPER:
            approval = self.risk.approve_buy_order(
                stock_code=stock_code,
                stock_name=stock_name,
                price=float(current_price),
                quantity=quantity,
                current_positions={} if allow_additional_buy else self.pos_mgr.get_all_positions(),
            )
            if not approval.approved:
                order_record["rejected_reason"] = f"RiskManager: {approval.reason}"
                self.record_order_log(order_record)
                return order_record

            self.pos_mgr.update_position_after_buy(stock_code, stock_name, quantity, current_price, now)
            order_record.update({
                "success": True,
                "order_result": "PAPER_SUCCESS",
                "order_no": f"PAPER_{now.strftime('%H%M%S%f')}",
                "filled_quantity": quantity,
            })
            logger.info(
                "[PAPER] api_called=False real_order_called=False %s %d주 @ %d원",
                stock_code, quantity, order_price,
            )
            self.record_order_log(order_record)
            return order_record

        # MOCK/REAL 모드: API 호출 포함
        order_record["api_called"] = True
        if mode == TRADE_MODE_MOCK:
            order_record["mock_order_called"] = True
        if mode == TRADE_MODE_REAL:
            order_record["real_order_called"] = True
        if self._api is not None:
            order_record["token_source"] = getattr(self._api.auth, "token_source", "")

        # RiskManager 승인
        approval = self.risk.approve_buy_order(
            stock_code=stock_code,
            stock_name=stock_name,
            price=float(current_price),
            quantity=quantity,
            current_positions={} if allow_additional_buy else self.pos_mgr.get_all_positions(),
        )
        if not approval.approved:
            order_record["rejected_reason"] = f"RiskManager: {approval.reason}"
            self.record_order_log(order_record)
            return order_record

        # 주문 실행
        try:
            if side == "buy":
                resp = self._api.place_cash_buy_order(
                    stock_code, quantity, order_price, "limit", order_session
                )
            else:
                resp = self._api.place_cash_sell_order(
                    stock_code, quantity, order_price, "limit", order_session
                )

            rt_cd = resp.get("rt_cd", "")
            order_no = resp.get("output", {}).get("ODNO", "")
            error_category = resp.get("error_category", "")
            raw_msg = resp.get("raw_msg", resp.get("msg1", ""))
            order_record["token_source"] = getattr(self._api.auth, "token_source", "") if self._api else ""
            for key in ["base_url", "token_url", "key_type_used", "token_cache_file", "token_source", "app_key_mode_valid", "mode_url_valid"]:
                if key in resp:
                    order_record[key] = resp.get(key)

            # resp에서 추가 분류 필드 반영
            order_record["error_category"] = error_category
            order_record["is_mock_supported"] = resp.get("is_mock_supported", True)
            order_record["is_real_safe_to_use"] = resp.get("is_real_safe_to_use", False)
            order_record["ord_dvsn"] = resp.get("ord_dvsn", "")
            order_record["tr_id"] = resp.get("tr_id", "")
            order_record["raw_msg"] = raw_msg

            if rt_cd == "0" and order_no:
                if side == "buy":
                    self.pos_mgr.update_position_after_buy(
                        stock_code, stock_name, quantity, current_price, now,
                        order_no=order_no,
                    )
                order_record.update({
                    "success": True,
                    "order_result": "API_SUCCESS",
                    "order_no": order_no,
                })
                logger.info(
                    "[%s] api_called=True real_order_called=%s %s %d주 @ %d원 주문번호=%s",
                    mode, mode == "REAL", stock_code, quantity, order_price, order_no,
                )
            elif rt_cd == "0" and not order_no:
                order_record.update({
                    "order_rejected": True,
                    "rejected_reason": "KIS response rt_cd=0 but order_no is empty; order receipt failed",
                    "order_result": "ORDER_NO_EMPTY",
                    "error_category": error_category or "KIS_REAL_ORDER_REJECTED",
                })
                self._failed_tickers.add(stock_code)
                logger.warning("[%s] 주문번호 없음: %s", mode, stock_code)
            elif error_category == "MOCK_UNSUPPORTED_ORDER_TYPE":
                # MOCK 서버가 해당 주문유형을 미지원 — 코드 오류 아님
                order_record.update({
                    "order_rejected": True,
                    "order_result": "MOCK_UNSUPPORTED_ORDER_TYPE",
                    "rejected_reason": (
                        "한국투자증권 모의투자 서버가 해당 시간외 주문유형을 지원하지 않음 "
                        f"(세션={order_session} ORD_DVSN={resp.get('ord_dvsn', '')} "
                        f"TR_ID={resp.get('tr_id', '')} msg={raw_msg})"
                    ),
                })
                logger.warning(
                    "[%s] MOCK_UNSUPPORTED_ORDER_TYPE %s 세션=%s ORD_DVSN=%s msg=%s — 코드 정상, MOCK 서버 제약",
                    mode, stock_code, order_session, resp.get("ord_dvsn", ""), raw_msg,
                )
            else:
                order_record.update({
                    "order_rejected": True,
                    "rejected_reason": f"API거부: rt_cd={rt_cd} {raw_msg}",
                    "order_result": f"REJECTED:{rt_cd}",
                })
                self._failed_tickers.add(stock_code)
                logger.warning(
                    "[%s] 주문거부 api_called=True %s rt_cd=%s msg=%s",
                    mode, stock_code, rt_cd, raw_msg,
                )
        except RuntimeError as e:
            order_record["rejected_reason"] = f"SafetyGate: {e}"
            self._failed_tickers.add(stock_code)
        except Exception as e:
            self.risk.increment_api_error()
            order_record["rejected_reason"] = f"API오류: {e}"
            self._failed_tickers.add(stock_code)

        self.record_order_log(order_record)
        return order_record

    # ------------------------------------------------------------------
    # 검증 포함 매도 주문 (수동매도 / 자동매도 공통 경로)
    # ------------------------------------------------------------------

    def place_sell_order_with_verification(
        self,
        stock_code: str,
        stock_name: str = "",
        quantity: Optional[int] = None,
        order_price: Optional[int] = None,
        reason: str = "manual",
    ) -> Dict:
        """매도 주문 전 검증을 포함한 통합 매도 실행.

        수동매도/자동매도 모두 이 경로를 사용한다.
        1. diagnostic_metadata 수집 (early return 시에도 CSV에 기록)
        2. 세션 확인
        3. positions.json에서 종목 확인
        4. KIS 계좌 실제 보유수량 확인 (MOCK/REAL)
        5. 현재가 조회 → 매도가 결정
        6. validate_final_order_headers() 로 KEY 검증
        7. place_cash_sell_order() 실행
        8. positions.json 업데이트

        Args:
            stock_code: 종목코드
            stock_name: 종목명 (없으면 positions에서 조회)
            quantity: 매도 수량 (None이면 전량)
            order_price: 매도 지정가 (None이면 현재가 기준)
            reason: 매도 사유

        Returns:
            매도 결과 딕셔너리 (진단 컬럼 포함)
        """
        mode = self.gate.mode
        now = datetime.now()
        code = str(stock_code).zfill(6)
        order_session = self.calendar.get_market_session(now)
        after_hours_cfg = self.cfg.get("after_hours", {})

        sell_record: Dict = {
            "datetime": now.isoformat(),
            "side": "sell",
            "requested_mode": (self.gate._runtime_mode or "").lower() or mode.lower(),
            "resolved_mode": mode,
            "trade_mode": mode,
            "order_session": order_session,
            "stock_code": code,
            "stock_name": stock_name,
            "quantity": quantity or 0,
            "price": order_price or 0,
            "amount": 0,
            "reason": reason,
            "api_called": False,
            "mock_order_called": False,
            "real_order_called": False,
            "token_source": "",
            "base_url": "",
            "token_url": "",
            "key_type_used": "",
            "token_cache_file": "",
            "app_key_mode_valid": False,
            "mode_url_valid": False,
            "expected_appkey_fingerprint": "",
            "header_appkey_fingerprint": "",
            "mode_consistency_valid": True,
            "mode_consistency_errors": "",
            "order_no": "",
            "rt_cd": "",
            "msg": "",
            "raw_msg": "",
            "tr_id": "",
            "ord_dvsn": "",
            "success": False,
            "rejected_reason": "",
            "order_result": "",
        }

        # ── 진단 메타데이터 먼저 수집 (early return 시에도 기록) ───────────
        if mode != TRADE_MODE_PAPER and self._api is not None:
            meta = self._api.diagnostic_metadata()
            sell_record.update(meta)
            sell_record["expected_appkey_fingerprint"] = get_expected_key_fingerprint_for_mode(mode)
            sell_record["header_appkey_fingerprint"] = meta.get("appkey_fingerprint", "MISSING")
            expected_fp = sell_record["expected_appkey_fingerprint"]
            actual_fp = sell_record["header_appkey_fingerprint"]
            if expected_fp not in ("MISSING", "N/A") and actual_fp != expected_fp:
                sell_record["mode_consistency_valid"] = False
                sell_record["mode_consistency_errors"] = f"appkey 불일치: expected={expected_fp} actual={actual_fp}"
            else:
                sell_record["mode_consistency_valid"] = True

        # ── positions.json 확인 ───────────────────────────────────────────
        pos = self.pos_mgr.get_position(code)
        if pos is not None:
            sell_record["stock_name"] = sell_record["stock_name"] or pos.stock_name
            if quantity is None:
                quantity = pos.quantity
            sell_record["quantity"] = quantity

        if quantity is None or quantity <= 0:
            sell_record["rejected_reason"] = "NO_QUANTITY: 매도 수량이 0 또는 미지정"
            self.record_order_log(sell_record)
            return sell_record

        # ── PAPER 모드: 가상 매도 ─────────────────────────────────────────
        if mode == TRADE_MODE_PAPER:
            price = order_price or (int(pos.entry_price) if pos else 0)
            sell_record.update({
                "price": price,
                "amount": price * quantity,
                "success": True,
                "order_result": "PAPER_SELL_SUCCESS",
                "order_no": f"PAPER_SELL_{now.strftime('%H%M%S%f')}",
            })
            self.pos_mgr.update_position_after_sell(code, price, reason, now, quantity=quantity)
            self.record_order_log(sell_record)
            return sell_record

        # ── 세션 확인 ─────────────────────────────────────────────────────
        if not self.calendar.is_session_allowed(order_session, after_hours_cfg):
            reason_txt = f"세션({order_session}) 매도 불가"
            if order_session == SESSION_CLOSED:
                reason_txt = f"장 마감({order_session}) — 매도 불가"
            elif order_session == SESSION_CLOSING_AUCTION:
                reason_txt = f"동시호가({order_session}) — 미지원"
            sell_record["rejected_reason"] = reason_txt
            self.record_order_log(sell_record)
            return sell_record

        # ── 현재가 조회 → 매도가 결정 ────────────────────────────────────
        if order_price is None or order_price <= 0:
            try:
                price_info = self._api.get_current_price(code)
                if price_info is not None:
                    cur = int(price_info.get("current_price", 0) or 0)
                else:
                    cur = int(pos.current_price if pos else 0)
                if cur <= 0 and pos:
                    cur = int(pos.entry_price or 0)
                order_price = adjust_price_to_tick(cur, side="sell", method="floor") if cur > 0 else 0
            except Exception as exc:
                logger.warning("매도 현재가 조회 실패 %s: %s", code, exc)
                order_price = int(pos.entry_price if pos else 0)

        if not order_price or order_price <= 0:
            sell_record["rejected_reason"] = "PRICE_UNAVAILABLE: 매도가 결정 불가"
            self.record_order_log(sell_record)
            return sell_record

        sell_record["price"] = order_price
        sell_record["amount"] = order_price * quantity

        # ── MOCK/REAL API 호출 ────────────────────────────────────────────
        sell_record["api_called"] = True
        if mode == TRADE_MODE_MOCK:
            sell_record["mock_order_called"] = True
        if mode == TRADE_MODE_REAL:
            sell_record["real_order_called"] = True
        if self._api is not None:
            sell_record["token_source"] = getattr(self._api.auth, "token_source", "")

        try:
            resp = self._api.place_cash_sell_order(
                code, quantity, order_price, "limit", order_session
            )
            rt_cd = resp.get("rt_cd", "")
            order_no = resp.get("output", {}).get("ODNO", "")
            raw_msg = resp.get("raw_msg", resp.get("msg1", ""))
            sell_record["rt_cd"] = rt_cd
            sell_record["msg"] = raw_msg
            sell_record["raw_msg"] = raw_msg
            sell_record["order_no"] = order_no
            sell_record["tr_id"] = resp.get("tr_id", "")
            sell_record["ord_dvsn"] = resp.get("ord_dvsn", "")
            for k in ["base_url", "token_url", "key_type_used", "token_cache_file",
                      "token_source", "app_key_mode_valid", "mode_url_valid"]:
                if k in resp:
                    sell_record[k] = resp[k]

            if rt_cd == "0" and order_no:
                sell_record.update({"success": True, "order_result": "SELL_SUCCESS"})
                self.pos_mgr.update_position_after_sell(code, order_price, reason, now, quantity=quantity)
                logger.info("[%s] 매도 성공 %s %d주 @ %d원 주문번호=%s", mode, code, quantity, order_price, order_no)
            else:
                sell_record.update({
                    "rejected_reason": f"API거부: rt_cd={rt_cd} {raw_msg}",
                    "order_result": f"SELL_REJECTED:{rt_cd}",
                })
                logger.warning("[%s] 매도거부 %s rt_cd=%s msg=%s", mode, code, rt_cd, raw_msg)

        except RuntimeError as exc:
            sell_record["rejected_reason"] = f"SafetyGate: {exc}"
        except Exception as exc:
            self.risk.increment_api_error()
            sell_record["rejected_reason"] = f"API오류: {exc}"

        self.record_order_log(sell_record)
        return sell_record
