"""예산배분 결과 진단 CLI — 종목별 배분 금액/수량과 Top20 제한 준수 여부 확인.

실행 예시:
  python src/diagnose_budget_allocation.py
  python src/diagnose_budget_allocation.py --date 20260615 --budget 300000
  python src/diagnose_budget_allocation.py --candidate-file reports/predictions/buy_top20_20260615.csv --budget 500000
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

_SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SRC_DIR.parent
sys.path.insert(0, str(_SRC_DIR))

from utils import get_today_str, load_config, setup_logger

logger = setup_logger(__name__, "logs/diagnose_budget_allocation.log")
cfg = load_config(str(PROJECT_ROOT / "config.yaml"))


def find_best_candidate_file(date_str: str) -> str:
    preds_dir = PROJECT_ROOT / "reports" / "predictions"
    for name in (f"buy_top20_{date_str}.csv", f"top100_{date_str}.csv", f"top20_{date_str}.csv"):
        p = preds_dir / name
        if p.exists():
            return str(p)
    for pat in ("buy_top20_????????.csv", "top100_????????.csv"):
        found = sorted(preds_dir.glob(pat), reverse=True)
        if found:
            return str(found[0])
    return ""


def diagnose(candidate_file: str, budget: int, mode: str = "paper") -> dict:
    print(f"\n{'='*60}", flush=True)
    print(f"  예산배분 진단", flush=True)
    print(f"  파일: {candidate_file}", flush=True)
    print(f"  예산: {budget:,}원  모드: {mode}", flush=True)
    print(f"{'='*60}", flush=True)

    if not Path(candidate_file).exists():
        print(f"❌ 파일 없음: {candidate_file}", flush=True)
        return {"success": False, "error": "file not found"}

    df = pd.read_csv(candidate_file)
    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    name_col = "stock_name" if "stock_name" in df.columns else None
    df[code_col] = df[code_col].astype(str).str.zfill(6)

    print(f"\n[후보 파일 정보]", flush=True)
    print(f"  종목 수: {len(df)}", flush=True)
    print(f"  컬럼: {list(df.columns)}", flush=True)
    print(f"  Top20 준수: {'✅' if len(df) <= 20 else '❌ 초과!'}", flush=True)

    if len(df) > 20:
        print(f"  ⚠️  종목 수 {len(df)}개 > 20: 상위 20개만 사용됩니다", flush=True)
        df = df.head(20)

    price_col = next((c for c in ("current_price", "close") if c in df.columns), None)
    if price_col is None:
        print(f"  ❌ 가격 컬럼 없음 (current_price / close 필요)", flush=True)
        return {"success": False, "error": "no price column"}

    df["_price"] = df[price_col].astype(float).fillna(0)
    df = df[df["_price"] > 0]

    per_stock_budget = budget / max(len(df), 1)
    df["_qty"] = (per_stock_budget / df["_price"]).astype(int).clip(lower=0)
    df["_order_amount"] = df["_qty"] * df["_price"]
    df["_skip"] = df["_qty"] == 0

    total_amount = df[~df["_skip"]]["_order_amount"].sum()
    order_count = int((df["_qty"] > 0).sum())

    print(f"\n[배분 결과 (균등분배 시뮬레이션)]", flush=True)
    print(f"  주문 가능 종목: {order_count}/{len(df)}개", flush=True)
    print(f"  예상 총 주문금액: {int(total_amount):,}원", flush=True)
    print(f"  잔여 예산: {int(budget - total_amount):,}원", flush=True)

    print(f"\n{'종목코드':8s} {'종목명':14s} {'가격':>10s} {'수량':>5s} {'주문금액':>12s}", flush=True)
    print(f"{'-'*60}", flush=True)
    for _, row in df.iterrows():
        code = str(row[code_col])
        name = str(row.get(name_col, ""))[:12] if name_col else ""
        price = int(row["_price"])
        qty = int(row["_qty"])
        amt = int(row["_order_amount"])
        skip = "SKIP" if row["_skip"] else ""
        print(f"{code:8s} {name:14s} {price:>10,} {qty:>5} {amt:>12,} {skip}", flush=True)

    alloc_v = df["allocation_version"].iloc[0] if "allocation_version" in df.columns else "N/A"
    print(f"\n[allocation_version]: {alloc_v}", flush=True)
    print(f"{'='*60}\n", flush=True)

    return {
        "success": True,
        "candidate_count": len(df),
        "order_count": order_count,
        "total_amount": int(total_amount),
        "remaining": int(budget - total_amount),
        "top20_compliant": len(df) <= 20,
        "allocation_version": alloc_v,
    }


def main():
    parser = argparse.ArgumentParser(description="예산배분 진단")
    parser.add_argument("--date", default=None, help="YYYYMMDD (기본: 오늘)")
    parser.add_argument("--budget", type=int, default=300_000, help="총 예산 (원, 기본: 300000)")
    parser.add_argument("--mode", default="paper", choices=["paper", "mock", "real"])
    parser.add_argument("--candidate-file", default=None, help="후보 파일 경로 (지정 시 date 무시)")
    args = parser.parse_args()

    date_str = args.date or get_today_str("%Y%m%d")
    cfile = args.candidate_file or find_best_candidate_file(date_str)

    if not cfile:
        print(f"❌ 후보 파일을 찾을 수 없습니다. 먼저 파이프라인을 실행하세요.", flush=True)
        sys.exit(1)

    result = diagnose(cfile, args.budget, args.mode)
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
