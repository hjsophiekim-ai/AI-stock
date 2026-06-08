"""config_service 테스트."""

import sys
import pytest
from pathlib import Path
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "services"))


def _make_config(tmp_path, **overrides):
    base = {
        "live_trade": False,
        "paper_trade": True,
        "kis": {"use_mock": True},
        "safety": {"confirm_live_trade": False},
        "force_trade": {"enabled": False, "allow_real_test_order": False},
    }
    base.update(overrides)
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(base), encoding="utf-8")
    return p


class TestTradeModeCalc:
    def test_paper_when_live_false(self, tmp_path, monkeypatch):
        """live_trade=false → PAPER."""
        import config_service
        p = _make_config(tmp_path, live_trade=False)
        monkeypatch.setattr(config_service, "CONFIG_PATH", p)
        assert config_service.get_trade_mode() == "PAPER"

    def test_mock_when_live_true_use_mock_true(self, tmp_path, monkeypatch):
        """live_trade=true, use_mock=true → MOCK."""
        import config_service
        p = _make_config(tmp_path, live_trade=True, kis={"use_mock": True})
        monkeypatch.setattr(config_service, "CONFIG_PATH", p)
        assert config_service.get_trade_mode() == "MOCK"

    def test_real_when_all_three(self, tmp_path, monkeypatch):
        """live_trade=true, use_mock=false, confirm=true → REAL."""
        import config_service
        p = _make_config(tmp_path, live_trade=True,
                         kis={"use_mock": False},
                         safety={"confirm_live_trade": True})
        monkeypatch.setattr(config_service, "CONFIG_PATH", p)
        assert config_service.get_trade_mode() == "REAL"

    def test_mock_when_confirm_missing(self, tmp_path, monkeypatch):
        """live_trade=true, use_mock=false, confirm=false → MOCK (다운그레이드)."""
        import config_service
        p = _make_config(tmp_path, live_trade=True,
                         kis={"use_mock": False},
                         safety={"confirm_live_trade": False})
        monkeypatch.setattr(config_service, "CONFIG_PATH", p)
        assert config_service.get_trade_mode() == "MOCK"


class TestNestedUpdate:
    def test_nested_update(self, tmp_path, monkeypatch):
        """nested_update로 중첩 키 수정."""
        import config_service
        cfg = {"kis": {"use_mock": True}, "live_trade": False}
        updated = config_service.nested_update(cfg, "kis.use_mock", False)
        assert updated["kis"]["use_mock"] is False

    def test_nested_update_creates_missing_key(self):
        """없는 키도 자동 생성."""
        import config_service
        cfg = {}
        result = config_service.nested_update(cfg, "a.b.c", 42)
        assert result["a"]["b"]["c"] == 42


class TestRealModeConditions:
    def test_real_conditions_all_false_by_default(self, tmp_path, monkeypatch):
        """기본 설정에서 REAL 조건 모두 미충족."""
        import config_service
        p = _make_config(tmp_path)
        monkeypatch.setattr(config_service, "CONFIG_PATH", p)
        conds = config_service.can_enable_real_mode()
        assert not any(conds.values())
