"""로그 파일 읽기 및 긴급중단 관리 서비스."""

import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

EMERGENCY_STOP_FILE = PROJECT_ROOT / "data" / "EMERGENCY_STOP"
LOGS_DIR = PROJECT_ROOT / "logs"


def _log_path(name: str) -> Path:
    return LOGS_DIR / name


def read_log(filename: str, last_n: int = 200) -> str:
    """로그 파일에서 마지막 N줄 읽기."""
    p = _log_path(filename)
    if not p.exists():
        return f"(파일 없음: {filename})"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-last_n:])
    except Exception as e:
        return f"(읽기 오류: {e})"


def read_report(path: Path, last_n: int = 500) -> str:
    """임의 경로의 텍스트 파일 읽기."""
    if not path.exists():
        return f"(파일 없음: {path.name})"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-last_n:])
    except Exception as e:
        return f"(읽기 오류: {e})"


def read_no_trade_analysis(date_str: Optional[str] = None) -> str:
    """no_trade_analysis 보고서 읽기."""
    ds = date_str or datetime.now().strftime("%Y%m%d")
    p = PROJECT_ROOT / "reports" / f"no_trade_analysis_{ds}.txt"
    return read_report(p)


def is_emergency_stop_active() -> bool:
    """긴급중단 파일 존재 여부."""
    return EMERGENCY_STOP_FILE.exists()


def activate_emergency_stop() -> Dict:
    """긴급중단 활성화 (파일 생성)."""
    try:
        EMERGENCY_STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        EMERGENCY_STOP_FILE.write_text(
            f"EMERGENCY STOP activated at {datetime.now().isoformat()}\n",
            encoding="utf-8"
        )
        return {"success": True, "message": "긴급중단 활성화 완료"}
    except Exception as e:
        return {"success": False, "message": f"긴급중단 활성화 오류: {e}"}


def deactivate_emergency_stop() -> Dict:
    """긴급중단 해제 (파일 삭제)."""
    try:
        if EMERGENCY_STOP_FILE.exists():
            EMERGENCY_STOP_FILE.unlink()
        return {"success": True, "message": "긴급중단 해제 완료"}
    except Exception as e:
        return {"success": False, "message": f"긴급중단 해제 오류: {e}"}


def list_available_logs() -> List[str]:
    """존재하는 로그 파일 목록."""
    if not LOGS_DIR.exists():
        return []
    return [p.name for p in sorted(LOGS_DIR.glob("*.log"))]


def get_log_text_for_download(filename: str) -> str:
    """다운로드용 전체 로그 텍스트."""
    p = _log_path(filename)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
