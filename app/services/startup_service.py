"""앱 시작 시 실행되는 초기화 서비스 — 환경변수 로딩, 디렉토리 생성, commit hash 조회, Render 환경 정보."""

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


def initialize_app_environment() -> Dict:
    """.env 로딩 + 필수 디렉토리 생성을 한 번에 처리.

    로컬:  PROJECT_ROOT/.env 를 load_dotenv로 os.environ에 주입
    Render: .env 파일 없어도 Render Dashboard의 Environment Variables를
            os.environ에서 직접 읽음 (이미 설정되어 있음)
    반환: {"env_loaded": bool, "dirs_created": [...], "env_file_exists": bool}
    """
    env_file = PROJECT_ROOT / ".env"
    env_loaded = False

    # .env 파일이 있으면 dotenv로 로딩 (os.environ에 아직 없는 값만 주입)
    if env_file.exists():
        try:
            from dotenv import load_dotenv as _load_dotenv
            _load_dotenv(env_file, override=False)
            env_loaded = True
        except Exception:
            pass
        # dotenv 미설치 환경 fallback — 직접 파싱
        if not env_loaded:
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if key and val.strip() and key not in os.environ:
                        os.environ[key] = val.strip()
                env_loaded = True
            except Exception:
                pass

    dirs_created = ensure_dirs()

    return {
        "env_file_exists": env_file.exists(),
        "env_loaded": env_loaded,
        "dirs_created": dirs_created,
        "running_on_render": bool(os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_URL")),
    }


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
