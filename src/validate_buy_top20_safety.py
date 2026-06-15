"""Validate that buy_top20 contains only safe intraday order candidates."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from market_safety_filter import normalize_bool, validate_orderable_buy_top20_df
from utils import load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _num(series: pd.Series, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default)


def validate(date_str: str | None = None, candidate_file: str | None = None) -> dict:
    today = date_str or datetime.now().strftime("%Y%m%d")
    path = Path(candidate_file) if candidate_file else PROJECT_ROOT / "reports" / "predictions" / f"buy_top20_{today}.csv"
    cfg = load_config(str(PROJECT_ROOT / "config.yaml")).get("safe_intraday_filter", {})
    if not path.exists():
        return {
            "success": False,
            "candidate_file": str(path),
            "candidate_count": 0,
            "failed_checks": ["candidate_file_missing"],
            "unsafe_rows": [],
        }

    df = pd.read_csv(path)
    failed: list[str] = []
    unsafe_masks: dict[str, pd.Series] = {}

    ok, base_failed = validate_orderable_buy_top20_df(df, cfg)
    failed.extend(base_failed)

    idx = df.index
    unsafe_masks["candidate_count_gt_20"] = pd.Series(len(df) > 20, index=idx)
    unsafe_masks["market_safety_pass_false"] = ~df.get("market_safety_pass", pd.Series(False, index=idx)).apply(normalize_bool)
    unsafe_masks["paper_close"] = df.get("price_source", pd.Series("", index=idx)).astype(str).str.upper().eq("PAPER_CLOSE")
    unsafe_masks["paper_mode"] = df.get("price_mode", pd.Series("", index=idx)).astype(str).str.upper().eq("PAPER")
    unsafe_masks["current_price_non_positive"] = _num(df.get("current_price", pd.Series(0, index=idx))) <= 0
    unsafe_masks["negative_change_rate"] = _num(df.get("change_rate", pd.Series(-999, index=idx))) < 0
    unsafe_masks["change_rate_lte_minus_2"] = _num(df.get("change_rate", pd.Series(-999, index=idx))) <= -2
    unsafe_masks["trading_value_lt_10b"] = _num(df.get("trading_value_intraday", pd.Series(0, index=idx))) < 10_000_000_000
    if "trading_value_rank_market" in df.columns:
        unsafe_masks["trading_value_rank_gt_250"] = _num(df["trading_value_rank_market"], default=999999) > 250
    else:
        unsafe_masks["trading_value_rank_missing"] = pd.Series(False, index=idx)
    unsafe_masks["below_vwap"] = ~df.get("above_vwap", pd.Series(False, index=idx)).apply(normalize_bool)
    unsafe_masks["high_drawdown_lt_minus_4"] = _num(df.get("high_drawdown_pct", pd.Series(-999, index=idx))) < -4
    unsafe_masks["missing_prob_intraday_2pct"] = pd.Series("prob_intraday_2pct" not in df.columns, index=idx)

    for name, mask in unsafe_masks.items():
        if bool(mask.any()) and name not in failed:
            failed.append(name)

    unsafe_rows = []
    if failed and not df.empty:
        combined = pd.Series(False, index=idx)
        for mask in unsafe_masks.values():
            combined = combined | mask
        show_cols = [
            c for c in [
                "stock_code", "stock_name", "current_price", "change_rate",
                "trading_value_intraday", "trading_value_rank_market",
                "above_vwap", "high_drawdown_pct", "prob_intraday_2pct",
                "price_mode", "price_source", "market_safety_pass",
                "market_safety_fail_reason",
            ]
            if c in df.columns
        ]
        unsafe_rows = df.loc[combined, show_cols].head(50).to_dict("records")

    return {
        "success": len(failed) == 0,
        "candidate_file": str(path),
        "candidate_count": int(len(df)),
        "failed_checks": failed,
        "unsafe_rows": unsafe_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate safe buy_top20")
    parser.add_argument("--date", default=None)
    parser.add_argument("--candidate-file", default=None)
    args = parser.parse_args()
    result = validate(args.date, args.candidate_file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
