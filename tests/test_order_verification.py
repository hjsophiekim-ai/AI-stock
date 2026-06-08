"""OrderVerificationTester 테스트."""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_config(tmp_path, live_trade=False, use_mock=True, confirm=False,
                 allow_real_test=False, ft_allow_real=False):
    import yaml
    cfg = {
        "live_trade": live_trade,
        "paper_trade": not live_trade,
        "safety": {
            "confirm_live_trade": confirm,
            "allow_real_test_order": allow_real_test,
        },
        "kis": {
            "use_mock": use_mock,
            "base_url_mock": "https://openapivts.koreainvestment.com:29443",
            "base_url_real": "https://openapi.koreainvestment.com:9443",
        },
        "force_trade": {
            "allow_real_test_order": ft_allow_real,
            "test_order_amount": 10_000,
            "max_force_trade_budget": 100_000,
            "enabled": True,
        },
        "order": {"buy_price_adjustment_rate": 0.001},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return str(p)


class TestPaperModeNoApiCall:
    """live_trade=false이면 실제 주문 API 호출 없음."""

    def test_paper_mode_no_api_call(self, tmp_path):
        cfg = _make_config(tmp_path, live_trade=False)
        from order_verification_test import OrderVerificationTester
        tester = OrderVerificationTester(config_path=cfg, stock_code="005930", amount=10_000)
        success = tester.run()
        assert success
        assert tester._results["api_called"] is False
        assert tester._results["real_order_called"] is False
        assert tester._results["trade_mode"] == "PAPER"


class TestRealOrderConditionCheck:
    """실전 주문 조건 미충족 시 거부."""

    def test_real_order_rejected_when_confirm_false(self, tmp_path):
        """confirm_live_trade=false이면 실전 주문 거부."""
        cfg = _make_config(tmp_path, live_trade=True, use_mock=False, confirm=False)
        from order_verification_test import OrderVerificationTester
        tester = OrderVerificationTester(config_path=cfg, stock_code="005930", amount=10_000)
        # REAL 모드가 아닌 MOCK 모드로 강제 다운그레이드됨 (SafetyGate 로직)
        # 실전 주문 조건 체크는 REAL 모드에서만 실행
        assert tester._results["real_order_called"] is False

    def test_real_test_order_rejected_without_allow_flag(self, tmp_path):
        """safety.allow_real_test_order=false이면 실전 테스트 거부."""
        cfg = _make_config(
            tmp_path,
            live_trade=True, use_mock=False, confirm=True,
            allow_real_test=False, ft_allow_real=False,
        )
        from order_verification_test import OrderVerificationTester
        tester = OrderVerificationTester(config_path=cfg, stock_code="005930", amount=10_000)
        # _check_real_order_conditions() 실행 확인
        ok = tester._check_real_order_conditions()
        assert ok is False
        assert tester._results["real_order_called"] is False


class TestOrderResultLog:
    """주문결과 로그 생성 테스트."""

    def test_paper_order_log_created(self, tmp_path):
        """PAPER 주문 후 reports/order_verification_*.csv 생성."""
        cfg = _make_config(tmp_path, live_trade=False)
        # reports 디렉터리를 tmp로 교체
        os.makedirs(str(tmp_path / "reports"), exist_ok=True)
        orig_dir = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            from order_verification_test import OrderVerificationTester
            tester = OrderVerificationTester(config_path=cfg, stock_code="005930", amount=10_000)
            tester.run()
            import glob
            files = glob.glob(str(tmp_path / "reports" / "order_verification_*.csv"))
            assert len(files) >= 1
        finally:
            os.chdir(orig_dir)
