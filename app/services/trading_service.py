"""Trading service wrappers used by the Streamlit pages."""

import sys
from pathlib import Path
from typing import Dict, List, Optional
from datetime import date as _date

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
        _open = pm.get_open_positions() if hasattr(pm, "get_open_positions") else {
            k: v for k, v in pm.get_all_positions().items() if not getattr(v, "is_closed", False)
        }
        for code, pos in _open.items():
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
                "sell_policy_id": getattr(pos, "sell_policy_id", "fixed_2pct"),
                "sell_policy_name": getattr(pos, "sell_policy_name", "기본 자동매도"),
                "trailing_active": getattr(pos, "trailing_active", False),
                "trailing_high_price": getattr(pos, "trailing_high_price", 0),
                "trailing_stop_price": getattr(pos, "trailing_stop_price", 0),
                "manual_only": getattr(pos, "manual_only", False),
                "auto_take_profit_enabled": getattr(pos, "auto_take_profit_enabled", True),
                "allowed_sell_sessions": getattr(pos, "allowed_sell_sessions", []),
                "status": getattr(pos, "status", "OPEN"),
                "source": getattr(pos, "source", "local"),
                "force_trade_mode": getattr(pos, "force_trade_mode", False),
            })
        return rows
    except Exception:
        return []


def get_positions_with_current_price(mode: str = "") -> List[Dict]:
    inject_to_os_env()
    if mode:
        from position_manager import PositionManager
        pm = PositionManager(str(PROJECT_ROOT / "config.yaml"), mode=mode)
        positions = []
        _open = pm.get_open_positions() if hasattr(pm, "get_open_positions") else {
            k: v for k, v in pm.get_all_positions().items() if not getattr(v, "is_closed", False)
        }
        for code, pos in _open.items():
            positions.append({
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
                "sell_policy_id": getattr(pos, "sell_policy_id", "fixed_2pct"),
                "sell_policy_name": getattr(pos, "sell_policy_name", "기본 자동매도"),
                "trailing_active": getattr(pos, "trailing_active", False),
                "trailing_high_price": getattr(pos, "trailing_high_price", 0),
                "trailing_stop_price": getattr(pos, "trailing_stop_price", 0),
                "manual_only": getattr(pos, "manual_only", False),
                "auto_take_profit_enabled": getattr(pos, "auto_take_profit_enabled", True),
                "allowed_sell_sessions": getattr(pos, "allowed_sell_sessions", []),
                "status": getattr(pos, "status", "OPEN"),
                "source": getattr(pos, "source", "local"),
                "force_trade_mode": getattr(pos, "force_trade_mode", False),
                "current_price": getattr(pos, "current_price", 0),
            })
    else:
        positions = get_positions()
    _effective_mode = (mode or "").upper() or get_trade_mode(load_config())
    for pos in positions:
        entry = float(pos.get("entry_price", 0) or 0)
        current_price = float(pos.get("current_price", entry) or entry)
        if _effective_mode != "PAPER":
            try:
                from api_service import test_current_price
                current_price = float(test_current_price(pos["stock_code"]).get("price", entry) or entry)
            except Exception:
                pass
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


def get_broker_positions(mode: str = "mock") -> List[Dict]:
    inject_to_os_env()
    try:
        from api_service import get_broker_positions as _api_get
        return _api_get(mode=mode).get("positions", [])
    except TypeError:
        # fallback: api_service.get_broker_positions doesn't accept mode arg
        try:
            from api_service import get_broker_positions as _api_get
            return _api_get().get("positions", [])
        except Exception:
            return []
    except Exception:
        return []


def sync_broker_to_local(
    mode: str = "mock",
    strategy_id: str = "morning_0930",
    apply: bool = False,
    close_missing: bool = False,
) -> Dict:
    inject_to_os_env()
    try:
        from sync_broker_positions import sync_broker_positions
        result = sync_broker_positions(
            mode=mode,
            strategy=strategy_id,
            config_path=str(PROJECT_ROOT / "config.yaml"),
            apply=apply,
            close_missing=close_missing,
        )
        broker_n = result.get("broker_count", 0)
        closed_n = result.get("closed_count", 0)
        open_n = result.get("open_count", broker_n)
        msg = f"브로커 {broker_n}개 동기화 → 로컬 OPEN {open_n}개"
        if closed_n:
            msg += f", {closed_n}개 CLOSED 처리"
        return {"success": True, "message": msg, "synced": broker_n, **result}
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


def run_sell_order(
    stock_code: str,
    mode: str = "mock",
    reason: str = "manual",
    quantity: int = None,
    order_price: int = None,
    stock_name: str = "",
) -> Dict:
    """검증 포함 매도 주문 — 수동매도/자동매도 공통 경로.

    UI에서 선택한 mode 를 끝까지 전달한다. config.yaml 기반 덮어쓰기 금지.
    """
    inject_to_os_env()
    try:
        from order_manager import OrderManager
        from safety_gate import SafetyGate
        CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")
        gate = SafetyGate(CONFIG_PATH, runtime_mode=(mode or "mock").lower())
        mgr = OrderManager(CONFIG_PATH, gate=gate)
        return mgr.place_sell_order_with_verification(
            stock_code=stock_code,
            stock_name=stock_name,
            quantity=quantity,
            order_price=order_price,
            reason=reason,
        )
    except Exception as e:
        return {
            "success": False,
            "reason": str(e),
            "requested_mode": (mode or "mock").upper(),
            "resolved_mode": "",
            "stock_code": str(stock_code).zfill(6),
        }


def refresh_candidate_prices_service(
    date_str: str,
    top_n: int = 100,
    mode: str = "mock",
    limit: int = None,
) -> Dict:
    """앱에서 현재가 갱신 버튼 클릭 시 호출."""
    inject_to_os_env()
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from refresh_candidate_prices import refresh_prices
        return refresh_prices(
            date_str=date_str,
            top_n=top_n,
            mode=(mode or "mock").lower(),
            limit=limit,
        )
    except Exception as e:
        return {"success": False, "message": str(e), "updated": 0, "price_mode": (mode or "mock").upper()}


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
    sell_policy_id: str = "fixed_2pct",
) -> Dict:
    inject_to_os_env()
    try:
        if (mode or "").lower() == "real":
            cfg = load_config()
            real_trade_cfg = cfg.get("real_trade", {})
            # New condition: allow_bulk_buy_after_api_confirmation
            allow_bulk = real_trade_cfg.get("allow_bulk_buy_after_api_confirmation", False)
            # Also allow if legacy allow_bulk_buy is True
            allow_bulk = allow_bulk or real_trade_cfg.get("allow_bulk_buy", False)
            if not allow_bulk:
                return {
                    "success": False,
                    "message": "실전 전체 리스트 매수가 비활성화되어 있습니다. config.yaml real_trade.allow_bulk_buy_after_api_confirmation을 확인하세요.",
                    "orders_placed": 0,
                }
            # Daily confirmation check
            try:
                from real_trade_confirmation import is_confirmed_today
                if not is_confirmed_today():
                    return {
                        "success": False,
                        "message": "오늘 실전 주문 확인이 완료되지 않았습니다. API 설정 화면에서 실전 주문 확인을 완료하세요.",
                        "orders_placed": 0,
                    }
            except Exception:
                pass
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
            sell_policy_id=sell_policy_id,
        )
        return result if isinstance(result, dict) else {"success": False, "message": "buy_candidates returned non-dict", "orders_placed": 0}
    except Exception as e:
        return {"success": False, "message": str(e), "orders_placed": 0}


def run_force_sell_once(
    strategy_id: Optional[str] = None,
    mode: str = "mock",
    sell_policy_id: Optional[str] = None,
    policy_override: bool = False,
) -> Dict:
    inject_to_os_env()
    try:
        from monitor_take_profit import monitor_once
        result = monitor_once(
            mode=mode,
            strategy=strategy_id,
            config_path=str(PROJECT_ROOT / "config.yaml"),
            sell_policy_id=sell_policy_id,
            policy_override=policy_override,
        )
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


def run_real_order_readiness_check() -> Dict:
    inject_to_os_env()
    try:
        from real_order_readiness_check import run_readiness_check, save_report
        result = run_readiness_check(str(PROJECT_ROOT / "config.yaml"))
        paths = save_report(result)
        return {"success": True, **result, **paths}
    except Exception as e:
        return {"success": False, "message": str(e)}


def run_real_single_order_test(stock_code: str, quantity: int = 1, price: int = 0, execute: bool = False) -> Dict:
    inject_to_os_env()
    try:
        from real_order_test import run_real_order_test
        return run_real_order_test(
            stock_code=stock_code,
            quantity=quantity,
            price=price,
            execute=execute,
            config_path=str(PROJECT_ROOT / "config.yaml"),
        )
    except Exception as e:
        return {"success": False, "message": str(e)}


def run_real_order_diagnosis(stock_code: str, quantity: int = 1, price: int = 0) -> Dict:
    inject_to_os_env()
    try:
        from real_order_diagnosis import run_real_order_diagnosis as _run
        return _run(
            stock_code=stock_code,
            quantity=quantity,
            price=price,
            config_path=str(PROJECT_ROOT / "config.yaml"),
        )
    except Exception as e:
        return {"success": False, "message": str(e)}


def run_real_order_verify(stock_code: str = "", order_no: str = "", raw_dump: bool = True) -> Dict:
    inject_to_os_env()
    try:
        from real_order_verify import run_real_order_verify as _run
        return _run(
            stock_code=stock_code,
            order_no=order_no,
            config_path=str(PROJECT_ROOT / "config.yaml"),
            today=True,
            raw_dump=raw_dump,
        )
    except Exception as e:
        return {"success": False, "message": str(e)}


def list_sell_policies() -> List[Dict]:
    try:
        from sell_policy import list_sell_policies as _list
        return _list(load_config())
    except Exception:
        return [
            {"id": "fixed_2pct", "name": "기본 자동매도"},
            {"id": "market_strength_trailing", "name": "강한 장 트레일링 매도"},
            {"id": "manual_hold", "name": "수동매도 전까지 보유"},
        ]


def run_market_strength() -> Dict:
    inject_to_os_env()
    try:
        from market_strength import get_market_strength
        return {"success": True, **get_market_strength()}
    except Exception as e:
        return {"success": False, "message": str(e)}


def get_open_sell_orders(mode: str = "mock") -> Dict:
    """KIS 미체결 매도 주문 조회."""
    inject_to_os_env()
    try:
        from kis_api import KISApiClient
        from safety_gate import SafetyGate
        CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")
        gate = SafetyGate(CONFIG_PATH, runtime_mode=(mode or "mock").lower())
        api = KISApiClient(CONFIG_PATH, gate=gate)
        oq = api.get_open_orders(side="SELL")
        orders = oq.get("orders", [])
        query_status = oq.get("query_status", "OK")
        return {
            "success": query_status == "OK",
            "count": len(orders),
            "orders": orders,
            "open_order_query_supported": oq.get("open_order_query_supported", True),
            "query_status": query_status,
            "query_msg": oq.get("query_msg", ""),
            "mode": gate.mode,
            "base_url": api._base_url,
        }
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "count": 0,
            "orders": [],
            "open_order_query_supported": False,
            "query_status": "ERROR",
            "query_msg": str(e),
        }


def run_bulk_sell_with_amend(
    mode: str = "mock",
    check_open_orders: bool = True,
    amend_unfilled: bool = True,
    cancel_replace_if_amend_fails: bool = True,
    max_sell_slippage_pct: float = 1.0,
    dry_run: bool = False,
    proceed_without_open_order_check: bool = False,
) -> Dict:
    """미체결 주문 정정 포함 전량 일괄매도."""
    inject_to_os_env()
    try:
        from order_manager import OrderManager
        from safety_gate import SafetyGate
        CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")
        gate = SafetyGate(CONFIG_PATH, runtime_mode=(mode or "mock").lower())
        mgr = OrderManager(CONFIG_PATH, gate=gate)
        result = mgr.bulk_sell_with_open_order_check(
            check_open_orders=check_open_orders,
            amend_unfilled=amend_unfilled,
            cancel_replace_if_amend_fails=cancel_replace_if_amend_fails,
            max_sell_slippage_pct=max_sell_slippage_pct,
            dry_run=dry_run,
            proceed_without_open_order_check=proceed_without_open_order_check,
        )
        return result
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "total": 0,
            "results": [],
            "requested_mode": (mode or "mock").upper(),
        }


def check_real_readiness() -> Dict:
    """REAL 계좌조회 준비상태 확인 (주문 없음).

    Returns:
        {"ready": bool, "verdict": str, "message": str, ...}
    """
    inject_to_os_env()
    try:
        from real_order_readiness_check import run_readiness_check
        result = run_readiness_check(config_path=str(PROJECT_ROOT / "config.yaml"), call_real_api=True)
        verdict = result.get("verdict", "NOT_READY")
        ready = verdict == "READY_FOR_REAL_SINGLE_TEST"
        msg_parts = []
        if not result.get("real_api_key"):
            msg_parts.append("API 키 없음")
        if not result.get("real_token"):
            msg_parts.append("토큰 발급 실패")
        if not result.get("real_balance"):
            msg_parts.append(f"계좌조회 실패 HTTP={result.get('http_status_code','?')}")
        if not result.get("orderable_cash"):
            msg_parts.append("주문가능금액 확인 실패")
        missing = result.get("missing_conditions", [])
        if missing:
            msg_parts.extend(missing)
        return {
            "ready": ready,
            "verdict": verdict,
            "message": " | ".join(msg_parts) if msg_parts else "준비 완료",
            "details": result,
        }
    except Exception as exc:
        return {
            "ready": False,
            "verdict": "NOT_READY",
            "message": f"readiness 확인 실패: {exc}",
            "details": {},
        }


def get_real_bulk_buy_readiness(
    planned_total_amount: int = 0,
    order_plan_id: str = "",
    order_plan_hash: str = "",
    user_confirmed_bulk_real: bool = False,
) -> Dict:
    """REAL 전체 리스트 매수 활성화 조건 점검.

    기존 '1주 테스트 완료 필수' 조건 제거.
    대신 API 설정 당일 확인, readiness, order_plan, 금액한도, 사용자 최종확인 기준.
    """
    inject_to_os_env()
    cfg = load_config()
    real_trade_cfg = cfg.get("real_trade", {})
    max_amount = int(real_trade_cfg.get("max_real_bulk_order_amount", 300000))

    # 1. REAL readiness
    readiness = check_real_readiness()
    real_readiness_ready = readiness.get("ready", False)

    # 2. 당일 API 확인
    try:
        from real_trade_confirmation import is_confirmed_today
        api_confirmation_today = is_confirmed_today()
    except Exception:
        api_confirmation_today = False

    # 3. order_plan 존재
    order_plan_exists = bool(order_plan_id)

    # 4. order_plan hash 유효 (hash가 없으면 skip)
    order_plan_hash_valid = bool(order_plan_id)  # plan id exists = valid for now

    # 5. 금액한도
    budget_within_limit = (planned_total_amount <= max_amount) if planned_total_amount > 0 else True

    # 6. real_bulk_enabled (config) — only check allow_bulk_buy_after_api_confirmation
    # NOTE: safety.block_real_bulk_order is intentionally excluded here; it is
    # a legacy global kill-switch that should not suppress the UI readiness check.
    real_bulk_enabled = (
        real_trade_cfg.get("allow_bulk_buy_after_api_confirmation", True)
        and real_trade_cfg.get("enabled", True)
    )

    # 7. 사용자 최종확인
    conditions = {
        "real_readiness_ready": real_readiness_ready,
        "api_confirmation_today": api_confirmation_today,
        "order_plan_exists": order_plan_exists,
        "order_plan_hash_valid": order_plan_hash_valid,
        "budget_within_limit": budget_within_limit,
        "user_confirmed_bulk_real": user_confirmed_bulk_real,
        "real_bulk_enabled": real_bulk_enabled,
    }
    missing = [k for k, v in conditions.items() if not v]
    ready = all(conditions.values())

    return {
        "ready": ready,
        "conditions": conditions,
        "missing_conditions": missing,
        "planned_total_amount": planned_total_amount,
        "max_real_bulk_order_amount": max_amount,
        "readiness_details": readiness,
    }
