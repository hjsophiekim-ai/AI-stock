"""주문 실행 및 포지션 관련 서비스."""

import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from env_service import inject_to_os_env
from config_service import load_config, get_trade_mode


def get_positions() -> List[Dict]:
    """현재 보유 포지션 반환."""
    inject_to_os_env()
    try:
        from position_manager import PositionManager
        config_path = str(PROJECT_ROOT / "config.yaml")
        pm = PositionManager(config_path)
        positions = pm.get_all_positions()
        result = []
        for code, pos in positions.items():
            result.append({
                "stock_code": code,
                "stock_name": getattr(pos, "stock_name", ""),
                "quantity": getattr(pos, "quantity", 0),
                "entry_price": getattr(pos, "entry_price", 0),
                "target_price": getattr(pos, "target_price", 0),
                "stop_loss_price": getattr(pos, "stop_loss_price", 0),
                "entry_time": str(getattr(pos, "entry_time", "")),
                "order_no": getattr(pos, "order_no", ""),
                "force_trade_mode": getattr(pos, "force_trade_mode", False),
            })
        return result
    except Exception as e:
        return []


def get_positions_with_current_price() -> List[Dict]:
    """현재가 포함한 포지션 반환 (PAPER 모드: 목표가 기준 가상 현재가)."""
    inject_to_os_env()
    positions = get_positions()
    cfg = load_config()
    mode = get_trade_mode(cfg)
    for pos in positions:
        entry = pos.get("entry_price", 0)
        target = pos.get("target_price", entry * 1.02 if entry else 0)
        stop = pos.get("stop_loss_price", entry * 0.97 if entry else 0)
        if mode == "PAPER":
            current_price = entry  # PAPER: 매수가 기준
        else:
            try:
                from api_service import test_current_price
                r = test_current_price(pos["stock_code"])
                current_price = r.get("price", entry)
            except Exception:
                current_price = entry
        pnl = (current_price - entry) * pos.get("quantity", 0)
        pnl_rate = (current_price / entry - 1) * 100 if entry else 0
        pos.update({
            "current_price": current_price,
            "pnl": pnl,
            "pnl_rate": round(pnl_rate, 2),
        })
    return positions


def run_paper_order(stock_code: str, stock_name: str, target_amount: int, current_price: int) -> Dict:
    """PAPER 모드 매수 주문 실행."""
    inject_to_os_env()
    try:
        config_path = str(PROJECT_ROOT / "config.yaml")
        from order_manager import OrderManager
        mgr = OrderManager(config_path)
        result = mgr.buy_stock(
            stock_code=stock_code,
            stock_name=stock_name,
            target_amount=float(target_amount),
            current_price=current_price,
        )
        return result
    except Exception as e:
        return {"success": False, "reason": str(e)}


def run_sell_all(stock_code: str, reason: str = "manual") -> Dict:
    """보유 포지션 전량 매도."""
    inject_to_os_env()
    try:
        config_path = str(PROJECT_ROOT / "config.yaml")
        from order_manager import OrderManager
        mgr = OrderManager(config_path)
        result = mgr.sell_all_position(stock_code, reason=reason)
        return result
    except Exception as e:
        return {"success": False, "reason": str(e)}


def run_budget_allocation(budget: int, date_str: Optional[str] = None) -> Dict:
    """예산 배분 계산."""
    inject_to_os_env()
    try:
        from prediction_service import load_top20, get_today_str
        ds = date_str or get_today_str()
        df = load_top20(ds)
        if df is None or df.empty:
            return {"success": False, "message": "top20 파일 없음", "data": None}
        config_path = str(PROJECT_ROOT / "config.yaml")
        from budget_allocator import BudgetAllocator
        allocator = BudgetAllocator(config_path)
        result = allocator.allocate(
            candidates=df,
            budget=float(budget),
            orderable_cash=float(budget),
        )
        return {"success": True, "data": result, "summary": result.summary()}
    except Exception as e:
        return {"success": False, "message": str(e), "data": None}


def run_force_sell_once() -> Dict:
    """ForceSellMonitor.run_once() 실행."""
    inject_to_os_env()
    try:
        config_path = str(PROJECT_ROOT / "config.yaml")
        from force_sell_monitor import ForceSellMonitor
        monitor = ForceSellMonitor(config_path)
        result = monitor.run_once()
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "message": str(e)}


def run_force_exit() -> Dict:
    """전체 포지션 강제청산 실행."""
    inject_to_os_env()
    try:
        config_path = str(PROJECT_ROOT / "config.yaml")
        from force_sell_monitor import ForceSellMonitor
        monitor = ForceSellMonitor(config_path)
        result = monitor.force_exit_all()
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "message": str(e)}


def check_real_order_conditions() -> Dict:
    """실전 주문 가능 조건 5개 체크."""
    inject_to_os_env()
    try:
        config_path = str(PROJECT_ROOT / "config.yaml")
        from safety_gate import SafetyGate
        gate = SafetyGate(config_path)
        conditions = gate.check_real_test_order_conditions()
        all_ok = all(conditions.values())
        return {"success": all_ok, "conditions": conditions,
                "message": "실전 주문 가능" if all_ok else "조건 미충족"}
    except Exception as e:
        return {"success": False, "conditions": {}, "message": str(e)}
