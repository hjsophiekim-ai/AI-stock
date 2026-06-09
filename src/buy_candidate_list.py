"""Buy ranked AI candidates with a fixed budget.

PAPER records virtual orders only. MOCK calls the KIS mock investment API.
REAL remains protected by SafetyGate and the existing configuration checks.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from budget_allocator import BudgetAllocator
from price_tick import get_tick_size
from strategy_config import get_strategy, validate_strategy_id
from utils import ensure_dir, setup_logger

logger = setup_logger(__name__, "logs/buy_candidate_list.log")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")


def _normalize_code(code) -> str:
    text = str(code).replace(".0", "").strip()
    try:
        return str(int(text)).zfill(6)
    except Exception:
        return text.zfill(6)


def _load_candidates(file_path: str, selected_codes: Optional[List[str]] = None) -> pd.DataFrame:
    df = pd.read_csv(file_path)
    if df.empty:
        return df
    code_col = "stock_code" if "stock_code" in df.columns else ("ticker" if "ticker" in df.columns else None)
    name_col = "stock_name" if "stock_name" in df.columns else ("name" if "name" in df.columns else None)
    if code_col is None:
        raise ValueError("stock_code/ticker column is required")
    df[code_col] = df[code_col].apply(_normalize_code)
    df["_code_normalized"] = df[code_col]
    df["_name_normalized"] = df[name_col].fillna("").astype(str) if name_col else df["_code_normalized"]
    if selected_codes:
        selected = {_normalize_code(c) for c in selected_codes}
        df = df[df["_code_normalized"].isin(selected)].copy()
    return df


def _refresh_prices(df: pd.DataFrame, api) -> pd.DataFrame:
    for idx, row in df.iterrows():
        try:
            info = api.get_current_price(row["_code_normalized"])
            price = int(info.get("current_price", 0) or 0)
            if price > 0:
                df.at[idx, "current_price"] = price
                df.at[idx, "close"] = price
        except Exception as ex:
            logger.warning("current price refresh failed %s: %s", row["_code_normalized"], ex)
        time.sleep(0.05)
    return df


def _save_preview(preview: list, today: str) -> None:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    pd.DataFrame(preview).to_csv(PROJECT_ROOT / "reports" / f"orders_preview_{today}.csv", index=False, encoding="utf-8-sig")


def _save_orders(order_results: list, today: str, ts: str) -> None:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    df = pd.DataFrame(order_results)
    df.to_csv(PROJECT_ROOT / "reports" / f"orders_{today}.csv", index=False, encoding="utf-8-sig")
    df.to_csv(PROJECT_ROOT / "reports" / f"strategy_orders_{today}.csv", index=False, encoding="utf-8-sig")
    summary = {
        "timestamp": ts,
        "orders_placed": int((df["success"] == True).sum()) if "success" in df.columns else 0,
        "total": len(df),
        "strategy_id": df["strategy_id"].iloc[0] if "strategy_id" in df.columns and len(df) else "",
    }
    with open(PROJECT_ROOT / "reports" / f"strategy_execution_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def _save_budget_usage(result: dict, allocations: list, today: str) -> None:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    rows = [{
        "timestamp": datetime.now().isoformat(),
        "input_budget": result.get("input_budget", 0),
        "orderable_cash": result.get("orderable_cash", 0),
        "effective_budget": result.get("effective_budget", 0),
        "expected_order_amount": result.get("expected_order_amount", 0),
        "remaining_budget": result.get("remaining_budget", 0),
        "order_count": len(allocations),
    }]
    pd.DataFrame(rows).to_csv(PROJECT_ROOT / "reports" / f"budget_usage_{today}.csv", index=False, encoding="utf-8-sig")


def _get_orderable_cash(mode: str, budget: int, errors: list) -> int:
    if mode == "paper":
        return int(budget)
    try:
        from kis_api import KISApiClient
        from safety_gate import SafetyGate
        gate = SafetyGate(CONFIG_PATH, runtime_mode=mode)
        cash = int(KISApiClient(CONFIG_PATH, gate=gate).get_orderable_cash() or 0)
        return cash if cash > 0 else int(budget)
    except Exception as ex:
        errors.append(f"orderable cash lookup failed; using input budget: {ex}")
        return int(budget)


def _execute_order(stock_code: str, stock_name: str, quantity: int, order_price: int, mode: str, allow_additional_buy: bool = False) -> dict:
    from order_manager import OrderManager
    from safety_gate import SafetyGate

    normalized_mode = (mode or "paper").lower()
    gate = SafetyGate(CONFIG_PATH, runtime_mode=normalized_mode)
    mgr = OrderManager(CONFIG_PATH, gate=gate)
    result = mgr.place_order_with_verification(
        stock_code=stock_code,
        stock_name=stock_name,
        quantity=int(quantity),
        order_price=int(order_price),
        current_price=int(order_price),
        side="buy",
        allow_additional_buy=allow_additional_buy,
    )
    result.setdefault("requested_mode", normalized_mode)
    result.setdefault("resolved_mode", gate.mode)
    result.setdefault("api_called", gate.mode != "PAPER")
    result.setdefault("mock_order_called", gate.mode == "MOCK")
    result.setdefault("real_order_called", gate.mode == "REAL")
    return result


def buy_candidates(
    candidate_file: str,
    budget: int,
    mode: str = "paper",
    strategy_id: str = "morning_0930",
    min_orders: int = 1,
    max_orders: int = 100,
    selected_codes: Optional[List[str]] = None,
    refresh_prices: bool = False,
    preview_only: bool = False,
    allow_outside_window: bool = True,
    allow_additional_buy: bool = False,
) -> dict:
    errors: list = []
    mode = (mode or "paper").lower()
    today = datetime.now().strftime("%Y%m%d")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        if not os.path.exists(candidate_file):
            return {"success": False, "message": f"file not found: {candidate_file}", "errors": [candidate_file], "orders_placed": 0, "allocation_preview": []}
        if not validate_strategy_id(strategy_id):
            return {"success": False, "message": f"invalid strategy_id: {strategy_id}", "errors": [strategy_id], "orders_placed": 0, "allocation_preview": []}

        strategy_cfg = get_strategy(strategy_id)
        df = _load_candidates(candidate_file, selected_codes)
        if df.empty:
            return {"success": False, "message": "no candidates after filtering", "errors": ["no candidates"], "orders_placed": 0, "allocation_preview": []}

        if refresh_prices and not preview_only and mode in ("mock", "real"):
            from kis_api import KISApiClient
            from safety_gate import SafetyGate
            gate = SafetyGate(CONFIG_PATH, runtime_mode=mode)
            df = _refresh_prices(df, KISApiClient(CONFIG_PATH, gate=gate))

        orderable_cash = _get_orderable_cash(mode, int(budget), errors)
        held_codes = set()
        try:
            from position_manager import PositionManager
            held_codes = set(PositionManager(CONFIG_PATH).get_all_positions().keys())
        except Exception:
            pass

        allocator = BudgetAllocator(CONFIG_PATH)
        alloc_result = allocator.allocate_until_budget(
            candidates=df,
            budget=int(budget),
            orderable_cash=orderable_cash,
            max_orders=int(max_orders),
            allow_additional_buy=allow_additional_buy,
            held_codes=held_codes,
        )
        allocations = alloc_result.allocations
        base_result = {
            "requested_mode": mode,
            "input_budget": int(budget),
            "orderable_cash": int(orderable_cash),
            "effective_budget": int(alloc_result.effective_budget),
            "expected_order_amount": int(alloc_result.total_order_amount),
            "remaining_budget": int(alloc_result.remaining_budget),
            "strategy_id": strategy_id,
            "strategy_name": strategy_cfg["name"],
        }
        _save_budget_usage(base_result, allocations, today)

        if not allocations:
            return {**base_result, "success": False, "message": "no orderable candidates within budget", "errors": errors, "orders_placed": 0, "allocation_preview": []}

        for a in allocations:
            a["strategy_id"] = strategy_cfg["id"]
            a["strategy_name"] = strategy_cfg["name"]
            a["allowed_sell_sessions"] = strategy_cfg["allowed_sell_sessions"]
            a["take_profit_rate"] = strategy_cfg["take_profit_rate"]
            a["stop_loss_rate"] = strategy_cfg["stop_loss_rate"]
            a["force_exit_rule"] = strategy_cfg["force_exit_rule"]

        preview = [{
            "strategy_id": a["strategy_id"],
            "strategy_name": a["strategy_name"],
            "stock_code": a["stock_code"],
            "stock_name": a["stock_name"],
            "current_price": int(a["current_price"]),
            "order_price": int(a["order_price"]),
            "tick_size": int(a.get("tick_size", get_tick_size(int(a["order_price"])))),
            "quantity": int(a["quantity"]),
            "order_amount": int(a["order_amount"]),
            "cumulative_order_amount": int(a.get("cumulative_order_amount", 0)),
            "remaining_budget": int(a.get("remaining_budget", 0)),
            "take_profit_rate": a["take_profit_rate"],
            "stop_loss_rate": a["stop_loss_rate"],
        } for a in allocations]

        if preview_only:
            _save_preview(preview, today)
            return {**base_result, "success": True, "stage": "preview", "message": f"preview created: {len(preview)} orders", "orders_placed": 0, "allocation_preview": preview, "errors": errors}

        from position_manager import PositionManager
        pm = PositionManager(CONFIG_PATH)
        order_results = []
        orders_placed = 0
        for a in allocations:
            code = a["stock_code"]
            name = a["stock_name"]
            qty = int(a["quantity"])
            price = int(a["order_price"])
            order_r = _execute_order(code, name, qty, price, mode, allow_additional_buy=allow_additional_buy)
            order_r.update({
                "strategy_id": a["strategy_id"],
                "strategy_name": a["strategy_name"],
                "stock_code": code,
                "stock_name": name,
                "current_price": int(a["current_price"]),
                "adjusted_price": price,
                "tick_size": a.get("tick_size", get_tick_size(price)),
                "quantity": qty,
                "order_amount": int(a["order_amount"]),
            })
            order_results.append(order_r)
            if order_r.get("success"):
                orders_placed += 1
                pm.update_position_after_buy(
                    stock_code=code,
                    stock_name=name,
                    quantity=qty,
                    entry_price=float(price),
                    order_no=order_r.get("order_no", ""),
                    strategy_id=strategy_cfg["id"],
                    strategy_name=strategy_cfg["name"],
                    take_profit_rate=strategy_cfg["take_profit_rate"],
                    stop_loss_rate=strategy_cfg["stop_loss_rate"],
                    allowed_sell_sessions=strategy_cfg["allowed_sell_sessions"],
                    buy_window=f"{strategy_cfg['buy_window_start']}~{strategy_cfg['buy_window_end']}",
                    force_exit_rule=strategy_cfg["force_exit_rule"],
                )
            else:
                errors.append(f"{code} order failed: {order_r.get('rejected_reason') or order_r.get('reason') or order_r.get('msg', '')}")

        _save_orders(order_results, today, ts)
        return {
            **base_result,
            "success": orders_placed > 0,
            "stage": "completed",
            "message": f"{orders_placed}/{len(allocations)} orders completed (strategy: {strategy_cfg['name']})",
            "orders_placed": orders_placed,
            "total_attempted": len(allocations),
            "allocation_preview": preview,
            "order_results": order_results,
            "errors": errors,
        }
    except Exception as ex:
        import traceback
        tb = traceback.format_exc()
        logger.error("buy_candidates exception: %s", tb)
        return {"success": False, "stage": "exception", "message": f"buy execution exception: {ex}", "errors": [tb], "traceback": tb, "orders_placed": 0, "allocation_preview": []}


def main() -> None:
    parser = argparse.ArgumentParser(description="Buy AI candidate list")
    parser.add_argument("--file", required=True)
    parser.add_argument("--budget", type=int, default=300_000)
    parser.add_argument("--mode", default="paper", choices=["paper", "mock", "real"])
    parser.add_argument("--strategy", default="morning_0930", choices=["morning_0930", "afternoon_1500"])
    parser.add_argument("--max-orders", type=int, default=100)
    parser.add_argument("--min-orders", type=int, default=1)
    parser.add_argument("--selected-codes", default=None)
    parser.add_argument("--refresh-prices", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--allow-additional-buy", action="store_true")
    args = parser.parse_args()

    selected = [c.strip() for c in args.selected_codes.split(",")] if args.selected_codes else None
    result = buy_candidates(
        candidate_file=args.file,
        budget=args.budget,
        mode=args.mode,
        strategy_id=args.strategy,
        min_orders=args.min_orders,
        max_orders=args.max_orders,
        selected_codes=selected,
        refresh_prices=args.refresh_prices,
        preview_only=args.preview,
        allow_additional_buy=args.allow_additional_buy,
    )
    print(json.dumps({
        "success": result.get("success"),
        "message": result.get("message"),
        "requested_mode": result.get("requested_mode"),
        "orders_placed": result.get("orders_placed", 0),
        "input_budget": result.get("input_budget", 0),
        "orderable_cash": result.get("orderable_cash", 0),
        "effective_budget": result.get("effective_budget", 0),
        "expected_order_amount": result.get("expected_order_amount", 0),
        "remaining_budget": result.get("remaining_budget", 0),
        "errors": result.get("errors", [])[:3],
    }, ensure_ascii=False, indent=2))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
