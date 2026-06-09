"""Monitor AI candidates and buy only when intraday timing conditions pass."""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from buy_candidate_list import buy_candidates
from intraday_timing_engine import evaluate_candidates_timing
from utils import ensure_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _save_orders(rows: list[dict]) -> str:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    path = PROJECT_ROOT / "reports" / f"intraday_buy_orders_{datetime.now().strftime('%Y%m%d')}.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


def run_once(
    candidate_file: str,
    budget: int,
    mode: str,
    strategy: str,
    timing: str,
    interval: str,
    max_orders: int = 100,
    already_ordered: set | None = None,
) -> dict:
    already_ordered = already_ordered or set()
    candidates = pd.read_csv(candidate_file)
    code_col = "stock_code" if "stock_code" in candidates.columns else ("ticker" if "ticker" in candidates.columns else None)
    if code_col is None:
        raise ValueError("stock_code/ticker column is required")
    candidates[code_col] = candidates[code_col].astype(str).str.replace(".0", "", regex=False).str.zfill(6)
    candidates = candidates[~candidates[code_col].isin(already_ordered)].copy()
    signals = evaluate_candidates_timing(candidates, interval=interval, method=timing)
    buy_codes = signals[signals["buy_now"] == True]["stock_code"].astype(str).str.zfill(6).tolist()
    if not buy_codes:
        report_path = _save_orders([])
        return {"success": True, "orders_placed": 0, "buy_candidates": 0, "signals": len(signals), "report_path": report_path}

    result = buy_candidates(
        candidate_file=candidate_file,
        budget=budget,
        mode=mode,
        strategy_id=strategy,
        max_orders=min(max_orders, len(buy_codes)),
        selected_codes=buy_codes,
        timing_method="immediate",
        interval=interval,
        allow_additional_buy=False,
    )
    rows = result.get("order_results", [])
    for row in rows:
        row["timing_method"] = timing
        row["interval"] = interval
    report_path = _save_orders(rows)
    return {**result, "signals": len(signals), "buy_candidates": len(buy_codes), "report_path": report_path}


def run_loop(args) -> None:
    already_ordered = set()
    while True:
        result = run_once(args.file, args.budget, args.mode, args.strategy, args.timing, args.interval, args.max_orders, already_ordered)
        for row in result.get("order_results", []):
            if row.get("success"):
                already_ordered.add(str(row.get("stock_code", "")).zfill(6))
        print(json.dumps({k: v for k, v in result.items() if k not in ("order_results", "allocation_preview")}, ensure_ascii=False))
        time.sleep(args.sleep)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--mode", default="paper", choices=["paper", "mock", "real"])
    parser.add_argument("--strategy", default="morning_0930", choices=["morning_0930", "afternoon_1500"])
    parser.add_argument("--timing", default="rsi_macd_rebound", choices=["immediate", "rsi_macd_rebound", "vwap_reclaim", "momentum_breakout", "pullback_then_up"])
    parser.add_argument("--interval", default="1min", choices=["1min", "3min"])
    parser.add_argument("--max-orders", type=int, default=100)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--sleep", type=float, default=30)
    args = parser.parse_args()
    if args.loop:
        run_loop(args)
    else:
        print(json.dumps(run_once(args.file, args.budget, args.mode, args.strategy, args.timing, args.interval, args.max_orders), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
