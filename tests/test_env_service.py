"""env_service 테스트."""

import os
import sys
import pytest
from pathlib import Path

# app/services를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "services"))


def _make_env_file(tmp_path, content: str):
    p = tmp_path / ".env"
    p.write_text(content, encoding="utf-8")
    return p


class TestEnvReadWrite:
    def test_load_env_empty(self, tmp_path, monkeypatch):
        """빈 .env 파일 로드."""
        import env_service
        monkeypatch.setattr(env_service, "ENV_PATH", tmp_path / ".env")
        result = env_service.load_env()
        assert result == {}

    def test_save_and_load(self, tmp_path, monkeypatch):
        """.env 저장 후 다시 읽기."""
        import env_service
        monkeypatch.setattr(env_service, "ENV_PATH", tmp_path / ".env")
        env_service.save_env({"KIS_APP_KEY": "testkey123", "KIS_MOCK_ACCOUNT_NO": "50123456"})
        loaded = env_service.load_env()
        assert loaded["KIS_APP_KEY"] == "testkey123"
        assert loaded["KIS_MOCK_ACCOUNT_NO"] == "50123456"

    def test_save_mock_keys(self, tmp_path, monkeypatch):
        """모의투자 키 저장."""
        import env_service
        monkeypatch.setattr(env_service, "ENV_PATH", tmp_path / ".env")
        env_service.save_mock_keys("mockkey", "mocksecret", "50999999", "01")
        loaded = env_service.load_env()
        assert loaded["KIS_APP_KEY"] == "mockkey"
        assert loaded["KIS_MOCK_ACCOUNT_NO"] == "50999999"

    def test_save_real_keys(self, tmp_path, monkeypatch):
        """실전투자 키 저장."""
        import env_service
        monkeypatch.setattr(env_service, "ENV_PATH", tmp_path / ".env")
        env_service.save_real_keys("realkey", "realsecret", "80999999", "01")
        loaded = env_service.load_env()
        assert loaded["KIS_REAL_APP_KEY"] == "realkey"
        assert loaded["KIS_ACCOUNT_NO"] == "80999999"


class TestMasking:
    def test_mask_short_value(self):
        """짧은 값 마스킹."""
        import env_service
        assert env_service.mask("abc") == "***"

    def test_mask_long_value(self):
        """긴 값은 앞 4자리만 표시."""
        import env_service
        result = env_service.mask("abcdefghij", show=4)
        assert result.startswith("abcd")
        assert "*" in result

    def test_masked_env_hides_secret(self, tmp_path, monkeypatch):
        """마스킹된 env에서 시크릿은 완전 숨김."""
        import env_service
        monkeypatch.setattr(env_service, "ENV_PATH", tmp_path / ".env")
        env_service.save_env({"KIS_APP_SECRET": "supersecret123"})
        masked = env_service.get_masked_env()
        assert "supersecret" not in masked.get("KIS_APP_SECRET", "")


class TestKeyChecks:
    def test_mock_keys_missing_returns_false(self, tmp_path, monkeypatch):
        """모의투자 키 없으면 False 반환."""
        import env_service
        monkeypatch.setattr(env_service, "ENV_PATH", tmp_path / ".env")
        checks = env_service.check_mock_keys()
        assert not any(checks.values())

    def test_real_keys_partial_false(self, tmp_path, monkeypatch):
        """일부 실전 키만 있으면 부분 False."""
        import env_service
        monkeypatch.setattr(env_service, "ENV_PATH", tmp_path / ".env")
        env_service.save_env({"KIS_REAL_APP_KEY": "k"})
        checks = env_service.check_real_keys()
        assert checks["KIS_REAL_APP_KEY"] is True
        assert checks["KIS_ACCOUNT_NO"] is False
