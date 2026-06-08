"""SafetyGate 모드 판별 테스트."""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from safety_gate import SafetyGate, TRADE_MODE_PAPER, TRADE_MODE_MOCK, TRADE_MODE_REAL


def _make_gate(live_trade: bool, use_mock: bool, confirm_live: bool, tmp_path) -> SafetyGate:
    """임시 config.yaml을 생성해 SafetyGate를 만드는 헬퍼."""
    import yaml

    cfg = {
        "live_trade": live_trade,
        "paper_trade": not live_trade,
        "safety": {"confirm_live_trade": confirm_live},
        "kis": {"use_mock": use_mock},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return SafetyGate(str(p))


class TestSafetyGateModes:
    """SafetyGate 모드 결정 로직 테스트."""

    def test_paper_mode_when_live_trade_false(self, tmp_path):
        """live_trade=false이면 use_mock·confirm 무관하게 PAPER 모드."""
        gate = _make_gate(live_trade=False, use_mock=False, confirm_live=True, tmp_path=tmp_path)
        assert gate.mode == TRADE_MODE_PAPER

    def test_paper_mode_default(self, tmp_path):
        """live_trade=false, use_mock=true이면 여전히 PAPER 모드."""
        gate = _make_gate(live_trade=False, use_mock=True, confirm_live=False, tmp_path=tmp_path)
        assert gate.mode == TRADE_MODE_PAPER

    def test_mock_mode_when_live_true_use_mock_true(self, tmp_path):
        """live_trade=true, use_mock=true이면 MOCK 모드."""
        gate = _make_gate(live_trade=True, use_mock=True, confirm_live=False, tmp_path=tmp_path)
        assert gate.mode == TRADE_MODE_MOCK

    def test_real_mode_requires_all_three(self, tmp_path):
        """REAL 모드: live_trade=true + use_mock=false + confirm=true 세 가지 모두 필요."""
        gate = _make_gate(live_trade=True, use_mock=False, confirm_live=True, tmp_path=tmp_path)
        assert gate.mode == TRADE_MODE_REAL

    def test_not_real_when_confirm_false(self, tmp_path):
        """confirm_live_trade=false이면 REAL 모드 불가 — MOCK 또는 PAPER."""
        gate = _make_gate(live_trade=True, use_mock=False, confirm_live=False, tmp_path=tmp_path)
        assert gate.mode != TRADE_MODE_REAL


class TestSafetyGateRealOrderBlock:
    """REAL 모드가 아닐 때 실전 주문 호출 차단 테스트."""

    def test_assert_real_raises_in_paper_mode(self, tmp_path):
        """PAPER 모드에서 실전 주문 시도 시 RuntimeError 발생."""
        gate = _make_gate(live_trade=False, use_mock=True, confirm_live=False, tmp_path=tmp_path)
        with pytest.raises(RuntimeError):
            gate.assert_can_place_real_order()

    def test_assert_real_raises_in_mock_mode(self, tmp_path):
        """MOCK 모드에서 실전 주문 시도 시 RuntimeError 발생."""
        gate = _make_gate(live_trade=True, use_mock=True, confirm_live=False, tmp_path=tmp_path)
        with pytest.raises(RuntimeError):
            gate.assert_can_place_real_order()

    def test_assert_real_passes_in_real_mode(self, tmp_path):
        """REAL 모드에서는 실전 주문 승인 — 예외 없음."""
        gate = _make_gate(live_trade=True, use_mock=False, confirm_live=True, tmp_path=tmp_path)
        gate.assert_can_place_real_order()  # 예외 없어야 함

    def test_is_paper(self, tmp_path):
        """is_paper() 확인."""
        gate = _make_gate(live_trade=False, use_mock=False, confirm_live=False, tmp_path=tmp_path)
        assert gate.is_paper() is True
        assert gate.is_mock_allowed() is False
        assert gate.is_real_allowed() is False

    def test_is_mock(self, tmp_path):
        """MOCK 모드: is_mock_allowed()=True, is_real_allowed()=False."""
        gate = _make_gate(live_trade=True, use_mock=True, confirm_live=False, tmp_path=tmp_path)
        assert gate.is_paper() is False
        assert gate.is_mock_allowed() is True  # MOCK 모드: API 호출 허용
        assert gate.is_real_allowed() is False

    def test_is_real(self, tmp_path):
        """REAL 모드: is_mock_allowed()=True (API 호출 허용), is_real_allowed()=True."""
        gate = _make_gate(live_trade=True, use_mock=False, confirm_live=True, tmp_path=tmp_path)
        assert gate.is_paper() is False
        assert gate.is_mock_allowed() is True  # REAL 모드도 API 호출 허용
        assert gate.is_real_allowed() is True


class TestSafetyGateAccountMask:
    """계좌번호 마스킹 테스트."""

    def test_mask_account_no(self, tmp_path):
        """계좌번호가 마스킹되어야 함."""
        gate = _make_gate(live_trade=False, use_mock=True, confirm_live=False, tmp_path=tmp_path)
        masked = gate.mask_account_no("12345678901")
        assert "12345678901" not in masked
        assert "***" in masked or "*" in masked
