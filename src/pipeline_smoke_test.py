"""파이프라인 스모크 테스트 — 폴더, 쓰기, 패키지, 환경변수 확인.

CLI:
    python src/pipeline_smoke_test.py

출력: JSON only (파싱 가능).
주의: API 키 원문은 절대 출력하지 않습니다.
"""

import json
import os
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SRC_DIR.parent
ENV_PATH = PROJECT_ROOT / ".env"

sys.path.insert(0, str(_SRC_DIR))

# .env를 os.environ에 주입 (로컬 환경 지원)
# Render에서는 .env 파일이 없어도 Dashboard의 환경변수가 이미 os.environ에 있음
_env_file_exists = ENV_PATH.exists()
_env_loaded = False

if _env_file_exists:
    try:
        from dotenv import load_dotenv as _ld
        _ld(ENV_PATH, override=False)
        _env_loaded = True
    except Exception:
        pass
    # dotenv 미설치 시 직접 파싱
    if not _env_loaded:
        try:
            for _line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _, _v = _line.partition("=")
                _k = _k.strip()
                if _k and _v.strip() and _k not in os.environ:
                    os.environ[_k] = _v.strip()
            _env_loaded = True
        except Exception:
            pass

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

REQUIRED_PACKAGES = [
    "pandas",
    "yaml",
    "sklearn",
    "joblib",
]

ENV_KEYS = [
    "DART_API_KEY",
    "KIS_MOCK_APP_KEY",
    "KIS_REAL_APP_KEY",
]


def main() -> None:
    checks = []
    overall_success = True

    def record(name: str, ok: bool, message: str = "") -> None:
        nonlocal overall_success
        checks.append({"name": name, "ok": ok, "message": message})
        if not ok:
            overall_success = False

    # 1. 필수 폴더 생성 및 확인
    for rel in REQUIRED_DIRS:
        p = PROJECT_ROOT / rel
        try:
            p.mkdir(parents=True, exist_ok=True)
            record(f"dir:{rel}", True, str(p))
        except Exception as ex:
            record(f"dir:{rel}", False, str(ex))

    # 2. reports/predictions 쓰기 테스트
    test_file = PROJECT_ROOT / "reports" / "predictions" / "_smoke_test_tmp.csv"
    try:
        test_file.write_text("a,b\n1,2\n", encoding="utf-8")
        test_file.unlink()
        record("write:reports/predictions", True)
    except Exception as ex:
        record("write:reports/predictions", False, str(ex))

    # 3. 필수 패키지 import 확인
    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
            record(f"import:{pkg}", True)
        except ImportError as ex:
            record(f"import:{pkg}", False, str(ex))

    # 4. config.yaml 읽기 확인
    config_path = PROJECT_ROOT / "config.yaml"
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            yaml.safe_load(f)
        record("config:config.yaml", True, str(config_path))
    except Exception as ex:
        record("config:config.yaml", False, str(ex))

    # 5. 환경변수 확인 (원문 절대 출력 금지)
    for key in ENV_KEYS:
        present = bool(os.environ.get(key))
        record(f"env:{key}", present, "present" if present else "missing")

    # 6. src/utils.py import 확인
    try:
        from utils import load_config, setup_logger, get_today_str  # noqa: F401
        record("import:src.utils", True)
    except Exception as ex:
        record("import:src.utils", False, str(ex))

    # 7. pipeline_service import 확인
    try:
        _app_svc = PROJECT_ROOT / "app" / "services"
        sys.path.insert(0, str(_app_svc))
        from pipeline_service import run_full_pipeline, run_pipeline_step, ensure_runtime_directories  # noqa: F401
        record("import:pipeline_service", True)
    except Exception as ex:
        record("import:pipeline_service", False, str(ex))

    missing_env_keys = [k for k in ENV_KEYS if not os.environ.get(k)]

    result = {
        "success": overall_success,
        "project_root": str(PROJECT_ROOT),
        "python": sys.executable,
        "env_file_exists": _env_file_exists,
        "env_path": str(ENV_PATH),
        "env_loaded": _env_loaded,
        "running_on_render": bool(
            os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_URL")
        ),
        "missing_env_keys": missing_env_keys,
        "checks": checks,
        "failed_checks": [c for c in checks if not c["ok"]],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
