"""log_service 테스트."""

import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "services"))


class TestEmergencyStop:
    def test_activate_creates_file(self, tmp_path, monkeypatch):
        """긴급중단 활성화 → 파일 생성."""
        import log_service
        stop_file = tmp_path / "EMERGENCY_STOP"
        monkeypatch.setattr(log_service, "EMERGENCY_STOP_FILE", stop_file)
        r = log_service.activate_emergency_stop()
        assert r["success"]
        assert stop_file.exists()

    def test_deactivate_removes_file(self, tmp_path, monkeypatch):
        """긴급중단 해제 → 파일 삭제."""
        import log_service
        stop_file = tmp_path / "EMERGENCY_STOP"
        stop_file.write_text("stop", encoding="utf-8")
        monkeypatch.setattr(log_service, "EMERGENCY_STOP_FILE", stop_file)
        r = log_service.deactivate_emergency_stop()
        assert r["success"]
        assert not stop_file.exists()

    def test_is_active_true_when_file_exists(self, tmp_path, monkeypatch):
        """파일 있으면 활성 상태."""
        import log_service
        stop_file = tmp_path / "EMERGENCY_STOP"
        stop_file.write_text("stop", encoding="utf-8")
        monkeypatch.setattr(log_service, "EMERGENCY_STOP_FILE", stop_file)
        assert log_service.is_emergency_stop_active() is True

    def test_is_active_false_when_no_file(self, tmp_path, monkeypatch):
        """파일 없으면 비활성 상태."""
        import log_service
        stop_file = tmp_path / "EMERGENCY_STOP"
        monkeypatch.setattr(log_service, "EMERGENCY_STOP_FILE", stop_file)
        assert log_service.is_emergency_stop_active() is False


class TestLogRead:
    def test_read_last_n_lines(self, tmp_path, monkeypatch):
        """최근 N줄만 읽기."""
        import log_service
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / "test.log"
        log_file.write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
        monkeypatch.setattr(log_service, "LOGS_DIR", log_dir)
        text = log_service.read_log("test.log", last_n=10)
        lines = text.strip().splitlines()
        assert len(lines) == 10
        assert "line99" in text

    def test_missing_log_returns_message(self, tmp_path, monkeypatch):
        """없는 파일이면 안내 메시지 반환."""
        import log_service
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(log_service, "LOGS_DIR", log_dir)
        text = log_service.read_log("nonexistent.log")
        assert "없음" in text or "nonexistent" in text
