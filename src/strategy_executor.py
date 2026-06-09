"""전략 실행 모듈.

전략 선택 → 매수 가능 시간 확인 → 매수 실행 → 매도 감시를 통합합니다.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))

from utils import load_config, setup_logger
from strategy_config import get_strategy, validate_strategy_id
from buy_candidate_list import buy_candidates

logger = setup_logger(__name__, "logs/strategy_executor.log")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")


def _get_current_session() -> str:
    """현재 시간대 세션 반환. 실패 시 'UNKNOWN'."""
    try:
        from trading_calendar import TradingCalendar
        cal = TradingCalendar(CONFIG_PATH)
        return cal.get_market_session(datetime.now())
    except Exception:
        return "UNKNOWN"


def _is_within_buy_window(strategy_cfg: dict, now: Optional[datetime] = None) -> bool:
    """현재 시간이 전략의 매수 시간 범위 내에 있는지 확인."""
    now = now or datetime.now()
    try:
        start_str = strategy_cfg.get("buy_window_start", "00:00")
        end_str = strategy_cfg.get("buy_window_end", "23:59")
        h, m = map(int, start_str.split(":"))
        from datetime import time as dtime
        start_t = dtime(h, m)
        h2, m2 = map(int, end_str.split(":"))
        end_t = dtime(h2, m2)
        current_t = now.time()
        return start_t <= current_t <= end_t
    except Exception:
        return True


def execute_buy_strategy(
    strategy_id: str,
    candidate_file: str,
    budget: int,
    mode: str = "mock",
    max_orders: int = 100,
    selected_codes: Optional[List[str]] = None,
    allow_outside_window: bool = True,
    refresh_prices: bool = False,
    preview_only: bool = False,
    sell_policy_id: str = "fixed_2pct",
) -> dict:
    """전략 매수 실행.

    Args:
        strategy_id: "morning_0930" | "afternoon_1500"
        candidate_file: top100/top50/top20 CSV 파일 경로
        budget: 총 예산 (원)
        mode: "paper" | "mock" | "real"
        max_orders: 최대 주문 종목 수
        selected_codes: 선택 종목 목록 (None이면 전체)
        allow_outside_window: 매수 시간 외에도 MOCK/PAPER에서 허용
        refresh_prices: 현재가 갱신 여부
        preview_only: 미리보기만

    Returns:
        dict (항상, 절대 None 없음)
    """
    if not validate_strategy_id(strategy_id):
        return {
            "success": False, "message": f"알 수 없는 전략 ID: {strategy_id}",
            "errors": [f"유효하지 않은 strategy_id: {strategy_id}"],
            "orders_placed": 0,
        }

    strategy_cfg = get_strategy(strategy_id)
    now = datetime.now()
    in_window = _is_within_buy_window(strategy_cfg, now)
    current_session = _get_current_session()

    warnings = []
    if not in_window:
        window_desc = f"{strategy_cfg['buy_window_start']}~{strategy_cfg['buy_window_end']}"
        if mode == "real":
            return {
                "success": False,
                "message": f"REAL 모드: 현재 시간이 매수 시간({window_desc}) 밖입니다.",
                "errors": [f"현재 시간 {now.strftime('%H:%M')} — 매수 시간 {window_desc}"],
                "orders_placed": 0,
            }
        elif allow_outside_window:
            warnings.append(
                f"현재 시간({now.strftime('%H:%M')})이 전략 매수 시간({window_desc}) 밖이지만 "
                f"{mode.upper()} 모드에서 강제 실행합니다."
            )
        else:
            return {
                "success": False,
                "message": f"현재 시간이 매수 시간({window_desc}) 밖입니다.",
                "errors": [f"현재 시간 {now.strftime('%H:%M')}"],
                "orders_placed": 0,
            }

    result = buy_candidates(
        candidate_file=candidate_file, budget=budget, mode=mode,
        strategy_id=strategy_id, max_orders=max_orders,
        selected_codes=selected_codes, refresh_prices=refresh_prices,
        preview_only=preview_only,
        sell_policy_id=sell_policy_id,
    )

    if warnings:
        result["warnings"] = result.get("warnings", []) + warnings

    logger.info(
        "[전략실행] %s | 모드=%s | 주문=%d건 | 세션=%s",
        strategy_id, mode, result.get("orders_placed", 0), current_session,
    )
    return result


def monitor_sell_strategy(
    strategy_id: Optional[str] = None,
    mode: str = "mock",
) -> dict:
    """전략별 매도 감시 1회 실행.

    Args:
        strategy_id: 특정 전략만 감시 (None이면 전체)
        mode: "paper" | "mock" | "real"

    Returns:
        dict
    """
    try:
        from force_sell_monitor import ForceSellMonitor
        monitor = ForceSellMonitor(CONFIG_PATH)
        cleared = monitor.run_once()

        result_list = []
        for code, reason, price in cleared:
            result_list.append({"stock_code": code, "reason": reason, "price": price})

        return {
            "success": True,
            "message": f"감시 완료: {len(cleared)}건 청산",
            "cleared": result_list,
            "strategy_id": strategy_id,
        }
    except Exception as ex:
        return {
            "success": False,
            "message": f"매도 감시 오류: {ex}",
            "cleared": [],
            "strategy_id": strategy_id,
        }


def get_strategy_status() -> dict:
    """현재 포지션의 전략별 상태 반환."""
    try:
        from position_manager import PositionManager
        pm = PositionManager(CONFIG_PATH)
        positions = pm.get_all_positions()

        by_strategy = {}
        for code, pos in positions.items():
            sid = getattr(pos, "strategy_id", "") or "unknown"
            if sid not in by_strategy:
                by_strategy[sid] = []
            by_strategy[sid].append({
                "stock_code": code,
                "stock_name": getattr(pos, "stock_name", ""),
                "quantity": getattr(pos, "quantity", 0),
                "entry_price": getattr(pos, "entry_price", 0),
                "target_price": getattr(pos, "target_price", 0),
                "stop_price": getattr(pos, "stop_price", 0),
                "strategy_name": getattr(pos, "strategy_name", sid),
            })

        return {"success": True, "by_strategy": by_strategy, "total_positions": len(positions)}
    except Exception as ex:
        return {"success": False, "message": str(ex), "by_strategy": {}, "total_positions": 0}
