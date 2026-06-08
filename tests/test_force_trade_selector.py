"""ForceTradeSelector 테스트."""

import os
import sys
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_config(tmp_path, **overrides):
    import yaml
    cfg = {
        "force_trade": {
            "enabled": True,
            "force_buy_top_n": 20,
            "allow_filter_relaxation": True,
            "hard_exclusions": {
                "exclude_halted_stock": True,
                "exclude_management_stock": True,
                "exclude_warning_stock": True,
                "exclude_preferred_stock": True,
                "exclude_spac": True,
                "exclude_etf_etn": True,
                "min_price": 1000,
            },
        },
        "risk": {
            "min_daily_trading_value": 3_000_000_000,
            "min_avg_20d_trading_value": 5_000_000_000,
            "relaxed_min_daily_trading_value": 500_000_000,
            "relaxed_min_avg_20d_trading_value": 1_000_000_000,
        },
        "paths": {"predictions_dir": str(tmp_path / "predictions")},
    }
    cfg.update(overrides)
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return str(p)


def _make_predictions_csv(tmp_path, rows, date_str="20260608"):
    import pandas as pd
    pred_dir = tmp_path / "predictions"
    pred_dir.mkdir(exist_ok=True)
    df = pd.DataFrame(rows)
    path = pred_dir / f"top20_{date_str}.csv"
    df.to_csv(path, index=False)
    return str(path)


class TestNormalFilter:
    """기본 필터에서 후보가 있으면 그대로 선정."""

    def test_normal_filter_selects_valid_candidates(self, tmp_path):
        cfg = _make_config(tmp_path)
        rows = [
            {"ticker": "005930", "name": "삼성전자", "close": 70000,
             "trading_value": 5_000_000_000, "avg_20d_trading_value": 6_000_000_000,
             "prediction_score": 0.8},
            {"ticker": "000660", "name": "SK하이닉스", "close": 150000,
             "trading_value": 4_000_000_000, "avg_20d_trading_value": 7_000_000_000,
             "prediction_score": 0.7},
        ]
        _make_predictions_csv(tmp_path, rows)

        from force_trade_selector import ForceTradeSelector
        selector = ForceTradeSelector(cfg)
        result = selector.select(date_str="20260608", min_candidates=1)
        assert len(result) >= 1
        assert result.iloc[0]["selected_by_step"] == "normal_filters"


class TestRelaxedFilter:
    """기본 필터에서 후보가 없으면 relaxed filter 적용."""

    def test_falls_back_to_relaxed_when_normal_fails(self, tmp_path):
        cfg = _make_config(tmp_path)
        rows = [
            {"ticker": "005930", "name": "삼성전자", "close": 70000,
             "trading_value": 600_000_000,  # 기본 기준 미달, relaxed 기준 충족
             "avg_20d_trading_value": 1_200_000_000,
             "prediction_score": 0.8},
        ]
        _make_predictions_csv(tmp_path, rows)

        from force_trade_selector import ForceTradeSelector
        selector = ForceTradeSelector(cfg)
        result = selector.select(date_str="20260608", min_candidates=1)
        assert len(result) >= 1
        assert result.iloc[0]["selected_by_step"] in (
            "relax_trading_value", "relax_volatility",
            "score_only_with_hard_exclusions", "emergency_top_n",
        )


class TestHardExclusion:
    """hard exclusion 종목은 절대 선정되지 않음."""

    def test_halted_stock_never_selected(self, tmp_path):
        cfg = _make_config(tmp_path)
        rows = [
            {"ticker": "005930", "name": "삼성전자", "close": 70000,
             "is_halted": True, "trading_value": 5_000_000_000,
             "avg_20d_trading_value": 6_000_000_000, "prediction_score": 0.99},
        ]
        _make_predictions_csv(tmp_path, rows)

        from force_trade_selector import ForceTradeSelector, ForceTradeSelectorError
        selector = ForceTradeSelector(cfg)
        with pytest.raises(ForceTradeSelectorError):
            selector.select(date_str="20260608", min_candidates=1)

    def test_preferred_stock_never_selected(self, tmp_path):
        cfg = _make_config(tmp_path)
        rows = [
            {"ticker": "005935", "name": "삼성전자우", "close": 60000,
             "trading_value": 5_000_000_000, "avg_20d_trading_value": 6_000_000_000,
             "prediction_score": 0.99},
        ]
        _make_predictions_csv(tmp_path, rows)

        from force_trade_selector import ForceTradeSelector, ForceTradeSelectorError
        selector = ForceTradeSelector(cfg)
        with pytest.raises(ForceTradeSelectorError):
            selector.select(date_str="20260608", min_candidates=1)

    def test_below_min_price_never_selected(self, tmp_path):
        cfg = _make_config(tmp_path)
        rows = [
            {"ticker": "999999", "name": "저가주", "close": 500,
             "trading_value": 5_000_000_000, "avg_20d_trading_value": 6_000_000_000,
             "prediction_score": 0.99},
        ]
        _make_predictions_csv(tmp_path, rows)

        from force_trade_selector import ForceTradeSelector, ForceTradeSelectorError
        selector = ForceTradeSelector(cfg)
        with pytest.raises(ForceTradeSelectorError):
            selector.select(date_str="20260608", min_candidates=1)

    def test_spac_never_selected(self, tmp_path):
        cfg = _make_config(tmp_path)
        rows = [
            {"ticker": "123456", "name": "한국스팩5호", "close": 2000,
             "trading_value": 5_000_000_000, "avg_20d_trading_value": 6_000_000_000,
             "prediction_score": 0.99},
        ]
        _make_predictions_csv(tmp_path, rows)

        from force_trade_selector import ForceTradeSelector, ForceTradeSelectorError
        selector = ForceTradeSelector(cfg)
        with pytest.raises(ForceTradeSelectorError):
            selector.select(date_str="20260608", min_candidates=1)


class TestMinCandidates:
    """최소 후보 수 반환 테스트."""

    def test_returns_at_least_min_candidates(self, tmp_path):
        cfg = _make_config(tmp_path)
        rows = [
            {"ticker": f"00{i:04d}", "name": f"종목{i}", "close": 10000 + i * 100,
             "trading_value": 5_000_000_000, "avg_20d_trading_value": 6_000_000_000,
             "prediction_score": 0.5 + i * 0.01}
            for i in range(5)
        ]
        _make_predictions_csv(tmp_path, rows)

        from force_trade_selector import ForceTradeSelector
        selector = ForceTradeSelector(cfg)
        result = selector.select(date_str="20260608", min_candidates=1)
        assert len(result) >= 1
