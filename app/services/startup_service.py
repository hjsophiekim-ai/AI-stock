"""앱 시작 시 실행되는 초기화 서비스 — 디렉토리 생성, commit hash 조회, Render 환경 정보."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).parent.parent.parent

REQUIRED_DIRS = [
    "data",
    "data/raw",
    "data/processed",
    "data/models",
    "models",
    "reports",
    "reports/predictions",
    "reports/orders",
    "reports/orders/mock",
    "reports/orders/real",
    "reports/order_plans",
    "logs",
]


def ensure_dirs() -> List[str]:
    """필수 디렉토리를 생성하고 새로 생성된 목록을 반환."""
    created = []
    for rel in REQUIRED_DIRS:
        d = PROJECT_ROOT / rel
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(rel)
    return created


def ensure_runtime_directories() -> List[str]:
    """ensure_dirs() 별칭 — pipeline_service 등에서 호출."""
    return ensure_dirs()


def get_commit_hash() -> str:
    """현재 commit hash 반환 (짧은 형식). 실패 시 UNKNOWN.

    우선순위:
    1. RENDER_GIT_COMMIT 환경변수 (Render 배포 시 자동 설정)
    2. git rev-parse --short HEAD 명령
    3. UNKNOWN
    """
    h = os.environ.get("RENDER_GIT_COMMIT", "").strip()
    if h:
        return h[:8]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "UNKNOWN"


def get_render_info() -> Dict:
    """Render 환경 정보 반환."""
    return {
        "is_render": bool(os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_URL")),
        "render_service_name": os.environ.get("RENDER_SERVICE_NAME", ""),
        "render_external_url": os.environ.get("RENDER_EXTERNAL_URL", ""),
        "render_git_commit": os.environ.get("RENDER_GIT_COMMIT", ""),
        "render_git_branch": os.environ.get("RENDER_GIT_BRANCH", ""),
    }
