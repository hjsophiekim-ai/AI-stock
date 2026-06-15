"""Select safe intraday buy candidates.

Safe mode creates reports/predictions/buy_top20_safe_YYYYMMDD.csv first.
Only when validation passes is it copied to buy_top20_YYYYMMDD.csv.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from market_safety_filter import (
    apply_safe_intraday_filter,
    copy_if_valid,
    normalize_code,
    summarize_filter_failures,
)
from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/select_today_buy_top20.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
cfg = load_config(str(CONFIG_PATH))


def _safe_cfg() -> dict[str, Any]:
    return cfg.get("safe_intraday_filter", {})


def _candidate_path_for(today: str) -> Path:
    return PROJECT_ROOT / "reports" / "predictions" / f"intraday_candidates_{today}.csv"


def _to_float(data: dict, *keys: str) -> float:
    for key in keys:
        val = data.get(key)
        if val is None or val == "":
            continue
        try:
            return float(str(val).replace(",", ""))
        except Exception:
            continue
    return 0.0


def _enrich_with_kis_realtime(df: pd.DataFrame, mode: str, max_fetch: int) -> pd.DataFrame:
    """Fetch realtime quote fields. Raises if KIS cannot be used."""
    if mode == "paper":
        raise RuntimeError("safe-mode requires KIS mock/real data; paper mode is not orderable")

    from kis_api import KISApiClient
    from safety_gate import SafetyGate

    gate = SafetyGate(str(CONFIG_PATH), runtime_mode=mode)
    api = KISApiClient(str(CONFIG_PATH), gate=gate)
    api.auth.get_access_token()
    meta = api.diagnostic_metadata()
    key_type = meta.get("key_type_used", "")
    price_mode = gate.mode.upper()
    price_source = "KIS_CURRENT_PRICE"
    request_delay = float(cfg.get("kis", {}).get("request_delay", 0.2) or 0.2)

    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    work = df.copy()
    work[code_col] = work[code_col].apply(normalize_code)

    sort_col = "prob_intraday_2pct" if "prob_intraday_2pct" in work.columns else None
    if sort_col:
        work = work.sort_values(sort_col, ascending=False, kind="stable")
    work = work.head(max_fetch).copy()

    rows = []
    failures = []
    now_iso = datetime.now().isoformat()
    for _, row in work.iterrows():
        row = row.copy()
        code = normalize_code(row[code_col])
        try:
            q = api.get_current_price(code)
            if not isinstance(q, dict):
                raise RuntimeError("KIS current price returned no data")
            current = _to_float(q, "current_price", "stck_prpr", "price", "close")
            open_price = _to_float(q, "open_price", "open", "stck_oprc")
            high = _to_float(q, "high_price", "high", "stck_hgpr")
            low = _to_float(q, "low_price", "low", "stck_lwpr")
            volume = _to_float(q, "volume", "acml_vol")
            trading_value = _to_float(q, "trading_value", "acml_tr_pbmn")
            change_rate = _to_float(q, "change_rate", "prdy_ctrt")
            if current <= 0:
                raise RuntimeError("current_price <= 0")
            if open_price <= 0:
                open_price = current
            if high <= 0:
                high = current
            if low <= 0:
                low = current
            row["stock_code"] = code
            row["current_price"] = current
            row["open_price"] = open_price
            row["high_price"] = high
            row["low_price"] = low
            row["volume_intraday"] = volume
            row["trading_value_intraday"] = trading_value
            row["change_rate"] = change_rate
            row["price_updated_at"] = now_iso
            row["price_mode"] = price_mode
            row["price_source"] = price_source
            row["price_key_type_used"] = key_type
            row["price_error"] = ""
            typical = (high + low + current) / 3
            row["vwap_proxy"] = typical
            row["above_vwap"] = current >= typical
            row["recent_return_15m"] = float(row.get("recent_return_15m", 0) or 0)
            row["recent_return_30m"] = float(row.get("recent_return_30m", 0) or 0)
            row["sector_strength_score"] = float(row.get("sector_strength_score", 0.5) or 0.5)
            rows.append(row)
        except Exception as exc:
            failures.append(f"{code}:{exc}")
        time.sleep(request_delay)

    if not rows:
        raise RuntimeError("실시간 현재가 갱신 실패 — 주문 후보 생성 불가: " + "; ".join(failures[:10]))

    out = pd.DataFrame(rows).reset_index(drop=True)
    out["trading_value_rank_market"] = (
        pd.to_numeric(out["trading_value_intraday"], errors="coerce")
        .rank(method="min", ascending=False)
        .astype(int)
    )
    if failures:
        logger.warning("KIS quote failures: %s", "; ".join(failures[:20]))
    return out


def select_buy_top20(
    date_str: str | None = None,
    candidates_path: str | None = None,
    output_dir: str | None = None,
    safe_mode: bool = False,
    mode: str = "mock",
    max_fetch: int = 300,
) -> dict:
    today = date_str or datetime.now().strftime("%Y%m%d")
    output_dir = output_dir or str(PROJECT_ROOT / "reports" / "predictions")
    ensure_dir(output_dir)

    input_path = Path(candidates_path) if candidates_path else _candidate_path_for(today)
    if not input_path.exists():
        latest = sorted((PROJECT_ROOT / "reports" / "predictions").glob("intraday_candidates_????????.csv"), reverse=True)
        if latest:
            input_path = latest[0]
            logger.warning("%s not found; using latest intraday candidate file %s", today, input_path)
        else:
            return {
                "success": False,
                "error": f"intraday 후보 파일 없음: {input_path}",
                "output_file": "",
                "candidate_count": 0,
            }

    df = pd.read_csv(input_path)
    if df.empty:
        return {"success": False, "error": "intraday 후보 파일이 비어 있습니다.", "output_file": "", "candidate_count": 0}
    if "prob_intraday_2pct" not in df.columns:
        return {
            "success": False,
            "error": "prob_intraday_2pct 컬럼 없음: intraday AI 예측을 먼저 실행해야 합니다.",
            "output_file": "",
            "candidate_count": 0,
        }

    if "probability_2pct" in df.columns:
        df = df.rename(columns={"probability_2pct": "legacy_nextday_probability_2pct"})

    if safe_mode:
        try:
            df = _enrich_with_kis_realtime(df, mode=mode.lower(), max_fetch=max_fetch)
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "output_file": "",
                "candidate_count": 0,
            }

    filtered = apply_safe_intraday_filter(df, _safe_cfg())
    max_n = int(_safe_cfg().get("max_buy_candidates", 20) or 20)
    passed = filtered[filtered["market_safety_pass"]].copy()
    passed = passed.sort_values("final_safe_intraday_score", ascending=False, kind="stable").head(max_n)
    passed = passed.reset_index(drop=True)
    passed["final_buy_rank"] = range(1, len(passed) + 1)
    passed["allocation_version"] = "SAFE_INTRADAY_BUY_TOP20_V1"

    safe_path = Path(output_dir) / f"buy_top20_safe_{today}.csv"
    final_path = Path(output_dir) / f"buy_top20_{today}.csv"
    passed.to_csv(safe_path, index=False, encoding="utf-8-sig")

    ok, failed = copy_if_valid(safe_path, final_path, _safe_cfg())
    summary = summarize_filter_failures(filtered)
    min_trade = int(_safe_cfg().get("min_candidates_to_trade", 10))
    candidate_count = len(passed)
    below_min = candidate_count < min_trade
    if below_min and not _safe_cfg().get("allow_trade_when_candidates_below_min", False):
        failed.append("candidate_count_below_min_candidates_to_trade")
        ok = False

    result = {
        "success": bool(ok),
        "safe_mode": bool(safe_mode),
        "input_file": str(input_path),
        "output_file": str(final_path if ok else safe_path),
        "safe_output_file": str(safe_path),
        "candidate_count": int(candidate_count),
        "original_count": int(len(df)),
        "filter_pass_count": int(len(passed)),
        "date": today,
        "failed_checks": failed,
        **summary,
    }
    if not ok:
        result["error"] = "안전 필터 통과 실패 — buy_top20_YYYYMMDD.csv를 업데이트하지 않았습니다."
    logger.info("safe buy_top20 result: %s", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Select safe intraday buy Top20")
    parser.add_argument("--date", default=None)
    parser.add_argument("--input", default=None, help="intraday_candidates CSV path")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--safe-mode", action="store_true", help="Require KIS realtime data and safe filters")
    parser.add_argument("--mode", default="mock", choices=["mock", "real", "paper"])
    parser.add_argument("--max-fetch", type=int, default=300)
    args = parser.parse_args()

    result = select_buy_top20(
        date_str=args.date,
        candidates_path=args.input,
        output_dir=args.output_dir,
        safe_mode=args.safe_mode,
        mode=args.mode,
        max_fetch=args.max_fetch,
    )
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
