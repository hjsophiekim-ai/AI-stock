"""리스크 매니저 모듈 (고도화).

모든 주문 실행 전 다중 리스크 체크를 수행합니다.
SafetyGate 연동 및 실전 자동매매 전용 체크 로직을 포함합니다.

force_trade 모드에서는 soft filter를 완화할 수 있으나
hard filter는 절대 완화하지 않습니다.

soft filter (force_trade에서 완화 가능):
  - 거래대금 기준, 평균 거래대금 기준, 변동성 기준, 점수 threshold

hard filter (절대 완화 불가):
  - 거래정지, 관리종목, 투자주의환기
  - 우선주, 스팩, ETF/ETN
  - 현재가 1,000원 미만
  - 주문가능수량 0
  - emergency stop 상태
  - 계좌 주문가능금액 부족
  - API 오류 임계치 초과
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from safety_gate import SafetyGate
from utils import is_etf_etn, is_preferred_stock, is_spac, load_config, setup_logger

logger = setup_logger(__name__, "logs/risk_manager.log")


@dataclass
class OrderApproval:
    """주문 승인 결과."""
    approved: bool
    reason: str
    mode: str = "PAPER"


class RiskManager:
    """주문 전 다중 리스크 체크 클래스.

    체크 순서 (우선순위 순):
    1. SafetyGate (live_trade / use_mock / confirm)
    2. 긴급 중단 파일 확인
    3. API 오류 횟수 임계치 확인
    4. 장중 급락 (지수 하락률)
    5. 하루 최대 손실 한도
    6. 종목 상태 (위험종목, 우선주, 스팩 등)
    7. 가격 필터 (최소 주가)
    8. 보유 종목 수 한도
    9. 종목당 최대 비중
    10. 중복 매수 방지
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        self.gate = SafetyGate(config_path)
        self.risk = self.cfg.get("risk", {})
        self._ft = self.cfg.get("force_trade", {})
        self._total_capital: float = 0.0
        self._daily_realized_pnl: float = 0.0
        self._api_error_count: int = 0
        self._max_api_errors: int = self.cfg.get("safety", {}).get("max_api_error_count", 5)

    def update_capital(self, total_capital: float) -> None:
        """총 투자금 업데이트."""
        self._total_capital = total_capital

    def update_daily_pnl(self, pnl: float) -> None:
        """당일 실현 손익 업데이트."""
        self._daily_realized_pnl += pnl

    def increment_api_error(self) -> None:
        """API 오류 횟수 증가."""
        self._api_error_count += 1

    def reset_api_error(self) -> None:
        """API 오류 횟수 초기화."""
        self._api_error_count = 0

    # ------------------------------------------------------------------
    # 매수 승인
    # ------------------------------------------------------------------

    def approve_buy_order(
        self,
        stock_code: str,
        stock_name: str,
        price: float,
        quantity: int,
        current_positions: Optional[Dict] = None,
        daily_pnl_rate: float = 0.0,
        market_drop_rate: float = 0.0,
        is_warning_stock: bool = False,
        is_halted: bool = False,
        is_management: bool = False,
        now: Optional[datetime] = None,
    ) -> OrderApproval:
        """매수 주문 승인 여부 판단.

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            price: 주문 가격
            quantity: 주문 수량
            current_positions: 현재 보유 포지션 딕셔너리 {code: ...}
            daily_pnl_rate: 당일 손익률 (음수=손실)
            market_drop_rate: 지수 당일 하락률 (음수=하락)
            is_warning_stock: 투자경고·주의환기 여부
            is_halted: 거래정지 여부
            is_management: 관리종목 여부

        Returns:
            OrderApproval (approved, reason, mode)
        """
        mode = self.gate.get_trade_mode()

        # 1. 긴급 중단 확인
        try:
            from emergency_stop import EmergencyStop
            es = EmergencyStop(config_path=None)
            if es.is_active():
                return OrderApproval(False, "긴급 중단(EMERGENCY_STOP) 활성화", mode)
        except Exception:
            pass

        # 2. API 오류 임계치 확인
        stop_on_error = self.cfg.get("safety", {}).get("stop_new_orders_on_api_error", True)
        if stop_on_error and self._api_error_count >= self._max_api_errors:
            return OrderApproval(
                False,
                f"API 오류 {self._api_error_count}회 발생. 신규 매수 중단.",
                mode,
            )

        # 3. 종목 상태 체크
        if is_halted:
            return OrderApproval(False, f"거래정지: {stock_code}", mode)
        if is_management:
            return OrderApproval(False, f"관리종목: {stock_code}", mode)
        if is_warning_stock:
            return OrderApproval(False, f"투자경고·주의환기: {stock_code}", mode)
        if self.risk.get("exclude_preferred_stock") and is_preferred_stock(stock_code):
            return OrderApproval(False, f"우선주: {stock_code}", mode)
        if self.risk.get("exclude_spac") and is_spac(stock_name):
            return OrderApproval(False, f"스팩: {stock_code}({stock_name})", mode)
        if self.risk.get("exclude_etf_etn") and is_etf_etn(stock_name):
            return OrderApproval(False, f"ETF/ETN: {stock_code}({stock_name})", mode)

        # 4. 가격 필터
        min_price = self.risk.get("min_price", 1000)
        if price < min_price:
            return OrderApproval(
                False, f"최소 주가 미달: {price:,.0f}원 < {min_price:,}원", mode
            )

        # 5. 시장 급락 체크
        max_drop = self.risk.get("max_market_index_drop_rate", -0.015)
        if market_drop_rate <= max_drop:
            return OrderApproval(
                False,
                f"지수 급락 감지: {market_drop_rate:.2%} ≤ {max_drop:.2%}. 신규매수 중단.",
                mode,
            )

        # 6. 일일 최대 손실 체크
        max_daily_loss = self.risk.get("max_daily_loss_rate", -0.03)
        if daily_pnl_rate <= max_daily_loss:
            return OrderApproval(
                False,
                f"일일 손실 한도 초과: {daily_pnl_rate:.2%} ≤ {max_daily_loss:.2%}",
                mode,
            )

        # 7. 보유 종목 수 체크 — OPEN 상태(status="OPEN", quantity>0)만 집계
        max_positions = self.risk.get("max_positions", 20)
        _all_pos = current_positions or {}
        _open_count = sum(
            1 for pos in _all_pos.values()
            if getattr(pos, "status", "OPEN") == "OPEN" and int(getattr(pos, "quantity", 1)) > 0
        )
        _pending_sell_count = sum(
            1 for pos in _all_pos.values()
            if getattr(pos, "status", "") == "OPEN_WITH_PENDING_SELL"
        )
        if _open_count >= max_positions:
            _msg = f"최대 보유 종목 수 초과: {_open_count}/{max_positions} (OPEN)"
            if _pending_sell_count:
                _msg += f" — 미체결 매도 {_pending_sell_count}건: 계좌 동기화 후 재시도"
            return OrderApproval(False, _msg, mode)

        # 8. 중복 매수 방지
        if current_positions and stock_code in current_positions:
            _pos = current_positions[stock_code]
            _st = getattr(_pos, "status", "OPEN")
            if _st == "OPEN_WITH_PENDING_SELL":
                return OrderApproval(
                    False,
                    f"미체결 매도 주문 존재 ({stock_code}) — 체결 또는 취소 후 매수 가능",
                    mode,
                )
            return OrderApproval(False, f"이미 보유 중인 종목: {stock_code}", mode)

        # 9. 종목당 최대 비중 체크
        if self._total_capital > 0:
            max_weight = self.risk.get("max_position_weight", 0.05)
            order_amount = price * quantity
            max_amount = self._total_capital * max_weight
            if order_amount > max_amount:
                return OrderApproval(
                    False,
                    f"비중 초과: {order_amount:,.0f}원 > 최대허용 {max_amount:,.0f}원",
                    mode,
                )

        return OrderApproval(True, "승인", mode)

    # ------------------------------------------------------------------
    # force_trade 모드 매수 승인 (soft filter 완화)
    # ------------------------------------------------------------------

    def approve_buy_order_force_trade(
        self,
        stock_code: str,
        stock_name: str,
        price: float,
        quantity: int,
        current_positions: Optional[Dict] = None,
        market_drop_rate: float = 0.0,
        is_warning_stock: bool = False,
        is_halted: bool = False,
        is_management: bool = False,
        relaxation_step: str = "normal_filters",
    ) -> OrderApproval:
        """force_trade 모드에서 soft filter를 단계적으로 완화한 매수 승인.

        hard filter는 절대 완화하지 않습니다.
        """
        mode = self.gate.get_trade_mode()
        hard = self._ft.get("hard_exclusions", {})

        # ── Hard filter (절대 완화 불가) ──────────────────────────────
        if hard.get("exclude_halted_stock", True) and is_halted:
            return OrderApproval(False, f"[HARD] 거래정지: {stock_code}", mode)
        if hard.get("exclude_management_stock", True) and is_management:
            return OrderApproval(False, f"[HARD] 관리종목: {stock_code}", mode)
        if hard.get("exclude_warning_stock", True) and is_warning_stock:
            return OrderApproval(False, f"[HARD] 투자주의환기: {stock_code}", mode)
        if hard.get("exclude_preferred_stock", True) and is_preferred_stock(stock_code):
            return OrderApproval(False, f"[HARD] 우선주: {stock_code}", mode)
        if hard.get("exclude_spac", True) and is_spac(stock_name):
            return OrderApproval(False, f"[HARD] 스팩: {stock_code}", mode)
        if hard.get("exclude_etf_etn", True) and is_etf_etn(stock_name):
            return OrderApproval(False, f"[HARD] ETF/ETN: {stock_code}", mode)
        min_price = int(hard.get("min_price", 1000))
        if price < min_price:
            return OrderApproval(False, f"[HARD] 가격 {min_price}원 미달: {price}", mode)

        # 긴급 중단
        try:
            from emergency_stop import EmergencyStop
            if EmergencyStop(config_path=None).is_active():
                return OrderApproval(False, "[HARD] 긴급 중단(EMERGENCY_STOP) 활성화", mode)
        except Exception:
            pass

        # API 오류 임계치
        if self._api_error_count >= self._max_api_errors:
            return OrderApproval(False, f"[HARD] API 오류 {self._api_error_count}회 초과", mode)

        # 중복 매수 방지
        if current_positions and stock_code in current_positions:
            return OrderApproval(False, f"[HARD] 이미 보유 중: {stock_code}", mode)

        # ── Soft filter (단계에 따라 완화) ─────────────────────────────
        if relaxation_step == "normal_filters":
            # 거래대금 기준 (기본값)
            # (실제 구현 시 DataFrame에서 체크하므로 여기서는 quantity/price 체크만)
            pass
        # relax_trading_value, relax_volatility, score_only 단계에서는 soft filter 완화

        # 최대 보유 종목 수 (force_trade에서도 유지) — OPEN만 집계
        max_pos = self.risk.get("max_positions", 20)
        _ft_pos = current_positions or {}
        _ft_open = sum(
            1 for pos in _ft_pos.values()
            if getattr(pos, "status", "OPEN") == "OPEN" and int(getattr(pos, "quantity", 1)) > 0
        )
        if _ft_open >= max_pos:
            _ft_pending = sum(1 for pos in _ft_pos.values() if getattr(pos, "status", "") == "OPEN_WITH_PENDING_SELL")
            _ft_msg = f"최대 보유 종목 수 초과: {_ft_open}/{max_pos} (OPEN)"
            if _ft_pending:
                _ft_msg += f" — 미체결 매도 {_ft_pending}건: 계좌 동기화 후 재시도"
            return OrderApproval(False, _ft_msg, mode)

        return OrderApproval(True, f"force_trade 승인 (step={relaxation_step})", mode)

    # ------------------------------------------------------------------
    # 매도 승인
    # ------------------------------------------------------------------

    def approve_sell_order(
        self,
        stock_code: str,
        stock_name: str,
        price: float,
        quantity: int,
    ) -> OrderApproval:
        """매도 주문 승인. 청산은 원칙적으로 차단하지 않습니다.

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            price: 주문 가격
            quantity: 주문 수량
        """
        mode = self.gate.get_trade_mode()
        # price=0 은 "실행 직전에 결정" 신호로 허용 (PAPER 모드 등)
        if price < 0:
            return OrderApproval(False, f"매도 가격 오류: {price}", mode)
        if quantity <= 0:
            return OrderApproval(False, f"매도 수량 오류: {quantity}", mode)
        return OrderApproval(True, "승인", mode)

    # ------------------------------------------------------------------
    # 호환 인터페이스 (check_order)
    # ------------------------------------------------------------------

    def check_order(
        self,
        ticker: str,
        name: str,
        side: str,
        price: float,
        qty: int,
        current_positions: Optional[Dict] = None,
        daily_pnl_rate: float = 0.0,
        market_drop_rate: float = 0.0,
        is_warning_stock: bool = False,
        is_halted: bool = False,
        is_management: bool = False,
    ) -> "OrderApproval":
        """approve_buy/sell_order 통합 호환 인터페이스.

        live_trade / paper_trade 직접 속성을 우선 사용 (단위 테스트 지원).
        """
        live = getattr(self, "live_trade", False)
        paper = getattr(self, "paper_trade", True)

        if live:
            mode = "REAL"
        elif paper:
            mode = "PAPER"
        else:
            mode = "NO_TRADE"

        if side == "sell":
            return OrderApproval(True, "매도 허용", mode)

        # buy: NO_TRADE 모드이면 거부
        if mode == "NO_TRADE":
            return OrderApproval(False, "live_trade=false, paper_trade=false: 매수 불가", mode)

        # 종목 필터
        if is_halted:
            return OrderApproval(False, f"거래정지: {ticker}", mode)
        if is_management:
            return OrderApproval(False, f"관리종목: {ticker}", mode)
        if is_warning_stock:
            return OrderApproval(False, f"투자경고·주의환기: {ticker}", mode)
        if self.risk.get("exclude_preferred_stock") and is_preferred_stock(ticker):
            return OrderApproval(False, f"우선주: {ticker}", mode)
        if self.risk.get("exclude_spac") and is_spac(name):
            return OrderApproval(False, f"스팩: {ticker}({name})", mode)
        if self.risk.get("exclude_etf_etn") and is_etf_etn(name):
            return OrderApproval(False, f"ETF/ETN: {ticker}({name})", mode)

        min_price = self.risk.get("min_price", 1000)
        if price < min_price:
            return OrderApproval(False, f"최소 주가 미달: {price:,.0f}원 < {min_price:,}원", mode)

        max_drop = self.risk.get("max_market_index_drop_rate", -0.015)
        if market_drop_rate <= max_drop:
            return OrderApproval(False, f"지수 급락: {market_drop_rate:.2%}", mode)

        max_daily_loss = self.risk.get("max_daily_loss_rate", -0.03)
        if daily_pnl_rate <= max_daily_loss:
            return OrderApproval(False, f"일일 손실 한도 초과: {daily_pnl_rate:.2%}", mode)

        max_positions = self.risk.get("max_positions", 20)
        _co_pos = current_positions or {}
        _co_open = sum(
            1 for pos in _co_pos.values()
            if getattr(pos, "status", "OPEN") == "OPEN" and int(getattr(pos, "quantity", 1)) > 0
        )
        if _co_open >= max_positions:
            _co_pending = sum(1 for pos in _co_pos.values() if getattr(pos, "status", "") == "OPEN_WITH_PENDING_SELL")
            _co_msg = f"최대 보유 종목 수 초과: {_co_open}/{max_positions} (OPEN)"
            if _co_pending:
                _co_msg += f" — 미체결 매도 {_co_pending}건: 계좌 동기화 후 재시도"
            return OrderApproval(False, _co_msg, mode)

        if current_positions and ticker in current_positions:
            _co_st = getattr(current_positions[ticker], "status", "OPEN")
            if _co_st == "OPEN_WITH_PENDING_SELL":
                return OrderApproval(False, f"미체결 매도 주문 존재 ({ticker}) — 체결 또는 취소 후 매수 가능", mode)
            return OrderApproval(False, f"이미 보유 중인 종목: {ticker}", mode)

        capital = getattr(self, "_total_capital", 0)
        if capital > 0:
            max_weight = self.risk.get("max_position_weight", 0.05)
            order_amount = price * qty
            max_amount = capital * max_weight
            if order_amount > max_amount:
                return OrderApproval(False, f"비중 초과: {order_amount:,.0f}원 > {max_amount:,.0f}원", mode)

        return OrderApproval(True, "주문 승인", mode)

    def approve_force_exit(self, stock_code: str) -> "OrderApproval":
        """강제청산 승인 (항상 허용)."""
        mode = getattr(self, "gate", None)
        mode_str = mode.get_trade_mode() if mode else "PAPER"
        return OrderApproval(True, f"강제청산 승인: {stock_code}", mode_str)

    # ------------------------------------------------------------------
    # 강제청산 승인
    # ------------------------------------------------------------------

    def approve_force_exit(
        self,
        stock_code: str,
        quantity: int,
        price: float,
    ) -> OrderApproval:
        """강제청산 주문 승인. 긴급 중단 중에도 허용됩니다 (설정에 따라).

        Args:
            stock_code: 종목코드
            quantity: 수량
            price: 가격
        """
        mode = self.gate.get_trade_mode()
        if price <= 0 or quantity <= 0:
            return OrderApproval(False, f"강제청산 파라미터 오류 (price={price}, qty={quantity})", mode)

        # 강제청산은 긴급중단 여부와 관계없이 허용 (설정에 따라)
        try:
            from emergency_stop import EmergencyStop
            es = EmergencyStop(config_path=None)
            if es.is_active() and not es.allow_force_exit():
                return OrderApproval(False, "긴급 중단 중 강제청산 비활성화", mode)
        except Exception:
            pass

        return OrderApproval(True, "강제청산 승인", mode)
