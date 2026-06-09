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
from price_tick import adjust_price_to_tick, get_tick_size
from utils import ensure_dir, get_today_str, load_config, setup_logger

logger = setup_logger(__name__, "logs/budget_allocator.log")


def _normalize_stock_code(value) -> str:
    text = str(value).replace(".0", "").strip()
    try:
        return str(int(text)).zfill(6)
    except Exception:
        return text.zfill(6)


def allocate_until_budget(
    candidates: pd.DataFrame,
    budget: int,
    orderable_cash: int | None = None,
    max_orders: int = 100,
    allocation_mode: str = "rank_one_share_then_repeat",
    allow_additional_buy: bool = False,
    held_codes: Optional[set] = None,
    config_path: str = "config.yaml",
) -> pd.DataFrame:
    """Allocate orders by repeatedly buying one share by candidate rank until budget is used."""
    cfg = load_config(config_path)
    buy_adj = cfg.get("order", {}).get("buy_price_adjustment_rate", 0.001)
    effective_budget = int(min(int(budget), int(orderable_cash))) if orderable_cash is not None else int(budget)
    held_codes = held_codes or set()

    base_cols = [
        "rank", "stock_code", "stock_name", "current_price", "order_price", "tick_size",
        "quantity", "order_amount", "cumulative_order_amount", "remaining_budget",
        "allocation_round", "allocation_reason",
    ]
    if candidates is None or candidates.empty or effective_budget <= 0:
        return pd.DataFrame(columns=base_cols)

    df = candidates.copy()
    code_col = "stock_code" if "stock_code" in df.columns else ("ticker" if "ticker" in df.columns else None)
    name_col = "stock_name" if "stock_name" in df.columns else ("name" if "name" in df.columns else None)
    price_col = next((c for c in ("current_price", "close", "price") if c in df.columns), None)
    if code_col is None or price_col is None:
        return pd.DataFrame(columns=base_cols)

    df["_stock_code"] = df[code_col].apply(_normalize_stock_code)
    df["_stock_name"] = df[name_col].fillna("").astype(str) if name_col else df["_stock_code"]
    df["_current_price"] = pd.to_numeric(df[price_col], errors="coerce").fillna(0).astype(float)
    if "rank" in df.columns:
        df["_rank_sort"] = pd.to_numeric(df["rank"], errors="coerce").fillna(999999)
    elif "prediction_score" in df.columns:
        df["_rank_sort"] = -pd.to_numeric(df["prediction_score"], errors="coerce").fillna(0)
    elif "probability_2pct" in df.columns:
        df["_rank_sort"] = -pd.to_numeric(df["probability_2pct"], errors="coerce").fillna(0)
    elif "proba_up" in df.columns:
        df["_rank_sort"] = -pd.to_numeric(df["proba_up"], errors="coerce").fillna(0)
    else:
        df["_rank_sort"] = range(1, len(df) + 1)

    ranked = df.sort_values("_rank_sort", kind="stable").reset_index(drop=True)
    ranked = ranked[ranked["_current_price"] > 0].copy()
    if "buy_allowed" in ranked.columns:
        ranked = ranked[ranked["buy_allowed"].astype(str).str.lower().isin(["true", "1", "yes"])].copy()
    if not allow_additional_buy:
        ranked = ranked[~ranked["_stock_code"].isin(held_codes)].copy()

    rows_by_code: Dict[str, Dict] = {}
    remaining = effective_budget
    cumulative = 0
    order_count = 0
    round_no = 1

    while order_count < max_orders and not ranked.empty:
        bought_in_round = False
        for idx, row in ranked.iterrows():
            if order_count >= max_orders:
                break
            raw_price = int(float(row["_current_price"]) * (1 + buy_adj))
            tick_cfg = cfg.get("order_price", {})
            if tick_cfg.get("tick_adjust_enabled", True):
                order_price = adjust_price_to_tick(raw_price, side="buy", method=tick_cfg.get("buy_tick_method", "floor"))
            else:
                order_price = raw_price
            if order_price <= 0 or order_price > remaining:
                continue

            code = row["_stock_code"]
            cumulative += order_price
            remaining -= order_price
            order_count += 1
            bought_in_round = True
            if code not in rows_by_code:
                rank_value = int(row["rank"]) if "rank" in row and pd.notna(row.get("rank")) else int(idx + 1)
                rows_by_code[code] = {
                    "rank": rank_value,
                    "stock_code": code,
                    "stock_name": row["_stock_name"],
                    "current_price": int(row["_current_price"]),
                    "order_price": order_price,
                    "tick_size": get_tick_size(order_price),
                    "quantity": 0,
                    "order_amount": 0,
                    "cumulative_order_amount": cumulative,
                    "remaining_budget": remaining,
                    "allocation_round": round_no,
                    "allocation_reason": allocation_mode,
                }
            rows_by_code[code]["quantity"] += 1
            rows_by_code[code]["order_amount"] += order_price
            rows_by_code[code]["cumulative_order_amount"] = cumulative
            rows_by_code[code]["remaining_budget"] = remaining
            rows_by_code[code]["allocation_round"] = round_no
        if not bought_in_round:
            break
        round_no += 1

    out = pd.DataFrame(list(rows_by_code.values()), columns=base_cols)
    if not out.empty:
        out = out.sort_values("rank", kind="stable").reset_index(drop=True)
    return out


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
        self.config_path = config_path
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

            raw_price = int(price * (1 + self._buy_adj))
            tick_cfg = self.cfg.get("order_price", {})
            if tick_cfg.get("tick_adjust_enabled", True):
                buy_method = tick_cfg.get("buy_tick_method", "floor")
                order_price = adjust_price_to_tick(raw_price, side="buy", method=buy_method)
            else:
                order_price = raw_price
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

            rank_col = next((c for c in ("rank",) if c in candidates.columns), None)
            alloc = {
                "rank": int(row[rank_col]) if rank_col else (len(allocations) + 1),
                "stock_code": code,
                "stock_name": name,
                "current_price": price,
                "original_price": int(price * (1 + self._buy_adj)),
                "order_price": order_price,
                "tick_size": get_tick_size(order_price),
                "tick_adjusted": (order_price != int(price * (1 + self._buy_adj))),
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

    def allocate_until_budget(
        self,
        candidates: pd.DataFrame,
        budget: int,
        orderable_cash: int | None = None,
        max_orders: int = 100,
        allow_additional_buy: bool = False,
        held_codes: Optional[set] = None,
    ) -> BudgetAllocationResult:
        result = BudgetAllocationResult()
        result.total_budget = float(budget)
        result.orderable_cash = float(orderable_cash or 0)
        result.effective_budget = float(min(budget, orderable_cash) if orderable_cash is not None else budget)
        df = allocate_until_budget(
            candidates=candidates,
            budget=int(budget),
            orderable_cash=int(orderable_cash) if orderable_cash is not None else None,
            max_orders=max_orders,
            allow_additional_buy=allow_additional_buy,
            held_codes=held_codes,
            config_path=self.config_path,
        )
        result.allocations = df.to_dict("records")
        result.total_order_amount = float(df["order_amount"].sum()) if not df.empty else 0.0
        result.remaining_budget = result.effective_budget - result.total_order_amount
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
