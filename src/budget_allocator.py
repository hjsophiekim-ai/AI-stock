"""예산 배분 모듈.

사용자 예산 안에서 top100 후보 종목별 주문 수량을 계산합니다.
동일비중 또는 점수 가중 비중 선택 가능.

사용법:
    python src/budget_allocator.py --budget 300000 --min-orders 1 --max-orders 100
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from utils import ensure_dir, get_today_str, load_config, setup_logger

logger = setup_logger(__name__, "logs/budget_allocator.log")


class BudgetAllocationResult:
    def __init__(self) -> None:
        self.total_budget: float = 0
        self.orderable_cash: float = 0
        self.effective_budget: float = 0
        self.allocations: List[Dict] = []
        self.excluded: List[Dict] = []
        self.total_order_amount: float = 0
        self.remaining_budget: float = 0

    def to_dataframe(self) -> pd.DataFrame:
        if not self.allocations:
            return pd.DataFrame()
        return pd.DataFrame(self.allocations)

    def summary(self) -> str:
        lines = [
            f"총 예산: {self.total_budget:,.0f}원",
            f"주문가능금액: {self.orderable_cash:,.0f}원",
            f"실효 예산: {self.effective_budget:,.0f}원",
            f"주문 종목 수: {len(self.allocations)}개",
            f"총 주문금액: {self.total_order_amount:,.0f}원",
            f"잔여 예산: {self.remaining_budget:,.0f}원",
        ]
        return "\n".join(lines)


class BudgetAllocator:
    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        self.ft = self.cfg.get("force_trade", {})
        self._buy_adj = self.cfg.get("order", {}).get("buy_price_adjustment_rate", 0.001)
        ensure_dir("reports")

    def allocate(
        self,
        candidates: pd.DataFrame,
        budget: float,
        orderable_cash: float = 0.0,
        min_orders: int = 1,
        max_orders: int = 100,
        use_score_weight: bool = False,
    ) -> BudgetAllocationResult:
        """후보 종목에 예산을 배분합니다.

        Args:
            candidates: 후보 종목 DataFrame (stock_code 또는 ticker, close 또는 current_price 필수)
            budget: 사용자 예산
            orderable_cash: 주문가능금액 (0이면 예산만 사용)
            min_orders: 최소 주문 종목 수
            max_orders: 최대 주문 종목 수
            use_score_weight: True이면 점수 가중 비중, False이면 동일비중
        """
        result = BudgetAllocationResult()
        result.total_budget = budget
        result.orderable_cash = orderable_cash

        # 실효 예산
        effective = budget
        if orderable_cash > 0:
            effective = min(effective, orderable_cash)
        result.effective_budget = effective

        if candidates.empty:
            logger.warning("후보 종목 없음")
            result.remaining_budget = effective
            return result

        # 컬럼 감지
        code_col = "stock_code" if "stock_code" in candidates.columns else "ticker"
        name_col = "stock_name" if "stock_name" in candidates.columns else (
            "name" if "name" in candidates.columns else None
        )
        price_col = next((c for c in ("close", "current_price") if c in candidates.columns), None)
        score_col = next((c for c in ("probability_2pct", "prediction_score", "proba_up") if c in candidates.columns), None)

        if price_col is None:
            logger.error("가격 컬럼 없음 (close 또는 current_price 필요)")
            result.remaining_budget = effective
            return result

        # max_orders 제한
        cands = candidates.head(max_orders).copy()
        n = len(cands)

        # 비중 계산
        if use_score_weight and score_col:
            scores = cands[score_col].fillna(0).clip(0, 1)
            total_score = scores.sum()
            weights = scores / total_score if total_score > 0 else pd.Series([1.0 / n] * n, index=cands.index)
        else:
            weights = pd.Series([1.0 / n] * n, index=cands.index)

        remaining = effective
        allocations = []

        for i, (_, row) in enumerate(cands.iterrows()):
            code = str(row[code_col]).zfill(6)
            name = str(row[name_col]) if name_col else code
            price = float(row[price_col])

            if price <= 0:
                result.excluded.append({"stock_code": code, "stock_name": name, "reason": "가격 0"})
                continue

            order_price = int(price * (1 + self._buy_adj))
            slot = effective * float(weights.iloc[i])
            slot = min(slot, remaining)

            # 1주 이상 살 수 있는지
            if order_price > slot:
                if order_price <= remaining and len(allocations) >= min_orders:
                    result.excluded.append({
                        "stock_code": code, "stock_name": name,
                        "reason": f"비중배분({slot:,.0f}원)으로 1주 불가 (주가={order_price:,}원)",
                    })
                    continue
                slot = remaining

            qty = int(slot // order_price)
            if qty <= 0:
                result.excluded.append({
                    "stock_code": code, "stock_name": name,
                    "reason": f"주문수량 0 (예산={slot:,.0f}원 < 주가={order_price:,}원)",
                })
                continue

            order_amount = qty * order_price
            remaining -= order_amount

            alloc = {
                "stock_code": code,
                "stock_name": name,
                "current_price": price,
                "order_price": order_price,
                "quantity": qty,
                "order_amount": order_amount,
                "prediction_score": float(row.get(score_col, 0)) if score_col else 0,
            }
            # 하위 호환 컬럼
            alloc["ticker"] = code
            alloc["name"] = name
            allocations.append(alloc)
            logger.info("배분: %s(%s) %d주 × %d원 = %d원", code, name, qty, order_price, order_amount)

            if remaining <= 0:
                break

        result.allocations = allocations
        result.total_order_amount = sum(a["order_amount"] for a in allocations)
        result.remaining_budget = remaining
        return result

    def save_result(self, result: BudgetAllocationResult, date_str: Optional[str] = None) -> str:
        ds = date_str or get_today_str("%Y%m%d")
        path = os.path.join("reports", f"budget_allocation_{ds}.csv")
        df = result.to_dataframe()
        if not df.empty:
            df.to_csv(path, index=False, encoding="utf-8-sig")
            logger.info("예산 배분 결과 저장: %s", path)
        return path


def main() -> None:
    parser = argparse.ArgumentParser(description="예산 배분 계산")
    parser.add_argument("--budget", type=int, required=True, help="총 예산 (원)")
    parser.add_argument("--min-orders", type=int, default=1, help="최소 주문 종목 수")
    parser.add_argument("--max-orders", type=int, default=100, help="최대 주문 종목 수 (기본값: 100)")
    parser.add_argument("--score-weight", action="store_true", help="점수 가중 비중 사용")
    parser.add_argument("--date", default=None, help="날짜 YYYYMMDD")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    allocator = BudgetAllocator(args.config)

    from force_trade_selector import ForceTradeSelector
    selector = ForceTradeSelector(args.config)
    try:
        candidates = selector.select(
            date_str=args.date,
            min_candidates=args.min_orders,
            max_candidates=args.max_orders,
        )
    except Exception as e:
        print(f"후보 선정 실패: {e}")
        sys.exit(1)

    orderable_cash = 0.0
    from safety_gate import SafetyGate, TRADE_MODE_PAPER
    gate = SafetyGate(args.config)
    if gate.mode != TRADE_MODE_PAPER:
        try:
            from kis_api import KISApiClient
            api = KISApiClient(args.config)
            orderable_cash = api.get_orderable_cash()
            print(f"주문가능금액: {orderable_cash:,.0f}원")
        except Exception as e:
            print(f"주문가능금액 조회 실패 (예산만 사용): {e}")

    result = allocator.allocate(
        candidates=candidates,
        budget=args.budget,
        orderable_cash=orderable_cash,
        min_orders=args.min_orders,
        max_orders=args.max_orders,
        use_score_weight=args.score_weight,
    )

    print(f"\n{result.summary()}")
    if result.allocations:
        print(f"\n주문 대상 종목 ({len(result.allocations)}개):")
        df = result.to_dataframe()
        print(df[["stock_code", "stock_name", "quantity", "order_price", "order_amount"]].to_string(index=False))

    if result.excluded:
        print(f"\n제외 종목 ({len(result.excluded)}개):")
        for exc in result.excluded[:5]:
            print(f"  {exc.get('stock_code','')} {exc.get('stock_name','')}: {exc.get('reason','')}")
        if len(result.excluded) > 5:
            print(f"  ... 외 {len(result.excluded)-5}개")

    path = allocator.save_result(result, args.date)
    print(f"\n결과 저장: {path}")


if __name__ == "__main__":
    main()
