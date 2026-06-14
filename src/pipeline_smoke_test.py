"""파이프라인 스모크 테스트 — 폴더, 쓰기, 패키지, 환경변수 확인.

CLI:
    python src/pipeline_smoke_test.py

출력: JSON only (파싱 가능).
"""

import json
import os
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SRC_DIR.parent

sys.path.insert(0, str(_SRC_DIR))

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
    success = True

    def record(name: str, ok: bool, message: str = "") -> None:
        nonlocal success
        checks.append({"name": name, "ok": ok, "message": message})
        if not ok:
            success = False

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

    # 5. 환경변수 확인
    for key in ENV_KEYS:
        present = bool(os.environ.get(key))
        record(f"env:{key}", present, "present" if present else "missing")

    # 6. src/utils.py import 확인
    try:
        from utils import load_config, setup_logger, get_today_str  # noqa: F401
        record("import:src.utils", True)
    except Exception as ex:
        record("import:src.utils", False, str(ex))

    result = {
        "success": success,
        "project_root": str(PROJECT_ROOT),
        "python": sys.executable,
        "checks": checks,
        "failed_checks": [c for c in checks if not c["ok"]],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
