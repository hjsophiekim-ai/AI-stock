"""force_trade로 매수한 종목 자동 매도 감시 모듈.

data/positions.json에서 보유 종목을 로드하여
+2% 익절, -3% 손절, 다음날 09:30 강제청산 조건을 감시합니다.

사용법:
    python src/force_sell_monitor.py
    python src/force_sell_monitor.py --loop --sleep 10
    python src/force_sell_monitor.py --force-exit
"""

import argparse
import os
import sys
import time
from datetime import datetime
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(__file__))

from emergency_stop import EmergencyStop
from order_manager import OrderManager
from position_manager import PositionManager
from price_monitor import PriceMonitor, RestPriceSource, PaperPriceSource
from safety_gate import SafetyGate, TRADE_MODE_PAPER
from trading_calendar import TradingCalendar, SESSION_CLOSED, SESSION_CLOSING_AUCTION
from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/trade.log")


class ForceSellMonitor:
    """force_trade 매수 종목 자동 매도 감시 클래스."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        self.gate = SafetyGate(config_path)
        self.es = EmergencyStop(config_path)
        self.pos_mgr = PositionManager(config_path)
        self.order_mgr = OrderManager(config_path)
        self.calendar = TradingCalendar(config_path)

        # 가격 소스 설정
        daily_path = self.cfg.get("data", {}).get("raw_daily_path", "data/raw/daily_prices.csv")
        if self.gate.mode == TRADE_MODE_PAPER:
            price_src = PaperPriceSource(daily_path)
        else:
            price_src = RestPriceSource(self.order_mgr._api)
        self.monitor = PriceMonitor(self.pos_mgr, price_src, config_path)
        ensure_dir("reports")

    # ------------------------------------------------------------------
    # 단발 실행
    # ------------------------------------------------------------------

    def run_once(self, now: Optional[datetime] = None) -> List[Tuple]:
        """현재 보유 종목을 한 번 스캔하여 필요한 매도를 실행합니다.

        Returns:
            [(stock_code, reason, price), ...] 청산된 종목 목록
        """
        current = now or datetime.now()
        positions = self.pos_mgr.get_all_positions()
        if not positions:
            logger.info("[ForceSellMonitor] 보유 종목 없음")
            return []

        if self.es.is_active():
            logger.warning("[ForceSellMonitor] 긴급 중단 활성화 — 감시 중단")
            return []

        # 세션 확인 — MOCK/REAL에서 CLOSED이면 매도 불가 경고 (PAPER는 무관)
        if self.gate.mode != TRADE_MODE_PAPER:
            session = self.calendar.get_market_session(current)
            ah_cfg = self.order_mgr.cfg.get("after_hours", {})
            if not self.calendar.is_session_allowed(session, ah_cfg):
                logger.warning(
                    "[ForceSellMonitor] 현재 세션(%s) 매도 불가 — 포지션 감시만 유지",
                    session,
                )
                return []

        logger.info("[ForceSellMonitor] 보유=%d개 모드=%s", len(positions), self.gate.mode)
        signals = self.monitor.monitor_positions(current)
        cleared = []

        for code, reason, price in signals:
            result = self.order_mgr.sell_all_position(code, reason=reason)
            if result.get("success"):
                cleared.append((code, reason, price))
                logger.info(
                    "[매도 완료] %s @ %d원 사유=%s 모드=%s api_called=%s",
                    code, price, reason,
                    result.get("mode", "?"),
                    result.get("api_called", "?"),
                )
            else:
                logger.warning("[매도 실패] %s: %s", code, result.get("reason", ""))
        return cleared

    # ------------------------------------------------------------------
    # 강제청산 전용 실행
    # ------------------------------------------------------------------

    def force_exit_all(self, now: Optional[datetime] = None) -> List[Tuple]:
        """전체 보유 종목 강제청산.

        Returns:
            청산된 종목 목록
        """
        positions = self.pos_mgr.get_all_positions()
        if not positions:
            logger.info("[ForceSellMonitor] 강제청산: 보유 종목 없음")
            return []

        logger.info("[ForceSellMonitor] 강제청산 시작: %d개", len(positions))
        cleared = []
        for code in list(positions.keys()):
            result = self.order_mgr.sell_all_position(code, reason="force_exit_manual")
            if result.get("success"):
                sell_price = result.get("sell_price", 0)
                cleared.append((code, "force_exit_manual", sell_price))
                logger.info("[강제청산] %s @ %d원", code, sell_price)
            else:
                logger.critical("[강제청산 실패] %s: %s — 수동 확인 필요!", code, result.get("reason", ""))
        return cleared

    # ------------------------------------------------------------------
    # 루프 실행
    # ------------------------------------------------------------------

    def run_loop(self, sleep_sec: float = 10.0, max_iter: int = 2000) -> None:
        """지정한 간격으로 반복 감시 (Ctrl+C로 중단).

        Args:
            sleep_sec: 스캔 간격 (초)
            max_iter: 최대 반복 횟수 (무한 루프 방지)
        """
        print(f"[ForceSellMonitor] 루프 시작 — {sleep_sec}초 간격 | Ctrl+C로 중단")
        for i in range(max_iter):
            now = datetime.now()
            print(f"\r[{now.strftime('%H:%M:%S')}] 감시 #{i+1} | 보유={len(self.pos_mgr.get_all_positions())}개", end="", flush=True)

            try:
                cleared = self.run_once(now)
                if cleared:
                    for code, reason, price in cleared:
                        print(f"\n  ✅ 청산: {code} @ {price}원 ({reason})")
            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error("[ForceSellMonitor] 루프 오류: %s", str(e))

            if not self.pos_mgr.get_all_positions():
                print("\n[ForceSellMonitor] 전체 청산 완료. 루프 종료.")
                break

            time.sleep(sleep_sec)


def main() -> None:
    parser = argparse.ArgumentParser(description="force_trade 매수 종목 매도 감시")
    parser.add_argument("--loop", action="store_true", help="반복 감시 모드")
    parser.add_argument("--sleep", type=float, default=10.0, help="루프 간격(초) (기본값: 10)")
    parser.add_argument("--force-exit", action="store_true", help="전량 즉시 강제청산")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    monitor = ForceSellMonitor(args.config)

    try:
        if args.force_exit:
            print("[force-exit] 전량 강제청산 실행")
            cleared = monitor.force_exit_all()
            print(f"강제청산 완료: {len(cleared)}개")
        elif args.loop:
            monitor.run_loop(sleep_sec=args.sleep)
        else:
            cleared = monitor.run_once()
            print(f"매도 신호: {len(cleared)}개")
            for code, reason, price in cleared:
                print(f"  {code} @ {price}원 ({reason})")
    except KeyboardInterrupt:
        print("\n[중단] 사용자에 의해 중단되었습니다.")


if __name__ == "__main__":
    main()
