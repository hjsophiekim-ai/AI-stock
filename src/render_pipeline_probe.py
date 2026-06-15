"""Render 파이프라인 Probe — 환경, 디렉토리, 단계별 실행 가능성 진단.

CLI:
    python src/render_pipeline_probe.py

출력: JSON (파싱 가능).
API 키 원문은 출력하지 않습니다.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))


ENV_KEYS_REQUIRED = [
    "KIS_MOCK_APP_KEY",
    "KIS_MOCK_APP_SECRET",
    "KIS_MOCK_ACCOUNT_NO",
    "KIS_REAL_APP_KEY",
    "KIS_REAL_APP_SECRET",
    "KIS_ACCOUNT_NO",
    "DART_API_KEY",
    "RENDER",
    "RENDER_COLLECT_LIMIT",
]

CHECK_DIRS = [
    "data",
    "data/raw",
    "data/processed",
    "models",
    "reports/predictions",
    "logs",
]


def probe() -> dict:
    result = {
        "probe_at": __import__("datetime").datetime.now().isoformat(),
        "python_version": sys.version,
        "project_root": str(PROJECT_ROOT),
        "cwd": str(Path.cwd()),
        "is_render": bool(
            os.environ.get("RENDER")
            or os.environ.get("RENDER_EXTERNAL_URL")
            or os.environ.get("RENDER_SERVICE_ID")
        ),
        "render_collect_limit": os.environ.get("RENDER_COLLECT_LIMIT", "300"),
        "env_check": {},
        "dir_check": {},
        "write_check": {},
        "script_check": {},
        "artifact_check": {},
        "errors": [],
    }

    # ── 1. 환경변수 존재 여부 (값 출력 없음) ────────────────────────────
    for key in ENV_KEYS_REQUIRED:
        result["env_check"][key] = "OK" if os.environ.get(key) else "MISSING"

    # ── 2. 디렉토리 존재 및 쓰기 가능 여부 ─────────────────────────────
    for rel in CHECK_DIRS:
        d = PROJECT_ROOT / rel
        exists = d.exists()
        if not exists:
            try:
                d.mkdir(parents=True, exist_ok=True)
                exists = True
            except Exception as ex:
                result["errors"].append(f"mkdir {rel}: {ex}")

        writable = False
        if exists:
            test_file = d / ".probe_write_test"
            try:
                test_file.write_text("probe")
                test_file.unlink()
                writable = True
            except Exception as ex:
                result["errors"].append(f"write {rel}: {ex}")

        result["dir_check"][rel] = {"exists": exists, "writable": writable}

    # ── 3. 현재 산출물 파일 목록 ────────────────────────────────────────
    def _count(d: Path, pattern: str = "*") -> int:
        try:
            return sum(1 for _ in d.glob(pattern) if _.is_file())
        except Exception:
            return 0

    raw_dir = PROJECT_ROOT / "data" / "raw"
    proc_dir = PROJECT_ROOT / "data" / "processed"

    result["artifact_check"] = {
        "raw_count": _count(raw_dir, "*.csv"),
        "raw_daily_exists": (raw_dir / "daily_prices.csv").exists(),
        "raw_files": [f.name for f in sorted(raw_dir.glob("*.csv"))[:10]] if raw_dir.exists() else [],
        "processed_count": _count(proc_dir, "*.csv"),
        "processed_files": [f.name for f in sorted(proc_dir.glob("*.csv"))[:10]] if proc_dir.exists() else [],
        "features_exists": (proc_dir / "features.csv").exists(),
        "labels_exists": (proc_dir / "labeled_dataset.csv").exists(),
        "models_count": _count(PROJECT_ROOT / "models"),
        "top100_count": _count(PROJECT_ROOT / "reports" / "predictions", "top100_*.csv"),
        "buy_top20_count": _count(PROJECT_ROOT / "reports" / "predictions", "buy_top20_*.csv"),
    }

    # ── 4. 스크립트 파일 존재 여부 ─────────────────────────────────────
    scripts = [
        "collect_daily_data.py",
        "make_features.py",
        "make_labels.py",
        "train_model.py",
        "predict_candidates.py",
        "select_top_candidates.py",
        "select_intraday_buy_candidates.py",
        "refresh_candidate_prices.py",
    ]
    for s in scripts:
        result["script_check"][s] = (PROJECT_ROOT / "src" / s).exists()

    # ── 5. make_features precheck (입력 파일 있으면 compile만 확인) ─────
    mf_script = PROJECT_ROOT / "src" / "make_features.py"
    if mf_script.exists():
        try:
            cp = subprocess.run(
                [sys.executable, "-m", "py_compile", str(mf_script)],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=10,
                env=os.environ.copy(),
            )
            result["write_check"]["make_features_compile"] = cp.returncode == 0
            if cp.returncode != 0:
                result["errors"].append(f"make_features compile: {cp.stderr[:200]}")
        except Exception as ex:
            result["write_check"]["make_features_compile"] = False
            result["errors"].append(f"make_features compile: {ex}")

    result["overall"] = "OK" if not result["errors"] else "WARN"
    return result


def main() -> None:
    r = probe()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print("\n" + "=" * 60, file=sys.stderr)
    print(f"  PROJECT_ROOT: {r['project_root']}", file=sys.stderr)
    print(f"  RENDER: {r['is_render']}", file=sys.stderr)
    print(f"  raw/daily_prices.csv: {r['artifact_check']['raw_daily_exists']}", file=sys.stderr)
    print(f"  features.csv: {r['artifact_check']['features_exists']}", file=sys.stderr)
    print(f"  overall: {r['overall']}", file=sys.stderr)
    if r["errors"]:
        print(f"  ERRORS: {r['errors']}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)


if __name__ == "__main__":
    main()
