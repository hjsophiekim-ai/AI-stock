"""NoTradeAnalyzer 테스트."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_config(tmp_path, live_trade=False, ft_enabled=False):
    import yaml
    cfg = {
        "live_trade": live_trade,
        "paper_trade": not live_trade,
        "force_trade": {"enabled": ft_enabled},
        "risk": {
            "min_daily_trading_value": 3_000_000_000,
            "min_avg_20d_trading_value": 5_000_000_000,
            "min_price": 1000,
        },
        "kis": {"use_mock": True},
        "safety": {"confirm_live_trade": False},
        "paths": {"predictions_dir": str(tmp_path / "predictions")},
        "logging": {
            "api_log_file": str(tmp_path / "logs" / "api.log"),
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return str(p)


class TestNoTradeAnalyzerReport:
    """거래 0건 분석 보고서 생성 테스트."""

    def test_report_generated(self, tmp_path):
        """분석 실행 후 reports/no_trade_analysis_*.txt 생성."""
        cfg = _make_config(tmp_path)
        orig = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            os.makedirs("reports", exist_ok=True)
            os.makedirs("logs", exist_ok=True)
            from no_trade_analyzer import NoTradeAnalyzer
            analyzer = NoTradeAnalyzer(cfg)
            path = analyzer.analyze("20260608")
            assert os.path.exists(path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "거래 0건 원인 분석" in content
        finally:
            os.chdir(orig)

    def test_disabled_live_trade_detected(self, tmp_path):
        """live_trade=false 감지 확인."""
        cfg = _make_config(tmp_path, live_trade=False)
        orig = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            os.makedirs("reports", exist_ok=True)
            os.makedirs("logs", exist_ok=True)
            from no_trade_analyzer import NoTradeAnalyzer
            analyzer = NoTradeAnalyzer(cfg)
            analyzer.analyze("20260608")
            findings = analyzer._findings
            live_trade_finding = next(
                (f for f in findings if "live_trade" in f["message"].lower() or "live_trade" in f["category"]),
                None
            )
            assert live_trade_finding is not None
        finally:
            os.chdir(orig)

    def test_no_prediction_file_detected(self, tmp_path):
        """예측 파일 없음 감지 확인."""
        cfg = _make_config(tmp_path)
        orig = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            os.makedirs("reports", exist_ok=True)
            os.makedirs("logs", exist_ok=True)
            (tmp_path / "predictions").mkdir(exist_ok=True)
            from no_trade_analyzer import NoTradeAnalyzer
            analyzer = NoTradeAnalyzer(cfg)
            analyzer.analyze("20260608")
            findings = analyzer._findings
            pred_finding = next(
                (f for f in findings if "예측" in f["category"] or "predictions" in f["category"].lower()),
                None
            )
            assert pred_finding is not None
            assert "조치 필요" in pred_finding["status"] or "없음" in pred_finding["message"]
        finally:
            os.chdir(orig)

    def test_force_trade_disabled_detected(self, tmp_path):
        """force_trade.enabled=false 감지 확인."""
        cfg = _make_config(tmp_path, ft_enabled=False)
        orig = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            os.makedirs("reports", exist_ok=True)
            os.makedirs("logs", exist_ok=True)
            from no_trade_analyzer import NoTradeAnalyzer
            analyzer = NoTradeAnalyzer(cfg)
            analyzer.analyze("20260608")
            findings = analyzer._findings
            ft_finding = next(
                (f for f in findings if "force_trade" in f["category"].lower() or "거래보장" in f["category"]),
                None
            )
            assert ft_finding is not None
        finally:
            os.chdir(orig)
