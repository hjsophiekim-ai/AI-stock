"""Verify whether a REAL order was accepted by querying today's KIS orders.

Multi-endpoint verification strategy:
  1. TTTC8001R CCLD_DVSN=00 — today's ALL orders (pending + filled)
  2. TTTC8001R CCLD_DVSN=02 — today's UNFILLED orders only
  3. TTTC8001R CCLD_DVSN=01 — today's FILLED orders only

Order number comparison normalizes leading zeros and whitespace.
Raw responses are saved to reports/real_order_verify_raw_YYYYMMDD_HHMMSS.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))

from kis_api import KISApiClient
from real_order_utils import normalize_stock_code, save_verify_csv
from safety_gate import SafetyGate
from utils import ensure_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_FILL_STATUS_FILLED = "FILLED"
_FILL_STATUS_ACCEPTED_UNFILLED = "ACCEPTED_UNFILLED"
_FILL_STATUS_PARTIALLY_FILLED = "PARTIALLY_FILLED"
_FILL_STATUS_SUBMITTED_UNVERIFIED = "SUBMITTED_UNVERIFIED"
_FILL_STATUS_REJECTED = "REJECTED"
_FILL_STATUS_DRY_RUN = "DRY_RUN_ONLY"


def _norm_order_no(val: Any) -> str:
    """Normalize order number: strip whitespace, remove leading zeros for comparison."""
    s = str(val or "").strip()
    return s.lstrip("0") or s  # keep "0" if all zeros


def _parse_row(raw: Dict[str, Any], source_endpoint: str = "") -> Dict[str, Any]:
    """Parse a single KIS order row into a normalized dict."""
    order_no = str(raw.get("odno") or raw.get("ODNO") or raw.get("order_no") or "").strip()
    order_org_no = str(raw.get("orgn_odno") or raw.get("ORGN_ODNO") or raw.get("KRX_FWDG_ORD_ORGNO") or "").strip()
    stock_code = str(raw.get("pdno") or raw.get("PDNO") or raw.get("stock_code") or "").strip()
    stock_name = str(raw.get("prdt_name") or raw.get("PRDT_NAME") or "").strip()
    ordered_qty = int(float(raw.get("ord_qty") or raw.get("ORD_QTY") or 0))
    filled_qty = int(float(raw.get("tot_ccld_qty") or raw.get("CCLD_QTY") or raw.get("filled_qty") or 0))
    unfilled_qty = int(float(raw.get("rmn_qty") or raw.get("RMN_QTY") or max(0, ordered_qty - filled_qty)))
    order_price = int(float(raw.get("ord_unpr") or raw.get("ORD_UNPR") or raw.get("order_price") or 0))
    order_time = str(raw.get("ord_tmd") or raw.get("ORD_TMD") or "").strip()
    sll_buy = str(raw.get("sll_buy_dvsn_cd") or "").strip()
    side = "buy" if sll_buy == "02" else ("sell" if sll_buy == "01" else "")

    # fill_status
    if ordered_qty <= 0:
        fill_status = _FILL_STATUS_SUBMITTED_UNVERIFIED
    elif filled_qty >= ordered_qty:
        fill_status = _FILL_STATUS_FILLED
    elif filled_qty > 0:
        fill_status = _FILL_STATUS_PARTIALLY_FILLED
    else:
        fill_status = _FILL_STATUS_ACCEPTED_UNFILLED

    return {
        "order_no": order_no,
        "order_org_no": order_org_no,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "ordered_qty": ordered_qty,
        "filled_qty": filled_qty,
        "unfilled_qty": unfilled_qty,
        "order_price": order_price,
        "order_time": order_time,
        "side": side,
        "fill_status": fill_status,
        "source_endpoint": source_endpoint,
        "raw": raw,
    }


def _save_raw_dump(
    endpoint_results: List[Dict[str, Any]],
    order_no: str = "",
    stock_code: str = "",
) -> Dict[str, str]:
    """Save raw dump of all endpoint queries to JSON and CSV."""
    ensure_dir(str(PROJECT_ROOT / "reports"))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = PROJECT_ROOT / "reports" / f"real_order_verify_raw_{ts}.json"
    csv_path = PROJECT_ROOT / "reports" / f"real_order_verify_raw_{ts}.csv"

    dump = {
        "run_at": datetime.now().isoformat(),
        "filter_order_no": order_no,
        "filter_stock_code": stock_code,
        "endpoints": endpoint_results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2, default=str)

    # CSV: flatten all rows from all endpoints
    all_rows = []
    for ep in endpoint_results:
        for row in ep.get("parsed_rows", []):
            flat = {k: v for k, v in row.items() if k != "raw"}
            flat["endpoint"] = ep.get("endpoint", "")
            flat["ccld_dvsn"] = ep.get("ccld_dvsn", "")
            flat["ep_rt_cd"] = ep.get("rt_cd", "")
            flat["ep_msg"] = ep.get("msg", "")
            all_rows.append(flat)

    import csv as _csv
    if all_rows:
        fieldnames = list(all_rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = _csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)

    return {"json_path": str(json_path), "csv_path": str(csv_path)}


def run_real_order_verify(
    stock_code: str = "",
    order_no: str = "",
    config_path: str = "config.yaml",
    today: bool = True,
    raw_dump: bool = False,
) -> Dict[str, Any]:
    """Verify a REAL order by querying KIS with multiple strategies.

    Args:
        stock_code: 종목코드 (선택)
        order_no: 주문번호 (선택)
        config_path: config.yaml 경로
        today: 당일 주문 조회 여부
        raw_dump: 원본 응답 저장 여부

    Returns:
        order_found, fill_status, ordered_qty, filled_qty, unfilled_qty,
        order_verify_success, order_found_in_broker,
        raw_dump_json_path, raw_dump_csv_path 등
    """
    gate = SafetyGate(config_path, runtime_mode="real")
    api = KISApiClient(config_path, gate=gate)
    code = normalize_stock_code(stock_code) if stock_code else ""
    norm_no = _norm_order_no(order_no)

    endpoint_results: List[Dict[str, Any]] = []
    found_rows: List[Dict[str, Any]] = []

    # Query all three modes: 전체(00), 미체결(02), 체결(01)
    for ccld_dvsn, label in [("00", "전체"), ("02", "미체결"), ("01", "체결완료")]:
        ep_info: Dict[str, Any] = {
            "endpoint": "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "ccld_dvsn": ccld_dvsn,
            "label": label,
            "order_no_filter": order_no,
            "stock_code_filter": code,
        }
        try:
            resp = api.get_today_orders(
                ccld_dvsn=ccld_dvsn,
                order_no="",   # fetch all, filter locally for better coverage
                stock_code="",
            )
            ep_info["rt_cd"] = resp.get("rt_cd", "")
            ep_info["msg"] = resp.get("msg1", "")
            ep_info["tr_id"] = resp.get("_query_params", {}).get("tr_id", "")
            raw_rows = resp.get("output1") or []
            ep_info["raw_row_count"] = len(raw_rows)
            ep_info["raw_response_text"] = resp.get("response_text", "")[:3000]

            parsed = [_parse_row(r, source_endpoint=f"TTTC8001R_CCLD{ccld_dvsn}") for r in raw_rows]
            ep_info["parsed_rows"] = parsed

            # Filter
            for p in parsed:
                match_no = (not norm_no) or (_norm_order_no(p["order_no"]) == norm_no)
                match_code = (not code) or (p["stock_code"] == code)
                if match_no and match_code:
                    p["matched_by"] = f"ccld_dvsn={ccld_dvsn}"
                    found_rows.append(p)

        except Exception as exc:
            ep_info["error"] = str(exc)
            ep_info["rt_cd"] = "ERROR"
            ep_info["msg"] = str(exc)
            ep_info["parsed_rows"] = []

        endpoint_results.append(ep_info)

    # Deduplicate found rows by order_no
    seen_nos = set()
    deduped: List[Dict[str, Any]] = []
    for r in found_rows:
        key = r["order_no"] or (r["stock_code"] + r["order_time"])
        if key not in seen_nos:
            seen_nos.add(key)
            deduped.append(r)

    order_found = bool(deduped)
    fill_status = _FILL_STATUS_SUBMITTED_UNVERIFIED
    matched_row: Optional[Dict[str, Any]] = None

    if deduped:
        matched_row = deduped[0]
        fill_status = matched_row.get("fill_status", _FILL_STATUS_ACCEPTED_UNFILLED)

    # Save verify CSV
    csv_rows = []
    if deduped:
        for r in deduped:
            csv_rows.append({
                "order_found": True,
                "order_no": r["order_no"],
                "order_org_no": r["order_org_no"],
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name"],
                "ordered_qty": r["ordered_qty"],
                "filled_qty": r["filled_qty"],
                "unfilled_qty": r["unfilled_qty"],
                "order_price": r["order_price"],
                "order_time": r["order_time"],
                "side": r["side"],
                "fill_status": r["fill_status"],
                "source_endpoint": r["source_endpoint"],
                "matched_by": r.get("matched_by", ""),
            })
    else:
        ep_msgs = "; ".join(
            f"[{ep['label']}] rt={ep.get('rt_cd','')} msg={ep.get('msg','')[:40]}"
            for ep in endpoint_results
        )
        csv_rows = [{
            "order_found": False,
            "order_no": order_no,
            "order_org_no": "",
            "stock_code": code,
            "stock_name": "",
            "ordered_qty": 0,
            "filled_qty": 0,
            "unfilled_qty": 0,
            "order_price": 0,
            "order_time": "",
            "side": "",
            "fill_status": _FILL_STATUS_SUBMITTED_UNVERIFIED,
            "source_endpoint": "NONE",
            "matched_by": "",
            "ep_summary": ep_msgs,
        }]

    verify_csv_path = save_verify_csv(csv_rows, prefix="real_order_verify")

    # Save raw dump
    raw_dump_paths: Dict[str, str] = {}
    if raw_dump:
        raw_dump_paths = _save_raw_dump(endpoint_results, order_no=order_no, stock_code=code)

    result: Dict[str, Any] = {
        "run_at": datetime.now().isoformat(),
        "order_found": order_found,
        "order_verify_success": order_found,
        "order_found_in_broker": order_found,
        "fill_status": fill_status,
        "stock_code": code,
        "order_no": order_no,
        "order_org_no": matched_row.get("order_org_no", "") if matched_row else "",
        "ordered_qty": matched_row.get("ordered_qty", 0) if matched_row else 0,
        "filled_qty": matched_row.get("filled_qty", 0) if matched_row else 0,
        "unfilled_qty": matched_row.get("unfilled_qty", 0) if matched_row else 0,
        "order_price": matched_row.get("order_price", 0) if matched_row else 0,
        "order_time": matched_row.get("order_time", "") if matched_row else "",
        "side": matched_row.get("side", "") if matched_row else "",
        "stock_name": matched_row.get("stock_name", "") if matched_row else "",
        "rows": csv_rows,
        "endpoint_count": len(endpoint_results),
        "endpoints_queried": [ep.get("label") for ep in endpoint_results],
        "raw_dump_json_path": raw_dump_paths.get("json_path", ""),
        "raw_dump_csv_path": raw_dump_paths.get("csv_path", ""),
        "verify_csv_path": verify_csv_path,
        "raw_rt_cd": endpoint_results[0].get("rt_cd", "") if endpoint_results else "",
        "raw_msg": endpoint_results[0].get("msg", "") if endpoint_results else "",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="REAL 주문 검증 도구")
    parser.add_argument("--stock-code", default="", help="종목코드")
    parser.add_argument("--order-no", default="", help="주문번호")
    parser.add_argument("--today", action="store_true", default=True, help="당일 주문 조회 (기본값 True)")
    parser.add_argument("--raw-dump", action="store_true", default=False, help="원본 응답 raw dump 저장")
    parser.add_argument("--config", default="config.yaml", help="config.yaml 경로")
    args = parser.parse_args()
    result = run_real_order_verify(
        stock_code=args.stock_code,
        order_no=args.order_no,
        config_path=args.config,
        today=args.today,
        raw_dump=args.raw_dump,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if not result.get("order_found"):
        print("\n[경고] 주문을 찾지 못했습니다.", file=sys.stderr)
        print(f"fill_status: {result.get('fill_status')}", file=sys.stderr)
        print(f"endpoint 응답:", file=sys.stderr)


if __name__ == "__main__":
    main()
