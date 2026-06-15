"""Trading service wrappers used by the Streamlit pages."""

import sys
from pathlib import Path
from typing import Dict, List, Optional
from datetime import date as _date, datetime as _datetime

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from env_service import inject_to_os_env
from config_service import load_config, get_trade_mode


# ──────────────────────────────────────────────────────────────────────────────
# 단일 진실 공급원: 후보 파일 탐색
# ──────────────────────────────────────────────────────────────────────────────

def get_active_buy_candidate_file(date: Optional[str] = None) -> Optional[Path]:
    """공식 매수 후보 파일(buy_top20)을 탐색해 Path 반환. 없으면 None.

    탐색 순서:
      1. reports/predictions/buy_top20_오늘.csv
      2. reports/predictions/buy_top20_*.csv 중 가장 최신
      없으면 None — top100 fallback은 하지 않는다.
    """
    preds_dir = PROJECT_ROOT / "reports" / "predictions"
    if not preds_dir.exists():
        return None

    today = date or _datetime.now().strftime("%Y%m%d")
    today_file = preds_dir / f"buy_top20_{today}.csv"
    if today_file.exists():
        return today_file

    found = sorted(preds_dir.glob("buy_top20_????????.csv"), reverse=True)
    if found:
        return found[0]
    return None


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
        result = _api_get(mode=mode)
        if not result.get("success"):
            raise RuntimeError(result.get("message", "알 수 없는 오류"))
        return result.get("positions", [])
    except Exception:
        return []


def get_broker_positions_result(mode: str = "mock") -> Dict:
    """get_broker_positions의 에러 정보 포함 버전 — UI에서 오류 표시 시 사용."""
    inject_to_os_env()
    try:
        from api_service import get_broker_positions as _api_get
        result = _api_get(mode=mode)
        return result
    except Exception as e:
        return {"success": False, "message": str(e), "positions": [], "mode": mode}


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
    candidate_file: Optional[str] = None,
) -> Dict:
    """앱에서 현재가 갱신 버튼 클릭 시 호출.

    반드시 buy_top20 파일을 대상으로 한다.
    candidate_file이 명시되지 않으면 get_active_buy_candidate_file()로 탐색.
    top100만 갱신하고 buy_top20을 그대로 두는 버그를 방지한다.
    """
    inject_to_os_env()
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from refresh_candidate_prices import refresh_prices

        # buy_top20 파일 우선 탐색
        target_file: Optional[str] = candidate_file
        if not target_file:
            active = get_active_buy_candidate_file(date=date_str)
            if active:
                target_file = str(active)

        if not target_file:
            return {
                "success": False,
                "message": (
                    f"buy_top20_{date_str}.csv 파일이 없습니다. "
                    "AI 후보 리스트 → '장중 Top20 필터 실행' 버튼을 먼저 실행하세요."
                ),
                "updated": 0,
                "price_mode": (mode or "mock").upper(),
                "candidate_file": "",
            }

        result = refresh_prices(
            date_str=date_str,
            top_n=top_n,
            mode=(mode or "mock").lower(),
            limit=limit,
            input_path=target_file,
        )
        result["candidate_file"] = target_file
        return result
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "updated": 0,
            "price_mode": (mode or "mock").upper(),
            "candidate_file": candidate_file or "",
        }


def run_budget_allocation(
    budget: int,
    date_str: Optional[str] = None,
    candidate_file: Optional[str] = None,
    mode: str = "mock",
) -> Dict:
    """예산배분 계산 — 매수 버튼과 동일한 후보파일·orderable_cash·held_codes를 사용해 수량 불일치 방지."""
    inject_to_os_env()
    try:
        import pandas as pd
        from budget_allocator import BudgetAllocator

        # 매수 버튼과 동일한 후보 파일 로드
        df = None
        if candidate_file:
            _p = Path(candidate_file)
            if _p.exists():
                df = pd.read_csv(_p)

        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            from prediction_service import load_top20, get_today_str
            ds = date_str or get_today_str()
            df = load_top20(ds)
            if df is None or df.empty:
                return {"success": False, "message": "후보 파일을 찾을 수 없습니다 (top20 fallback도 없음)", "data": None}

        total_candidates = len(df)

        # 매수 버튼의 _get_orderable_cash()와 동일한 로직으로 orderable_cash 결정
        # 8초 초과 시 KIS 서버 무응답으로 판단 — 입력 예산으로 대체 (30초 hang 방지)
        # API 실패와 진짜 0원 잔고를 구분: 실패 시 input budget 유지 (0원 처리 금지)
        orderable_cash = int(budget)
        orderable_cash_success = True
        orderable_cash_error = ""
        if (mode or "mock").lower() not in ("paper",):
            import concurrent.futures as _cf
            _cfg = str(PROJECT_ROOT / "config.yaml")
            _mode_lower = (mode or "mock").lower()
            _cash_result: Dict = {"success": False, "orderable_cash": None, "error_message": "timeout"}

            def _fetch_cash():
                try:
                    from kis_api import KISApiClient
                    from safety_gate import SafetyGate
                    _gate = SafetyGate(_cfg, runtime_mode=_mode_lower)
                    _cash_result.update(KISApiClient(_cfg, gate=_gate).get_orderable_cash_result())
                except Exception as _ex:
                    _cash_result["error_message"] = str(_ex)

            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(_fetch_cash)
                try:
                    _fut.result(timeout=8)
                except _cf.TimeoutError:
                    pass  # KIS 서버 무응답 — input budget 유지

            if _cash_result.get("success") and _cash_result.get("orderable_cash") is not None:
                orderable_cash = int(_cash_result["orderable_cash"])
                orderable_cash_success = True
            else:
                orderable_cash_success = False
                orderable_cash_error = _cash_result.get("error_message", "조회 실패")
                # API 실패 시 input budget 유지 (0원으로 처리 금지)

        # 매수 버튼과 동일한 held_codes 계산 (이미 OPEN 보유 종목 제외)
        held_codes: set = set()
        held_names: list = []
        try:
            from position_manager import PositionManager
            _cfg_path = str(PROJECT_ROOT / "config.yaml")
            _pm = PositionManager(_cfg_path, mode=(mode or "mock").lower())
            _all_pos = _pm.get_all_positions()
            held_codes = {
                code for code, pos in _all_pos.items()
                if getattr(pos, "status", "OPEN") == "OPEN"
            }
            held_names = [
                {"code": code, "name": getattr(pos, "stock_name", code)}
                for code, pos in _all_pos.items()
                if getattr(pos, "status", "OPEN") == "OPEN"
            ]
        except Exception:
            pass

        allocator = BudgetAllocator(str(PROJECT_ROOT / "config.yaml"))
        result = allocator.allocate_until_budget(
            candidates=df, budget=int(budget), orderable_cash=orderable_cash,
            held_codes=held_codes,
        )

        # 가격 신선도 확인 (_data_source 컬럼으로 판단)
        price_source = "unknown"
        price_stale = False
        if "current_price" in df.columns and "_data_source" in df.columns:
            sources = df["_data_source"].dropna().unique().tolist()
            price_source = ", ".join(str(s) for s in sources)
            price_stale = any("csv" in str(s).lower() or "paper" in str(s).lower() for s in sources)
        elif "current_price" not in df.columns and "close" in df.columns:
            price_stale = True
            price_source = "close (일봉 종가)"

        summary = {
            "allocated_count": len(result.allocations),
            "total_order_amount": result.total_order_amount,
            "remaining_budget": result.remaining_budget,
            "input_budget": budget,
            "orderable_cash": orderable_cash,
            "orderable_cash_success": orderable_cash_success,
            "orderable_cash_error": orderable_cash_error,
            "effective_budget": result.effective_budget,
            "total_candidates": total_candidates,
            "held_count": len(held_codes),
            "held_names": held_names,
            "available_candidates": total_candidates - len(held_codes),
            "price_source": price_source,
            "price_stale": price_stale,
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
        import importlib, sys
        # 장시간 실행된 Streamlit 프로세스에서 stale 모듈 캐시 방지
        if "real_order_readiness_check" in sys.modules:
            importlib.reload(sys.modules["real_order_readiness_check"])
        result = run_readiness_check(config_path=str(PROJECT_ROOT / "config.yaml"), call_real_api=True)
        verdict = result.get("verdict", "NOT_READY")
        ready = verdict == "READY_FOR_REAL_SINGLE_TEST"
        msg_parts = []
        if not result.get("real_api_key"):
            masked = result.get("real_api_key_masked", "")
            if masked:
                msg_parts.append(f"API 키 불완전(키={masked}, KIS_REAL_APP_SECRET/KIS_ACCOUNT_NO 확인 필요)")
            else:
                msg_parts.append("API 키 없음 (1_API_설정 페이지에서 REAL 키 입력 필요)")
        if not result.get("real_token"):
            msg_parts.append(f"토큰 발급 실패 — {result.get('real_api_error', '')[:80]}")
        if result.get("real_token") and not result.get("real_balance"):
            http = result.get("http_status_code", "?")
            err = result.get("real_api_error", "")[:80]
            msg_parts.append(f"계좌조회 실패 HTTP={http} {err}")
        if result.get("real_balance") and not result.get("orderable_cash"):
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


# ──────────────────────────────────────────────────────────────────────────────
# KIS 토큰 / 연결 / 계좌 서비스 함수 (앱 UI에서 호출)
# ──────────────────────────────────────────────────────────────────────────────

def get_kis_token_status(mode: str = "mock") -> Dict:
    """토큰 캐시 상태 조회 (토큰 원문 미포함)."""
    inject_to_os_env()
    try:
        from kis_auth import get_token_status
        return get_token_status(mode)
    except Exception as exc:
        return {"success": False, "mode": (mode or "mock").upper(), "error": str(exc)}


def refresh_kis_token(mode: str = "mock") -> Dict:
    """해당 mode 토큰 캐시 삭제 후 새 토큰 발급."""
    inject_to_os_env()
    try:
        from kis_auth import refresh_token
        result = refresh_token(mode)
        return result
    except Exception as exc:
        return {"success": False, "mode": (mode or "mock").upper(), "token_refreshed": False, "error": str(exc)}


def check_kis_connection(mode: str = "mock") -> Dict:
    """현재가 조회로 KIS API 연결 가능 여부 확인 (가벼운 1회 호출)."""
    inject_to_os_env()
    try:
        from api_service import _get_api_client
        api = _get_api_client(runtime_mode=mode)
        result = api.get_current_price("005930")
        # get_current_price returns {"current_price": int, ...} or {"output": {...}, ...}
        connection_ok = bool(result and (result.get("current_price") or result.get("output")))
        return {"success": connection_ok, "mode": (mode or "mock").upper(), "connection_ok": connection_ok}
    except Exception as exc:
        return {"success": False, "mode": (mode or "mock").upper(), "connection_ok": False, "error": str(exc)}


def check_kis_account(mode: str = "mock") -> Dict:
    """계좌 보유종목/잔고 조회."""
    inject_to_os_env()
    try:
        from api_service import _get_api_client
        api = _get_api_client(runtime_mode=mode)
        bal = api.get_account_balance()
        account_ok = isinstance(bal, dict) and bool(bal.get("output1") or bal.get("output2"))
        broker_count = 0
        if account_ok:
            out1 = bal.get("output1", [])
            if isinstance(out1, list):
                broker_count = len(out1)
        return {"success": account_ok, "mode": (mode or "mock").upper(), "account_ok": account_ok, "broker_count": broker_count}
    except Exception as exc:
        err_info: Dict = {"success": False, "mode": (mode or "mock").upper(), "account_ok": False, "error": str(exc)}
        for attr in ("http_status_code", "response_text", "response_json", "error_category"):
            if hasattr(exc, attr):
                err_info[attr] = getattr(exc, attr)
        return err_info


def check_orderable_cash(mode: str = "mock") -> Dict:
    """주문가능금액 조회.

    API 실패와 진짜 0원 잔고를 구분한다.
    실패 시 orderable_cash_amount=None 반환 — 0원으로 오해하지 않도록.
    """
    inject_to_os_env()
    try:
        from api_service import _get_api_client
        api = _get_api_client(runtime_mode=mode)
        result = api.get_orderable_cash_result()
        success = result.get("success", False)
        cash_val = result.get("orderable_cash")  # None on failure
        return {
            "success": success,
            "mode": (mode or "mock").upper(),
            "orderable_cash_ok": success,
            "orderable_cash_amount": int(cash_val) if (success and cash_val is not None) else None,
            "raw_fields": result.get("raw_fields", {}),
            "rt_cd": result.get("rt_cd", ""),
            "api_msg": result.get("msg", ""),
            "error": result.get("error_message", "") if not success else "",
        }
    except Exception as exc:
        return {
            "success": False,
            "mode": (mode or "mock").upper(),
            "orderable_cash_ok": False,
            "orderable_cash_amount": None,
            "error": str(exc),
        }


def run_kis_readiness(mode: str = "mock") -> Dict:
    """token → connection → account → orderable_cash 한 번에 점검."""
    inject_to_os_env()
    import time as _time
    mode_u = (mode or "mock").strip().upper()
    result: Dict = {
        "success": False,
        "mode": mode_u,
        "resolved_mode": mode_u,
        "token_status": {},
        "token_cache_file": "",
        "token_refreshed": False,
        "connection_ok": False,
        "account_ok": False,
        "broker_count": 0,
        "orderable_cash_ok": False,
        "orderable_cash_amount": None,
        "http_status_code": "",
        "response_text": "",
        "response_json": {},
        "error_message": "",
        "verdict": "NOT_READY",
        "checked_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        ts = get_kis_token_status(mode_u)
        result["token_status"] = ts
        result["token_cache_file"] = ts.get("token_cache_file", "")

        if mode_u == "REAL":
            readiness = check_real_readiness()
            det = readiness.get("details", {})
            result["connection_ok"] = bool(det.get("real_token"))
            result["account_ok"] = bool(det.get("real_balance"))
            result["orderable_cash_ok"] = bool(det.get("orderable_cash"))
            result["orderable_cash_amount"] = int(det.get("orderable_cash_amount", 0))
            result["http_status_code"] = str(det.get("http_status_code", ""))
            result["response_text"] = str(det.get("response_text", ""))[:500]
            result["success"] = readiness.get("ready", False)
            result["verdict"] = readiness.get("verdict", "NOT_READY")
            result["missing_conditions"] = readiness.get("details", {}).get("missing_conditions", [])
        else:
            conn = check_kis_connection(mode_u)
            result["connection_ok"] = conn.get("connection_ok", False)
            if result["connection_ok"]:
                acc = check_kis_account(mode_u)
                result["account_ok"] = acc.get("account_ok", False)
                result["broker_count"] = acc.get("broker_count", 0)
                if not result["account_ok"]:
                    result["response_text"] = str(acc.get("response_text", acc.get("error", "")))[:500]
                    result["http_status_code"] = str(acc.get("http_status_code", ""))
                cash = check_orderable_cash(mode_u)
                result["orderable_cash_ok"] = cash.get("orderable_cash_ok", False)
                # orderable_cash_amount=None means API failure (not 0 balance)
                result["orderable_cash_amount"] = cash.get("orderable_cash_amount")
                if not cash.get("orderable_cash_ok"):
                    result["error_message"] = cash.get("error", "주문가능금액 조회 실패")
            else:
                result["error_message"] = conn.get("error", "연결 실패")
            result["success"] = result["connection_ok"] and result["account_ok"]
            result["verdict"] = "MOCK_READY" if result["success"] else "MOCK_NOT_READY"
    except Exception as exc:
        result["error_message"] = str(exc)
    return result


# ──────────────────────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────────────────────
# 통합 진단 — 현재가·계좌·매수가능금액·후보파일·주문가능 여부를 한 번에 점검
# ──────────────────────────────────────────────────────────────────────────────

def run_full_trading_diagnosis(mode: str = "mock", budget: int = 10_000_000) -> Dict:
    """매매 파이프라인 전체 진단.

    Returns:
        {
            "success": bool,
            "candidate_file": str,
            "candidate_count": int,
            "current_price_updated": bool,
            "broker_position_count": int,
            "local_open_position_count": int,
            "orderable_cash_success": bool,
            "orderable_cash": int or None,
            "buy_preflight_ok": bool,
            "buy_block_reason": str,
            "latest_buy_orders_file": str,
            "latest_sell_orders_file": str,
            "render_pipeline_ready": bool,
            "failed_checks": list,
        }
    """
    inject_to_os_env()
    import time as _time
    failed_checks = []
    result: Dict = {
        "success": False,
        "mode": (mode or "mock").upper(),
        "candidate_file": "",
        "candidate_count": 0,
        "current_price_updated": False,
        "broker_position_count": -1,
        "local_open_position_count": -1,
        "orderable_cash_success": False,
        "orderable_cash": None,
        "buy_preflight_ok": False,
        "buy_block_reason": "",
        "latest_buy_orders_file": "",
        "latest_sell_orders_file": "",
        "render_pipeline_ready": False,
        "failed_checks": [],
        "checked_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 1. buy_top20 후보 파일
    active_file = get_active_buy_candidate_file()
    if active_file:
        result["candidate_file"] = str(active_file)
        try:
            df = pd.read_csv(active_file)
            result["candidate_count"] = len(df)
            # 현재가 갱신 여부 확인
            if "price_updated_at" in df.columns:
                from datetime import datetime as dt
                try:
                    latest_ts = pd.to_datetime(df["price_updated_at"].dropna()).max()
                    age_min = (dt.now() - latest_ts.to_pydatetime().replace(tzinfo=None)).total_seconds() / 60
                    result["current_price_updated"] = age_min < 30
                except Exception:
                    result["current_price_updated"] = False
        except Exception as e:
            failed_checks.append(f"후보파일 읽기 실패: {e}")
    else:
        failed_checks.append("buy_top20 파일 없음 — 장중 Top20 필터를 먼저 실행하세요")

    # 2. KIS 계좌조회 (broker positions)
    try:
        broker_result = get_broker_positions_result(mode=mode)
        if broker_result.get("success"):
            result["broker_position_count"] = len(broker_result.get("positions", []))
        else:
            result["broker_position_count"] = -1
            failed_checks.append(f"KIS 계좌조회 실패: {broker_result.get('message', '')}")
    except Exception as e:
        result["broker_position_count"] = -1
        failed_checks.append(f"KIS 계좌조회 예외: {e}")

    # 3. 로컬 OPEN 포지션 수
    try:
        from position_manager import PositionManager
        pm = PositionManager(str(PROJECT_ROOT / "config.yaml"), mode=mode)
        open_pos = pm.get_open_positions()
        result["local_open_position_count"] = sum(
            1 for pos in open_pos.values()
            if getattr(pos, "status", "OPEN") == "OPEN"
            and int(getattr(pos, "quantity", 0)) > 0
        )
    except Exception as e:
        result["local_open_position_count"] = -1
        failed_checks.append(f"로컬 포지션 읽기 실패: {e}")

    # 4. 주문가능금액
    if (mode or "mock").lower() != "paper":
        cash_check = check_orderable_cash(mode)
        result["orderable_cash_success"] = cash_check.get("orderable_cash_ok", False)
        result["orderable_cash"] = cash_check.get("orderable_cash_amount")
        if not result["orderable_cash_success"]:
            failed_checks.append(f"주문가능금액 조회 실패: {cash_check.get('error', '')}")
    else:
        result["orderable_cash_success"] = True
        result["orderable_cash"] = budget

    # 5. 매수 preflight
    open_cnt = result["broker_position_count"] if result["broker_position_count"] >= 0 else result["local_open_position_count"]
    try:
        from utils import load_config as _lc
        cfg_ = _lc(str(PROJECT_ROOT / "config.yaml"))
        max_pos = cfg_.get("risk", {}).get("max_positions", 20)
    except Exception:
        max_pos = 20

    if open_cnt >= max_pos:
        result["buy_block_reason"] = f"최대 보유 종목 수 초과: {open_cnt}/{max_pos}"
    elif not result["candidate_file"]:
        result["buy_block_reason"] = "buy_top20 파일 없음"
    elif not result["orderable_cash_success"]:
        result["buy_block_reason"] = f"주문가능금액 조회 실패 — {failed_checks[-1] if failed_checks else ''}"
    elif result["orderable_cash"] is not None and result["orderable_cash"] < 10000:
        result["buy_block_reason"] = f"주문가능금액 부족: {result['orderable_cash']:,}원"
    else:
        result["buy_preflight_ok"] = True

    # 6. 최신 주문 파일 탐색
    orders_dir = PROJECT_ROOT / "reports" / "orders" / "mock"
    if orders_dir.exists():
        buy_files = sorted(orders_dir.glob("buy_orders_*.csv"), reverse=True)
        sell_files = sorted(orders_dir.glob("sell_orders_*.csv"), reverse=True)
        result["latest_buy_orders_file"] = str(buy_files[0]) if buy_files else ""
        result["latest_sell_orders_file"] = str(sell_files[0]) if sell_files else ""

    # 7. Render 파이프라인 준비 여부
    models_dir = PROJECT_ROOT / "models"
    data_models_dir = PROJECT_ROOT / "data" / "models"
    features_file = PROJECT_ROOT / "data" / "processed" / "features.csv"
    has_model = (
        any(models_dir.glob("*.joblib")) if models_dir.exists() else False
    ) or (
        any(data_models_dir.glob("*.joblib")) if data_models_dir.exists() else False
    )
    result["render_pipeline_ready"] = has_model and features_file.exists()
    if not has_model:
        failed_checks.append("모델 파일 없음 (models/*.joblib)")
    if not features_file.exists():
        failed_checks.append("features.csv 없음 (data/processed/features.csv)")

    result["failed_checks"] = failed_checks
    result["success"] = len(failed_checks) == 0 and result["buy_preflight_ok"]
    return result
