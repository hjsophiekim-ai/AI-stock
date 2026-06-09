"""Trading service wrappers used by the Streamlit pages."""

import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from env_service import inject_to_os_env
from config_service import load_config, get_trade_mode


def get_positions() -> List[Dict]:
    inject_to_os_env()
    try:
        from position_manager import PositionManager
        pm = PositionManager(str(PROJECT_ROOT / "config.yaml"))
        rows = []
        for code, pos in pm.get_all_positions().items():
            rows.append({
                "stock_code": code,
                "stock_name": getattr(pos, "stock_name", ""),
                "quantity": getattr(pos, "quantity", 0),
                "entry_price": getattr(pos, "entry_price", 0),
                "avg_price": getattr(pos, "avg_price", getattr(pos, "entry_price", 0)),
                "target_price": getattr(pos, "target_price", 0),
                "stop_loss_price": getattr(pos, "stop_loss_price", getattr(pos, "stop_price", 0)),
                "entry_time": str(getattr(pos, "entry_time", "")),
                "order_no": getattr(pos, "order_no", ""),
                "strategy_id": getattr(pos, "strategy_id", ""),
                "strategy_name": getattr(pos, "strategy_name", ""),
                "allowed_sell_sessions": getattr(pos, "allowed_sell_sessions", []),
                "status": getattr(pos, "status", "OPEN"),
                "source": getattr(pos, "source", "local"),
                "force_trade_mode": getattr(pos, "force_trade_mode", False),
            })
        return rows
    except Exception:
        return []


def get_positions_with_current_price() -> List[Dict]:
    inject_to_os_env()
    positions = get_positions()
    cfg = load_config()
    mode = get_trade_mode(cfg)
    for pos in positions:
        entry = float(pos.get("entry_price", 0) or 0)
        current_price = entry
        if mode != "PAPER":
            try:
                from api_service import test_current_price
                current_price = float(test_current_price(pos["stock_code"]).get("price", entry) or entry)
            except Exception:
                current_price = float(pos.get("current_price", entry) or entry)
        qty = int(pos.get("quantity", 0) or 0)
        pnl = (current_price - entry) * qty
        pnl_rate = (current_price / entry - 1) * 100 if entry else 0
        pos.update({
            "current_price": current_price,
            "target_reached": current_price >= float(pos.get("target_price", entry * 1.02) or 0),
            "pnl": pnl,
            "pnl_rate": round(pnl_rate, 2),
        })
    return positions


def get_broker_positions() -> List[Dict]:
    inject_to_os_env()
    try:
        from api_service import get_broker_positions as _api_get
        return _api_get().get("positions", [])
    except Exception:
        return []


def sync_broker_to_local(mode: str = "mock", strategy_id: str = "morning_0930") -> Dict:
    inject_to_os_env()
    try:
        from sync_broker_positions import sync_broker_positions
        result = sync_broker_positions(mode=mode, strategy=strategy_id, config_path=str(PROJECT_ROOT / "config.yaml"))
        return {"success": True, "message": f"{result.get('broker_count', 0)} positions synced", "synced": result.get("broker_count", 0), **result}
    except Exception as e:
        return {"success": False, "message": str(e), "synced": 0}


def run_paper_order(stock_code: str, stock_name: str, target_amount: int, current_price: int) -> Dict:
    inject_to_os_env()
    try:
        from order_manager import OrderManager
        mgr = OrderManager(str(PROJECT_ROOT / "config.yaml"), runtime_mode="paper")
        return mgr.buy_stock(stock_code=stock_code, stock_name=stock_name, target_amount=float(target_amount), current_price=current_price)
    except Exception as e:
        return {"success": False, "reason": str(e)}


def run_sell_all(stock_code: str, reason: str = "manual", mode: str = "mock") -> Dict:
    inject_to_os_env()
    try:
        from order_manager import OrderManager
        mgr = OrderManager(str(PROJECT_ROOT / "config.yaml"), runtime_mode=mode)
        return mgr.sell_all_position(stock_code, reason=reason)
    except Exception as e:
        return {"success": False, "reason": str(e)}


def run_budget_allocation(budget: int, date_str: Optional[str] = None) -> Dict:
    inject_to_os_env()
    try:
        from prediction_service import load_top20, get_today_str
        from budget_allocator import BudgetAllocator
        ds = date_str or get_today_str()
        df = load_top20(ds)
        if df is None or df.empty:
            return {"success": False, "message": "top20 file not found", "data": None}
        allocator = BudgetAllocator(str(PROJECT_ROOT / "config.yaml"))
        result = allocator.allocate_until_budget(candidates=df, budget=int(budget), orderable_cash=int(budget))
        summary = {
            "allocated_count": len(result.allocations),
            "total_order_amount": result.total_order_amount,
            "remaining_budget": result.remaining_budget,
            "input_budget": budget,
            "effective_budget": result.effective_budget,
        }
        return {"success": True, "data": result, "summary": summary}
    except Exception as e:
        return {"success": False, "message": str(e), "data": None}


def run_buy_candidates(
    candidate_file: str,
    budget: int,
    mode: str,
    strategy_id: str,
    max_orders: int = 100,
    selected_codes: Optional[List] = None,
    refresh_prices: bool = False,
    preview_only: bool = False,
    allow_additional_buy: bool = False,
) -> Dict:
    inject_to_os_env()
    try:
        from buy_candidate_list import buy_candidates
        result = buy_candidates(
            candidate_file=candidate_file,
            budget=budget,
            mode=(mode or "paper").lower(),
            strategy_id=strategy_id,
            max_orders=max_orders,
            selected_codes=selected_codes,
            refresh_prices=refresh_prices,
            preview_only=preview_only,
            allow_outside_window=True,
            allow_additional_buy=allow_additional_buy,
        )
        return result if isinstance(result, dict) else {"success": False, "message": "buy_candidates returned non-dict", "orders_placed": 0}
    except Exception as e:
        return {"success": False, "message": str(e), "orders_placed": 0}


def run_force_sell_once(strategy_id: Optional[str] = None, mode: str = "mock") -> Dict:
    inject_to_os_env()
    try:
        from monitor_take_profit import monitor_once
        result = monitor_once(mode=mode, strategy=strategy_id, config_path=str(PROJECT_ROOT / "config.yaml"))
        return {"success": True, "data": result.get("rows", []), **result}
    except Exception as e:
        return {"success": False, "message": str(e)}


def run_force_exit(mode: str = "mock") -> Dict:
    inject_to_os_env()
    try:
        from force_sell_monitor import ForceSellMonitor
        monitor = ForceSellMonitor(str(PROJECT_ROOT / "config.yaml"), runtime_mode=mode)
        return {"success": True, "data": monitor.force_exit_all()}
    except Exception as e:
        return {"success": False, "message": str(e)}


def check_real_order_conditions() -> Dict:
    inject_to_os_env()
    try:
        from safety_gate import SafetyGate
        gate = SafetyGate(str(PROJECT_ROOT / "config.yaml"))
        conditions = gate.check_real_test_order_conditions()
        return {"success": all(conditions.values()), "conditions": conditions}
    except Exception as e:
        return {"success": False, "conditions": {}, "message": str(e)}
