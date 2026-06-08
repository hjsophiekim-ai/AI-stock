"""자동매매 실행 엔진.

+2% 익절 목표 자동매매 전략의 핵심 실행 흐름을 담당합니다.
수익을 보장하지 않으며, 실전 사용 전 반드시 모의투자를 완료해야 합니다.

실행 흐름:
  [14:40~15:00] 매수
  [15:40~18:00] 시간외 매도 감시
  [08:30~08:40] 장전 시간외 매도 감시
  [09:00~09:30] 다음날 장초반 매도 감시
  [09:30]       강제청산
"""

import os
import subprocess
import sys
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from emergency_stop import EmergencyStop
from order_manager import OrderManager
from position_manager import PositionManager
from price_monitor import PriceMonitor, RestPriceSource, PaperPriceSource
from risk_manager import RiskManager
from safety_gate import SafetyGate, TRADE_MODE_PAPER
from trading_calendar import TradingCalendar
from utils import ensure_dir, get_today_str, load_config, setup_logger

logger = setup_logger(__name__, "logs/auto_trader.log")


class AutoTrader:
    """자동매매 실행 클래스.

    +2% 익절 목표 전략의 매수·감시·청산을 수행합니다.
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        self.gate = SafetyGate(config_path)
        self.calendar = TradingCalendar(config_path)
        self.es = EmergencyStop(config_path)
        self.pos_mgr = PositionManager(config_path)
        self.order_mgr = OrderManager(config_path)
        self.risk_mgr = self.order_mgr.risk
        self._predictions_dir = self.cfg.get("paths", {}).get("predictions_dir", "reports/predictions")
        self._daily_data_path = self.cfg.get("data", {}).get("raw_daily_path", "data/raw/daily_prices.csv")
        self._buy_top_n = self.cfg.get("strategy", {}).get("buy_top_n", 20)
        self._use_equal_weight = self.cfg.get("strategy", {}).get("use_equal_weight", True)
        self._config_path = config_path

        # 가격 소스 설정
        if self.gate.mode == TRADE_MODE_PAPER:
            price_src = PaperPriceSource(self._daily_data_path)
        else:
            price_src = RestPriceSource(self.order_mgr._api)
        self.monitor = PriceMonitor(self.pos_mgr, price_src, config_path)

    # ------------------------------------------------------------------
    # Top 20 로딩
    # ------------------------------------------------------------------

    def load_today_top20(self, date_str: Optional[str] = None) -> pd.DataFrame:
        """오늘 날짜의 Top 20 예측 파일 로드.

        Args:
            date_str: 날짜 'YYYYMMDD' (None이면 오늘)

        Returns:
            top20 DataFrame

        Raises:
            FileNotFoundError: 예측 파일 없을 때
        """
        ds = date_str or get_today_str("%Y%m%d")
        path = os.path.join(self._predictions_dir, f"top20_{ds}.csv")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Top 20 파일 없음: {path}\n"
                "아래 명령어를 먼저 실행하세요:\n"
                "  python src/predict_candidates.py\n"
                "  python src/select_top20.py"
            )
        df = pd.read_csv(path)
        logger.info("Top 20 로드: %d개 종목 (%s)", len(df), path)
        return df

    # ------------------------------------------------------------------
    # 포지션 사이즈 계산
    # ------------------------------------------------------------------

    def calculate_position_size(self, total_capital: float) -> float:
        """종목당 투자 금액 계산.

        Args:
            total_capital: 총 투자 가능 금액

        Returns:
            종목당 투자 금액
        """
        max_weight = self.cfg.get("risk", {}).get("max_position_weight", 0.05)
        if self._use_equal_weight:
            return total_capital / self._buy_top_n
        return total_capital * max_weight

    # ------------------------------------------------------------------
    # 매수 실행
    # ------------------------------------------------------------------

    def execute_buy_window(self, now: Optional[datetime] = None) -> List[dict]:
        """14:40~15:00 매수 실행.

        Args:
            now: 현재 시각 (None이면 실제 시간)

        Returns:
            매수 결과 리스트
        """
        current = now or datetime.now()
        if not self.calendar.is_buy_window(current):
            logger.info("현재 매수 시간이 아닙니다: %s", current.strftime("%H:%M"))
            return []

        try:
            self.es.assert_not_stopped()
        except RuntimeError as e:
            logger.warning(str(e))
            return []

        try:
            top20 = self.load_today_top20()
        except FileNotFoundError as e:
            logger.error(str(e))
            return []

        total_capital = self.order_mgr.sync_capital()
        per_position = self.calculate_position_size(total_capital)
        logger.info(
            "[매수 시작] 모드=%s | 총자산=%.0f원 | 종목당=%.0f원 | 대상=%d개",
            self.gate.mode, total_capital, per_position, len(top20),
        )

        # 시장 급락 확인
        market_drop = self._get_market_drop_rate()
        results = []
        self.order_mgr.reset_failed_tickers()

        for _, row in top20.iterrows():
            stock_code = str(row["ticker"])
            stock_name = str(row.get("name", stock_code))
            current_price = int(row.get("close", 0))

            result = self.order_mgr.buy_stock(
                stock_code=stock_code,
                stock_name=stock_name,
                target_amount=per_position,
                market_drop_rate=market_drop,
                current_price=current_price if current_price > 0 else None,
            )
            results.append(result)
            if self.order_mgr.risk._api_error_count >= self.order_mgr.risk._max_api_errors:
                logger.critical("API 오류 임계치 초과. 매수 루프 중단.")
                break

        success_count = sum(1 for r in results if r.get("success"))
        logger.info("[매수 완료] 성공=%d/%d", success_count, len(results))
        return results

    # ------------------------------------------------------------------
    # 매도 감시
    # ------------------------------------------------------------------

    def _run_sell_monitor(self, window_name: str, now: Optional[datetime] = None) -> List[Tuple]:
        """공통 매도 감시 실행.

        Args:
            window_name: 감시 구간 이름 (로그용)
            now: 현재 시각

        Returns:
            청산된 종목 [(stock_code, reason, price), ...]
        """
        positions = self.pos_mgr.get_all_positions()
        if not positions:
            return []

        logger.info("[%s 감시] 보유종목=%d개", window_name, len(positions))
        signals = self.monitor.monitor_positions(now)
        cleared = []
        for code, reason, price in signals:
            result = self.order_mgr.sell_all_position(code, reason=reason)
            if result.get("success"):
                cleared.append((code, reason, price))
                logger.info("[매도 완료] %s @ %d원 (사유=%s)", code, price, reason)
            else:
                logger.warning("[매도 실패] %s: %s", code, result.get("reason", ""))
        return cleared

    def monitor_after_hours_sell(self, now: Optional[datetime] = None) -> List[Tuple]:
        """15:40~18:00 시간외 단일가 구간 매도 감시."""
        return self._run_sell_monitor("시간외단일가", now)

    def monitor_pre_market_sell(self, now: Optional[datetime] = None) -> List[Tuple]:
        """08:30~08:40 장전 시간외 구간 매도 감시."""
        return self._run_sell_monitor("장전시간외", now)

    def monitor_next_morning_sell(self, now: Optional[datetime] = None) -> List[Tuple]:
        """09:00~09:30 다음날 장초반 매도 감시."""
        return self._run_sell_monitor("장초반", now)

    def force_exit_all_positions(self, now: Optional[datetime] = None) -> List[Tuple]:
        """09:30 전량 강제청산.

        Returns:
            청산된 종목 리스트
        """
        positions = self.pos_mgr.get_all_positions()
        if not positions:
            logger.info("[강제청산] 보유종목 없음")
            return []

        logger.info("[강제청산 시작] 보유종목=%d개", len(positions))
        cleared = []
        for code in list(positions.keys()):
            pos = self.pos_mgr.get_position(code)
            if pos is None:
                continue
            approval = self.risk_mgr.approve_force_exit(code, pos.quantity, pos.entry_price)
            if not approval.approved:
                logger.warning("[강제청산 거부] %s: %s", code, approval.reason)
                continue

            # 최대 3회 재시도
            success = False
            for attempt in range(3):
                result = self.order_mgr.sell_all_position(code, reason="force_exit_0930")
                if result.get("success"):
                    success = True
                    cleared.append((code, "force_exit_0930", result.get("sell_price", 0)))
                    break
                logger.warning("[강제청산 재시도] %s 시도%d", code, attempt + 1)
            if not success:
                logger.critical("⛔ 강제청산 실패 [%s]. 수동 확인 필요!", code)

        logger.info("[강제청산 완료] %d/%d개 청산", len(cleared), len(positions))
        return cleared

    # ------------------------------------------------------------------
    # 통합 실행
    # ------------------------------------------------------------------

    def run_once(self, now: Optional[datetime] = None) -> None:
        """현재 시간에 맞는 단계만 실행.

        Args:
            now: 현재 시각 (None이면 실제 시간)
        """
        current = now or datetime.now()
        window = self.calendar.current_window_name(current)
        logger.info("[run_once] 현재 시간=%s | 구간=%s | 모드=%s",
                    current.strftime("%H:%M:%S"), window, self.gate.mode)

        if window == "BUY_WINDOW":
            self.execute_buy_window(current)
        elif window == "AFTER_HOURS":
            self.monitor_after_hours_sell(current)
        elif window == "PRE_MARKET":
            self.monitor_pre_market_sell(current)
        elif window == "NEXT_MORNING":
            self.monitor_next_morning_sell(current)
        elif window == "FORCE_EXIT":
            self.force_exit_all_positions(current)
        else:
            logger.info("[run_once] 현재 실행 가능한 구간이 아닙니다: %s", window)

    def run_buy_only(self, now: Optional[datetime] = None) -> List[dict]:
        """매수만 실행 (시간 관계없이)."""
        logger.info("[run_buy_only] 매수 강제 실행")
        return self.execute_buy_window(now or datetime.now())

    def run_sell_monitor_only(self, now: Optional[datetime] = None) -> List[Tuple]:
        """매도 감시만 실행 (시간 관계없이)."""
        logger.info("[run_sell_monitor_only] 매도 감시 강제 실행")
        return self._run_sell_monitor("수동감시", now)

    def run_force_exit_only(self, now: Optional[datetime] = None) -> List[Tuple]:
        """강제청산만 실행 (시간 관계없이)."""
        logger.info("[run_force_exit_only] 강제청산 강제 실행")
        return self.force_exit_all_positions(now)

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------

    def _get_market_drop_rate(self) -> float:
        """코스피 지수 당일 등락률 조회.

        Returns:
            등락률 (음수=하락). 조회 실패 시 0.0 반환.
        """
        if self.gate.mode == TRADE_MODE_PAPER or self.order_mgr._api is None:
            return 0.0
        try:
            # 코스피 지수 현재가 조회
            # 공식 문서 기준 재확인 필요: 지수 코드 0001 (코스피)
            data = self.order_mgr._api.get_current_price("0001")
            return float(data.get("change_rate", 0)) / 100.0
        except Exception:
            return 0.0
