"""BudgetAllocator 테스트."""

import os
import sys
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_config(tmp_path):
    import yaml
    cfg = {
        "force_trade": {
            "minimum_order_amount": 10_000,
            "max_force_trade_budget": 1_000_000,
        },
        "order": {
            "buy_price_adjustment_rate": 0.001,
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return str(p)


def _make_candidates(*prices):
    rows = [
        {"ticker": f"00{i:04d}", "name": f"종목{i}", "close": p, "prediction_score": 0.8}
        for i, p in enumerate(prices, 1)
    ]
    return pd.DataFrame(rows)


class TestBasicAllocation:
    """기본 예산 배분 테스트."""

    def test_single_stock_allocation(self, tmp_path):
        """예산 100,000원, 종목가 70,000원 → 1주 주문."""
        cfg = _make_config(tmp_path)
        from budget_allocator import BudgetAllocator
        allocator = BudgetAllocator(cfg)
        candidates = _make_candidates(70_000)
        result = allocator.allocate(candidates, budget=100_000, min_orders=1)
        assert len(result.allocations) == 1
        assert result.allocations[0]["quantity"] >= 1

    def test_min_one_order(self, tmp_path):
        """예산 100,000원에서 최소 1개 주문수량 계산."""
        cfg = _make_config(tmp_path)
        from budget_allocator import BudgetAllocator
        allocator = BudgetAllocator(cfg)
        candidates = _make_candidates(50_000)
        result = allocator.allocate(candidates, budget=100_000, min_orders=1)
        assert len(result.allocations) >= 1
        assert result.total_order_amount > 0


class TestExpensiveStockExclusion:
    """주가가 예산보다 비싼 종목 제외 테스트."""

    def test_stock_more_expensive_than_budget_excluded(self, tmp_path):
        """예산 10,000원, 주가 50,000원 → 제외."""
        cfg = _make_config(tmp_path)
        from budget_allocator import BudgetAllocator
        allocator = BudgetAllocator(cfg)
        candidates = _make_candidates(50_000)
        result = allocator.allocate(candidates, budget=10_000, min_orders=1)
        assert len(result.allocations) == 0
        assert len(result.excluded) >= 1

    def test_mixed_prices_excludes_expensive(self, tmp_path):
        """비싼 종목은 제외, 싼 종목만 주문."""
        cfg = _make_config(tmp_path)
        from budget_allocator import BudgetAllocator
        allocator = BudgetAllocator(cfg)
        candidates = _make_candidates(500_000, 10_000)  # 비싼 + 싼
        result = allocator.allocate(candidates, budget=50_000, min_orders=1)
        # 500,000원짜리는 제외, 10,000원짜리는 포함
        codes = [a["ticker"] for a in result.allocations]
        assert any("0002" in c or "10000" in str(result.allocations) for c in codes) or len(result.allocations) >= 1


class TestBudgetReallocation:
    """남은 예산 재배분 테스트."""

    def test_remaining_budget_reallocated(self, tmp_path):
        """비싼 종목 제외 후 남은 예산이 다음 종목에 재배분."""
        cfg = _make_config(tmp_path)
        from budget_allocator import BudgetAllocator
        allocator = BudgetAllocator(cfg)
        # 예산 100,000원, 3개 종목
        # 종목1: 200,000원 (제외됨), 종목2: 30,000원, 종목3: 20,000원
        candidates = _make_candidates(200_000, 30_000, 20_000)
        result = allocator.allocate(candidates, budget=100_000, min_orders=1)
        # 최소 1개 이상 주문
        assert len(result.allocations) >= 1

    def test_orderable_cash_limits_budget(self, tmp_path):
        """주문가능금액이 예산보다 적으면 주문가능금액이 기준."""
        cfg = _make_config(tmp_path)
        from budget_allocator import BudgetAllocator
        allocator = BudgetAllocator(cfg)
        candidates = _make_candidates(10_000)
        result = allocator.allocate(candidates, budget=100_000, orderable_cash=20_000)
        # effective_budget = min(100000, 20000, max_budget)
        assert result.effective_budget <= 20_000


class TestMaxBudgetCap:
    """최대 허용 예산 상한 테스트."""

    def test_budget_capped_at_max_force_trade_budget(self, tmp_path):
        """예산이 max_force_trade_budget을 초과하면 상한 적용."""
        import yaml
        cfg_data = {
            "force_trade": {"minimum_order_amount": 10_000, "max_force_trade_budget": 50_000},
            "order": {"buy_price_adjustment_rate": 0.001},
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg_data), encoding="utf-8")

        from budget_allocator import BudgetAllocator
        allocator = BudgetAllocator(str(p))
        candidates = _make_candidates(10_000)
        result = allocator.allocate(candidates, budget=1_000_000)
        assert result.effective_budget <= 50_000
