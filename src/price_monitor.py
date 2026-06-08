"""가격 감시 모듈.

보유 종목의 현재가를 주기적으로 조회하여
익절/손절/강제청산 조건 충족 시 신호를 반환합니다.

현재 구현: REST polling 방식.
추후 고도화: WebSocket 실시간 수신으로 교체 가능하도록 인터페이스 분리.
"""

import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from position_manager import PositionManager, PositionRecord
from utils import load_config, setup_logger

logger = setup_logger(__name__, "logs/price_monitor.log")


# ------------------------------------------------------------------
# 가격 소스 추상 인터페이스 (WebSocket 교체 시 이 인터페이스 구현)
# ------------------------------------------------------------------

class PriceSource(ABC):
    """가격 데이터 소스 추상 클래스."""

    @abstractmethod
    def get_price(self, stock_code: str) -> Optional[int]:
        """현재가 반환. 실패 시 None."""
        ...

    @abstractmethod
    def get_prices_bulk(self, stock_codes: List[str]) -> Dict[str, int]:
        """여러 종목 현재가 일괄 반환."""
        ...


class RestPriceSource(PriceSource):
    """KIS REST API polling 방식 가격 소스."""

    def __init__(self, api_client) -> None:
        self._api = api_client
        self._error_counts: Dict[str, int] = {}
        self._max_errors = 5

    def get_price(self, stock_code: str) -> Optional[int]:
        """단일 종목 현재가 조회."""
        if self._error_counts.get(stock_code, 0) >= self._max_errors:
            return None
        try:
            data = self._api.get_current_price(stock_code)
            price = data.get("current_price", 0)
            if price > 0:
                self._error_counts[stock_code] = 0
                return int(price)
            return None
        except Exception as e:
            self._error_counts[stock_code] = self._error_counts.get(stock_code, 0) + 1
            logger.warning("가격 조회 실패 [%s] (오류=%d): %s", stock_code, self._error_counts[stock_code], str(e))
            return None

    def get_prices_bulk(self, stock_codes: List[str]) -> Dict[str, int]:
        """여러 종목 현재가 순차 조회."""
        result = {}
        for code in stock_codes:
            price = self.get_price(code)
            if price is not None:
                result[code] = price
        return result


class PaperPriceSource(PriceSource):
    """PAPER 모드용 가상 가격 소스 (일봉 데이터 기반)."""

    def __init__(self, daily_data_path: str) -> None:
        import pandas as pd
        self._prices: Dict[str, int] = {}
        try:
            df = pd.read_csv(daily_data_path, parse_dates=["date"])
            latest = df.sort_values("date").groupby("ticker").last()["close"]
            self._prices = {k: int(v) for k, v in latest.items()}
        except Exception as e:
            logger.warning("일봉 가격 로드 실패: %s", str(e))

    def get_price(self, stock_code: str) -> Optional[int]:
        return self._prices.get(stock_code)

    def get_prices_bulk(self, stock_codes: List[str]) -> Dict[str, int]:
        return {c: p for c in stock_codes if (p := self._prices.get(c)) is not None}


# ------------------------------------------------------------------
# 가격 감시 메인 클래스
# ------------------------------------------------------------------

class PriceMonitor:
    """보유 종목 가격 감시 및 청산 신호 생성 클래스.

    익절/손절/강제청산 조건 충족 시 해당 종목코드와 사유를 반환합니다.
    """

    EXIT_TAKE_PROFIT = "take_profit"
    EXIT_STOP_LOSS = "stop_loss"
    EXIT_FORCE_EXIT = "forced_exit_0930"

    def __init__(
        self,
        pos_mgr: PositionManager,
        price_source: PriceSource,
        config_path: str = "config.yaml",
    ) -> None:
        self.pos_mgr = pos_mgr
        self.price_source = price_source
        self.cfg = load_config(config_path)
        self._poll_interval = self.cfg.get("kis", {}).get("rate_limit_sleep_seconds", 0.25)

    def monitor_positions(
        self,
        now: Optional[datetime] = None,
    ) -> List[Tuple[str, str, int]]:
        """보유 포지션 전체를 한 번 스캔하여 청산 신호 반환.

        Args:
            now: 현재 시각 (None이면 실제 시간)

        Returns:
            [(stock_code, exit_reason, current_price), ...] 리스트
        """
        positions = self.pos_mgr.get_all_positions()
        if not positions:
            return []

        codes = list(positions.keys())
        prices = self.price_source.get_prices_bulk(codes)
        signals = []

        for code, pos in positions.items():
            price = prices.get(code)
            if price is None:
                logger.debug("가격 없음: %s — 스킵", code)
                continue
            reason = self._check_exit(code, price, pos, now)
            if reason:
                signals.append((code, reason, price))
                logger.info(
                    "청산 신호 [%s] %s: 현재가=%d, 매수가=%.0f, 목표=%.0f, 손절=%.0f",
                    code, reason, price, pos.entry_price, pos.target_price, pos.stop_price,
                )
        return signals

    def _check_exit(
        self,
        stock_code: str,
        current_price: int,
        pos: PositionRecord,
        now: Optional[datetime],
    ) -> Optional[str]:
        """단일 종목 청산 조건 확인.

        Returns:
            exit reason 또는 None
        """
        if self.check_take_profit(stock_code, current_price):
            return self.EXIT_TAKE_PROFIT
        if self.check_stop_loss(stock_code, current_price):
            return self.EXIT_STOP_LOSS
        if self.check_force_exit_time(stock_code, now):
            return self.EXIT_FORCE_EXIT
        return None

    def check_take_profit(self, stock_code: str, current_price: int) -> bool:
        """+2% 익절 조건 확인."""
        return self.pos_mgr.should_take_profit(stock_code, float(current_price))

    def check_stop_loss(self, stock_code: str, current_price: int) -> bool:
        """-3% 손절 조건 확인."""
        return self.pos_mgr.should_stop_loss(stock_code, float(current_price))

    def check_force_exit_time(self, stock_code: str, now: Optional[datetime] = None) -> bool:
        """다음날 09:30 강제청산 시간 확인."""
        return self.pos_mgr.should_force_exit(stock_code, now)

    def get_realtime_or_polling_price(self, stock_code: str) -> Optional[int]:
        """현재가 조회 (현재는 polling, 추후 WebSocket 교체 가능).

        TODO: WebSocket 실시간 수신으로 교체 시 이 메서드만 수정.
        """
        return self.price_source.get_price(stock_code)

    def run_monitor_loop(
        self,
        max_iterations: int = 1000,
        sleep_sec: float = 5.0,
        now_fn=None,
    ) -> List[Tuple[str, str, int]]:
        """가격 감시 루프 실행.

        max_iterations 또는 보유 포지션이 없으면 종료.

        Args:
            max_iterations: 최대 반복 횟수 (무한 루프 방지)
            sleep_sec: 각 스캔 간격 (초)
            now_fn: 현재 시각 반환 함수 (테스트용)

        Returns:
            청산된 종목 리스트 [(stock_code, reason, price), ...]
        """
        all_signals: List[Tuple[str, str, int]] = []
        for i in range(max_iterations):
            now = now_fn() if now_fn else datetime.now()
            signals = self.monitor_positions(now)
            all_signals.extend(signals)
            if not self.pos_mgr.get_all_positions():
                logger.info("보유 포지션 없음. 감시 루프 종료.")
                break
            time.sleep(sleep_sec)
        return all_signals
