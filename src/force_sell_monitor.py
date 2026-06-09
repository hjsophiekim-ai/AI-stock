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

    def __init__(self, config_path: str = "config.yaml", runtime_mode: Optional[str] = None) -> None:
        self.cfg = load_config(config_path)
        self.gate = SafetyGate(config_path, runtime_mode=runtime_mode)
        self.es = EmergencyStop(config_path)
        self.pos_mgr = PositionManager(config_path)
        self.order_mgr = OrderManager(config_path, gate=self.gate)
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

    def run_once(self, now: Optional[datetime] = None,
                 strategy_id: Optional[str] = None) -> List[Tuple]:
        """현재 보유 종목을 한 번 스캔하여 필요한 매도를 실행합니다.

        Args:
            now: 기준 시각 (None이면 현재 시각)
            strategy_id: 특정 전략 ID만 감시 (None이면 전체)

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

        # 전략 필터링: strategy_id가 지정되면 해당 전략 포지션만 감시
        if strategy_id:
            positions = {
                code: pos for code, pos in positions.items()
                if getattr(pos, "strategy_id", "") == strategy_id
            }
            if not positions:
                logger.info("[ForceSellMonitor] strategy_id=%s 해당 포지션 없음", strategy_id)
                return []
            logger.info("[ForceSellMonitor] strategy_id=%s 포지션 %d개 감시", strategy_id, len(positions))

        # 세션 확인 — MOCK/REAL에서 CLOSED이면 매도 불가 경고 (PAPER는 무관)
        current_session = None
        if self.gate.mode != TRADE_MODE_PAPER:
            current_session = self.calendar.get_market_session(current)
            ah_cfg = self.order_mgr.cfg.get("after_hours", {})
            if not self.calendar.is_session_allowed(current_session, ah_cfg):
                logger.warning(
                    "[ForceSellMonitor] 현재 세션(%s) 매도 불가 — 포지션 감시만 유지",
                    current_session,
                )
                return []

        logger.info("[ForceSellMonitor] 보유=%d개 모드=%s", len(positions), self.gate.mode)
        signals = self.monitor.monitor_positions(current)
        cleared = []

        for code, reason, price in signals:
            pos = positions.get(code)
            # 전략별 허용 세션 체크 (MOCK/REAL, 세션 정보 있을 때)
            if pos and current_session and self.gate.mode != TRADE_MODE_PAPER:
                allowed = getattr(pos, "allowed_sell_sessions", [])
                if allowed and current_session not in allowed:
                    logger.info(
                        "[ForceSellMonitor] %s 전략=%s 현재세션=%s 허용세션=%s — 매도 건너뜀",
                        code, getattr(pos, "strategy_id", "?"), current_session, allowed,
                    )
                    continue

            result = self.order_mgr.sell_all_position(code, reason=reason)
            if result.get("success"):
                cleared.append((code, reason, price))
                logger.info(
                    "[매도 완료] %s @ %d원 사유=%s 전략=%s 모드=%s api_called=%s",
                    code, price, reason,
                    getattr(pos, "strategy_id", "?") if pos else "?",
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

    def run_loop(self, sleep_sec: float = 10.0, max_iter: int = 2000,
                 strategy_id: Optional[str] = None) -> None:
        """지정한 간격으로 반복 감시 (Ctrl+C로 중단).

        Args:
            sleep_sec: 스캔 간격 (초)
            max_iter: 최대 반복 횟수 (무한 루프 방지)
            strategy_id: 특정 전략만 감시 (None이면 전체)
        """
        label = f" [전략: {strategy_id}]" if strategy_id else " [전체]"
        print(f"[ForceSellMonitor] 루프 시작 — {sleep_sec}초 간격{label} | Ctrl+C로 중단")
        for i in range(max_iter):
            now = datetime.now()
            print(f"\r[{now.strftime('%H:%M:%S')}] 감시 #{i+1}{label} | 보유={len(self.pos_mgr.get_all_positions())}개", end="", flush=True)

            try:
                cleared = self.run_once(now, strategy_id=strategy_id)
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
    parser.add_argument(
        "--strategy",
        choices=["morning_0930", "afternoon_1500"],
        default=None,
        help="특정 전략 포지션만 감시 (기본값: 전체 감시)",
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--sell-policy", default=None, choices=["fixed_2pct", "market_strength_trailing", "manual_hold"])
    parser.add_argument("--policy-override", action="store_true")
    args = parser.parse_args()

    if args.sell_policy:
        from monitor_take_profit import monitor_once
        import json
        result = monitor_once(
            mode="mock",
            strategy=args.strategy,
            config_path=args.config,
            sell_now=True,
            sell_policy_id=args.sell_policy,
            policy_override=args.policy_override,
        )
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2))
        return

    monitor = ForceSellMonitor(args.config)
    strategy_label = f" [전략: {args.strategy}]" if args.strategy else " [전체 전략]"

    try:
        if args.force_exit:
            print(f"[force-exit] 전량 강제청산 실행{strategy_label}")
            cleared = monitor.force_exit_all()
            print(f"강제청산 완료: {len(cleared)}개")
        elif args.loop:
            print(f"[루프 감시] {args.sleep}초 간격{strategy_label}")
            for i in range(2000):
                now = datetime.now()
                print(f"\r[{now.strftime('%H:%M:%S')}] 감시 #{i+1}{strategy_label}", end="", flush=True)
                try:
                    cleared = monitor.run_once(now, strategy_id=args.strategy)
                    if cleared:
                        for code, reason, price in cleared:
                            print(f"\n  ✅ 청산: {code} @ {price}원 ({reason})")
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    logger.error("[ForceSellMonitor] 루프 오류: %s", str(e))
                if not monitor.pos_mgr.get_all_positions():
                    print(f"\n[ForceSellMonitor] 전체 청산 완료. 루프 종료.")
                    break
                time.sleep(args.sleep)
        else:
            cleared = monitor.run_once(strategy_id=args.strategy)
            print(f"매도 신호{strategy_label}: {len(cleared)}개")
            for code, reason, price in cleared:
                print(f"  {code} @ {price}원 ({reason})")
    except KeyboardInterrupt:
        print("\n[중단] 사용자에 의해 중단되었습니다.")


if __name__ == "__main__":
    main()
