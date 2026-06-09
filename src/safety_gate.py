"""실전 주문 방지용 최종 안전장치.

실전 주문이 가능하려면 아래 조건이 모두 충족되어야 합니다:
  1. config.yaml: live_trade: true
  2. config.yaml: kis.use_mock: false
  3. config.yaml: safety.confirm_live_trade: true

실전 테스트 주문(소액)을 위해서는 추가로:
  4. config.yaml: safety.allow_real_test_order: true
  5. config.yaml: force_trade.allow_real_test_order: true

하나라도 충족되지 않으면 실전 주문은 절대 실행되지 않습니다.
force_trade 모드에서도 hard_exclusion은 절대 우회할 수 없습니다.
"""

from typing import Optional

from utils import load_config, setup_logger

logger = setup_logger(__name__, "logs/safety_gate.log")

TRADE_MODE_PAPER = "PAPER"   # 가상 기록 전용 — 실제 API 주문 없음
TRADE_MODE_MOCK = "MOCK"     # KIS 모의투자 API 사용
TRADE_MODE_REAL = "REAL"     # 실전 API — live_trade/use_mock=false/confirm 모두 충족 시


class SafetyGate:
    """실전 주문 최종 안전장치 클래스.

    Attributes:
        mode: 현재 거래 모드 ('PAPER' | 'MOCK' | 'REAL')

    Args:
        config_path: config.yaml 경로
        runtime_mode: CLI --mode 값 ('paper' | 'mock' | 'real' | None).
            지정 시 config 설정보다 우선 적용.
            - 'paper' → 항상 PAPER (API 주문 없음)
            - 'mock'  → 항상 MOCK (live_trade=false여도 허용)
            - 'real'  → 3가지 조건 충족 시 REAL, 미충족 시 PAPER 유지
                        (ForceAutoTrader가 REAL 요청 거부 처리)
            - None    → config 기반 판단 (기존 동작)
    """

    def __init__(
        self,
        config_path: str = "config.yaml",
        runtime_mode: Optional[str] = None,
    ) -> None:
        self.cfg = load_config(config_path)
        self._runtime_mode: Optional[str] = runtime_mode  # 'paper' | 'mock' | 'real' | None
        self._live_trade: bool = bool(self.cfg.get("live_trade", False))
        self._use_mock: bool = bool(self.cfg.get("kis", {}).get("use_mock", True))
        self._confirm: bool = bool(
            self.cfg.get("safety", {}).get("confirm_live_trade", False)
        )
        self._allow_real_test: bool = bool(
            self.cfg.get("safety", {}).get("allow_real_test_order", False)
            and self.cfg.get("force_trade", {}).get("allow_real_test_order", False)
        )
        self._ft_enabled: bool = bool(self.cfg.get("force_trade", {}).get("enabled", False))
        self._max_test_order_amount: int = int(
            self.cfg.get("force_trade", {}).get("test_order_amount", 10_000)
        )
        self.mode: str = self._determine_mode()
        self._log_startup()

    def _determine_mode(self) -> str:
        """CLI runtime_mode 우선, 없으면 config 기반으로 거래 모드를 결정."""
        rm = self._runtime_mode

        # ── CLI 명시 모드 (config보다 우선) ──────────────────────────
        if rm == "paper":
            return TRADE_MODE_PAPER

        if rm == "mock":
            # live_trade=false여도 MOCK 허용 (모의투자 API 사용)
            return TRADE_MODE_MOCK

        if rm == "real":
            # 3가지 조건 모두 충족돼야 REAL
            # 미충족 시 PAPER로 남기고 ForceAutoTrader가 REAL 요청을 거부
            if self._live_trade and not self._use_mock and self._confirm:
                return TRADE_MODE_REAL
            missing = []
            if not self._live_trade:
                missing.append("live_trade=true 필요")
            if self._use_mock:
                missing.append("kis.use_mock=false 필요")
            if not self._confirm:
                missing.append("safety.confirm_live_trade=true 필요")
            logger.warning(
                "REAL 모드 요청이지만 조건 미충족: %s → PAPER 유지",
                ", ".join(missing),
            )
            return TRADE_MODE_PAPER

        # ── runtime_mode 미지정 → config 기반 판단 ───────────────────
        if not self._live_trade:
            return TRADE_MODE_PAPER
        if self._use_mock:
            return TRADE_MODE_MOCK
        if self._confirm:
            return TRADE_MODE_REAL
        logger.warning(
            "live_trade=true, kis.use_mock=false이지만 "
            "safety.confirm_live_trade=false이므로 MOCK 모드로 강제 설정합니다."
        )
        return TRADE_MODE_MOCK

    def _log_startup(self) -> None:
        """시작 시 현재 모드를 명확히 로그에 기록."""
        mode_msgs = {
            TRADE_MODE_PAPER: "[PAPER] 가상 기록 전용 - 실제 API 주문 없음",
            TRADE_MODE_MOCK: "[MOCK] 한국투자증권 모의투자 API 사용",
            TRADE_MODE_REAL: "[REAL] 실전투자 API - 실제 자금 사용!",
        }
        logger.info("SafetyGate init: %s", mode_msgs.get(self.mode, self.mode))

    def is_paper(self) -> bool:
        """PAPER 모드 여부 (실제 API 주문 없음)."""
        return self.mode == TRADE_MODE_PAPER

    def is_live_trade_allowed(self) -> bool:
        """실전 주문 허용 여부 (REAL 모드인지)."""
        return self.mode == TRADE_MODE_REAL

    def is_real_allowed(self) -> bool:
        """실전 주문 허용 여부 (is_live_trade_allowed 별칭)."""
        return self.mode == TRADE_MODE_REAL

    def is_mock_allowed(self) -> bool:
        """KIS API 호출 허용 여부 (MOCK 또는 REAL 모드).

        PAPER 모드에서는 False를 반환하여 API 호출을 차단합니다.
        kis_api.py에서 주문 전 호출합니다.
        """
        return self.mode in (TRADE_MODE_MOCK, TRADE_MODE_REAL)

    def assert_can_place_real_order(self) -> None:
        """실전 주문 가능 여부를 검증. 불가능하면 RuntimeError 발생.

        Raises:
            RuntimeError: REAL 모드가 아닐 때
        """
        if self.mode != TRADE_MODE_REAL:
            raise RuntimeError(
                f"실전 주문 불가 (현재 모드: {self.mode}). "
                "실전 주문을 위해서는 config.yaml에서 "
                "live_trade=true, kis.use_mock=false, "
                "safety.confirm_live_trade=true를 모두 설정하세요."
            )
        logger.warning(
            "[REAL] 실전 주문 실행 중. 실제 자금 사용. +2%% 익절 목표 달성 보장 없음."
        )

    def get_trade_mode(self) -> str:
        """현재 거래 모드 문자열 반환."""
        return self.mode

    def get_base_url(self) -> str:
        """현재 모드에 맞는 KIS API 기본 URL 반환."""
        kis = self.cfg.get("kis", {})
        if self.mode == TRADE_MODE_REAL:
            return kis.get("base_url_real", "https://openapi.koreainvestment.com:9443")
        return kis.get("base_url_mock", "https://openapivts.koreainvestment.com:29443")

    @staticmethod
    def mask_account_no(account_no: str) -> str:
        """계좌번호 마스킹 (로그용).

        예: '50123456' → '501*****'
        """
        if len(account_no) <= 3:
            return "***"
        return account_no[:3] + "*" * (len(account_no) - 3)

    def is_force_trade_enabled(self) -> bool:
        """거래 보장 모드 활성화 여부."""
        return self._ft_enabled

    def is_real_test_order_allowed(self) -> bool:
        """실전 소액 주문 테스트 허용 여부.

        5가지 조건 모두 충족 시 True:
          live_trade=true, use_mock=false, confirm=true,
          safety.allow_real_test_order=true, force_trade.allow_real_test_order=true
        """
        return self.mode == TRADE_MODE_REAL and self._allow_real_test

    def check_real_test_order_conditions(self) -> dict:
        """실전 테스트 주문 허용 조건 5가지를 딕셔너리로 반환."""
        ft = self.cfg.get("force_trade", {})
        safety = self.cfg.get("safety", {})
        return {
            "live_trade=true": self._live_trade,
            "kis.use_mock=false": not self._use_mock,
            "safety.confirm_live_trade=true": self._confirm,
            "safety.allow_real_test_order=true": safety.get("allow_real_test_order", False),
            "force_trade.allow_real_test_order=true": ft.get("allow_real_test_order", False),
        }

    def get_real_order_conditions(self) -> dict:
        """Return all conditions required for a REAL single-stock test order."""
        safety = self.cfg.get("safety", {})
        ft = self.cfg.get("force_trade", {})
        real_trade = self.cfg.get("real_trade", {})
        return {
            "live_trade=true": bool(self.cfg.get("live_trade", False)),
            "paper_trade=false": not bool(self.cfg.get("paper_trade", True)),
            "kis.use_mock=false": not bool(self.cfg.get("kis", {}).get("use_mock", True)),
            "safety.confirm_live_trade=true": bool(safety.get("confirm_live_trade", False)),
            "safety.allow_real_test_order=true": bool(safety.get("allow_real_test_order", False)),
            "force_trade.allow_real_test_order=true": bool(ft.get("allow_real_test_order", False)),
            "real_trade.enabled=true": bool(real_trade.get("enabled", False)),
            "real_trade.allow_single_stock_test=true": bool(real_trade.get("allow_single_stock_test", False)),
            "real_trade.real_order_confirmed_by_user=true": bool(real_trade.get("real_order_confirmed_by_user", False)),
        }

    def is_real_single_test_allowed(self) -> bool:
        return self.mode == TRADE_MODE_REAL and all(self.get_real_order_conditions().values())

    def assert_can_place_real_single_order(self, amount: int, quantity: int, order_type: str = "limit") -> None:
        conditions = self.get_real_order_conditions()
        failed = [k for k, v in conditions.items() if not v]
        if self.mode != TRADE_MODE_REAL:
            failed.append(f"resolved_mode=REAL (current={self.mode})")
        if failed:
            raise RuntimeError("REAL single-stock test blocked: " + ", ".join(failed))
        real_trade = self.cfg.get("real_trade", {})
        safety = self.cfg.get("safety", {})
        max_amount = int(real_trade.get("max_single_test_amount", safety.get("max_real_test_order_amount", 10_000)))
        max_qty = int(real_trade.get("max_single_test_quantity", safety.get("max_real_test_quantity", 1)))
        if amount > max_amount:
            raise RuntimeError(f"REAL single-stock test amount limit exceeded: {amount:,} > {max_amount:,}")
        if quantity > max_qty:
            raise RuntimeError(f"REAL single-stock test quantity limit exceeded: {quantity} > {max_qty}")
        if order_type != real_trade.get("allowed_order_type", "limit"):
            raise RuntimeError(f"REAL order type blocked: {order_type}")
        if order_type == "market" and not bool(real_trade.get("allow_market_order", False)):
            raise RuntimeError("REAL market order is disabled")

    def assert_can_place_real_bulk_order(self, amount: int) -> None:
        failed = []
        real_trade = self.cfg.get("real_trade", {})
        ft = self.cfg.get("force_trade", {})
        if not self.is_real_single_test_allowed():
            failed.extend([k for k, v in self.get_real_order_conditions().items() if not v])
        if not bool(real_trade.get("allow_bulk_buy", False)):
            failed.append("real_trade.allow_bulk_buy=true")
        if not bool(ft.get("allow_real_bulk_order", False)):
            failed.append("force_trade.allow_real_bulk_order=true")
        if bool(self.cfg.get("safety", {}).get("block_real_bulk_order", True)):
            failed.append("safety.block_real_bulk_order=false")
        if failed:
            raise RuntimeError("REAL bulk order blocked: " + ", ".join(failed))

    def assert_real_test_order_allowed(self, order_amount: int = 0) -> None:
        """실전 테스트 주문 가능 여부 검증.

        Raises:
            RuntimeError: 조건 미충족 또는 금액 초과
        """
        conditions = self.check_real_test_order_conditions()
        failed = [k for k, v in conditions.items() if not v]
        if failed:
            raise RuntimeError(
                f"실전 테스트 주문 불가: {', '.join(failed)}"
            )
        if order_amount > 0 and order_amount > self._max_test_order_amount:
            raise RuntimeError(
                f"테스트 주문금액({order_amount:,}원)이 허용 한도({self._max_test_order_amount:,}원)를 초과합니다."
            )

    def validate_order_request(
        self,
        stock_code: str,
        quantity: int,
        price: int,
        side: str,
        total_capital: float = 0.0,
    ) -> None:
        """주문 파라미터 기본 유효성 검증.

        Args:
            stock_code: 종목코드
            quantity: 주문 수량
            price: 주문 가격
            side: 'buy' 또는 'sell'
            total_capital: 총 투자 가능 금액

        Raises:
            ValueError: 유효하지 않은 주문 파라미터
        """
        if not stock_code or len(stock_code) != 6:
            raise ValueError(f"종목코드 오류: '{stock_code}' (6자리여야 함)")
        if quantity <= 0:
            raise ValueError(f"주문 수량 오류: {quantity} (1 이상이어야 함)")
        if price < 0:
            raise ValueError(f"주문 가격 오류: {price} (0 이상이어야 함)")
        if side not in ("buy", "sell"):
            raise ValueError(f"주문 방향 오류: '{side}' ('buy' 또는 'sell')")

        if side == "buy" and total_capital > 0:
            max_weight = self.cfg.get("order", {}).get("max_order_amount_per_stock_rate", 0.05)
            order_amount = price * quantity
            max_amount = total_capital * max_weight
            if order_amount > max_amount:
                raise ValueError(
                    f"주문금액({order_amount:,}원)이 종목당 최대 허용금액"
                    f"({max_amount:,.0f}원 = 총자산 {total_capital:,.0f}원 × {max_weight:.1%})을 초과합니다."
                )
