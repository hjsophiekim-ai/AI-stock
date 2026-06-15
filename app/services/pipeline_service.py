"""파이프라인 서비스 - 전체 AI 예측 파이프라인을 단계별로 실행합니다.

항상 dict를 반환하며 문자열만 반환하는 경우는 절대 없습니다.
subprocess는 sys.executable + Path(PROJECT_ROOT/"src"/script) 절대경로만 사용합니다.
shell=True 사용 금지. Windows 하드코딩 경로 금지.
json.loads(stdout) 파싱 금지.
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

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


def ensure_runtime_directories() -> List[str]:
    """필수 런타임 디렉토리를 생성하고 새로 생성된 폴더 목록을 반환."""
    created: List[str] = []
    for rel in REQUIRED_DIRS:
        d = PROJECT_ROOT / rel
        if not d.exists():
            try:
                d.mkdir(parents=True, exist_ok=True)
                created.append(rel)
            except Exception:
                pass
    return created


def run_pipeline_step(
    step_name: str,
    script_name: str,
    args: Optional[List[str]] = None,
    timeout: int = 900,
) -> Dict:
    """단일 파이프라인 단계를 subprocess로 실행. 항상 dict를 반환한다.

    반환 키: step, success, returncode, stdout, stderr, duration_sec
    """
    print(f"[PIPELINE] START {step_name}", flush=True)

    script_path = PROJECT_ROOT / "src" / script_name
    cmd = [sys.executable, str(script_path)] + (args or [])

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        duration = round(time.time() - t0, 1)
        success = proc.returncode == 0
        stdout = (proc.stdout or "")[-4000:]
        stderr = (proc.stderr or "")[-4000:]

        if success:
            print(
                f"[PIPELINE] END {step_name} returncode={proc.returncode} duration={duration}s",
                flush=True,
            )
        else:
            print(
                f"[PIPELINE] FAIL {step_name} returncode={proc.returncode} duration={duration}s",
                flush=True,
            )
            if stderr:
                print(
                    f"[PIPELINE] STDERR {step_name}: {stderr[:1000]}",
                    flush=True,
                )

        return {
            "step": step_name,
            "success": success,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_sec": duration,
        }

    except subprocess.TimeoutExpired:
        duration = round(time.time() - t0, 1)
        print(f"[PIPELINE] TIMEOUT {step_name} after {timeout}s", flush=True)
        return {
            "step": step_name,
            "success": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"TimeoutExpired after {timeout}s",
            "duration_sec": duration,
        }

    except Exception as ex:
        duration = round(time.time() - t0, 1)
        print(f"[PIPELINE] ERROR {step_name}: {ex}", flush=True)
        return {
            "step": step_name,
            "success": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(ex),
            "duration_sec": duration,
        }


def find_latest_candidate_file() -> Optional[str]:
    """가장 최근 top100_YYYYMMDD.csv 파일 경로 반환. 없으면 None.

    탐색 순서:
        1. 오늘 top100_YYYYMMDD.csv
        2. reports/predictions/top100_*.csv 중 최신
        3. reports/predictions/candidates_*.csv 중 최신
    """
    preds_dir = PROJECT_ROOT / "reports" / "predictions"
    if not preds_dir.exists():
        return None

    today_str = datetime.now().strftime("%Y%m%d")
    today_file = preds_dir / f"top100_{today_str}.csv"
    if today_file.exists():
        return str(today_file)

    for pattern in ("top100_????????.csv", "candidates_????????.csv"):
        found = sorted(preds_dir.glob(pattern), reverse=True)
        if found:
            return str(found[0])

    return None


def run_full_pipeline(
    mode: str = "mock",
    top_n: int = 100,
    refresh_prices: bool = True,
    years: int = 3,
    limit: Optional[int] = None,
) -> Dict:
    """전체 AI 예측 파이프라인 실행. 항상 JSON-직렬화 가능한 dict를 반환한다.

    반환 키:
        success         - bool
        failed_step     - 실패 단계 이름 (성공 시 "")
        steps           - 단계별 결과 list
        candidate_file  - 생성된 top100 CSV 경로 (성공 시)
        candidate_count - top100 행 수
        error_message   - 실패 메시지
        stdout_raw      - 실패 단계 stdout
        stderr_raw      - 실패 단계 stderr
        log_path        - 로그 파일 경로
    """
    print("[PIPELINE] ENTER run_full_pipeline", flush=True)
    ensure_runtime_directories()

    today = datetime.now().strftime("%Y%m%d")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = str(PROJECT_ROOT / "logs" / f"pipeline_{ts}.log")

    step_defs = [
        {
            "step": "collect_daily_data",
            "script": "collect_daily_data.py",
            "timeout": 3600,
            "args": ["--years", str(years)] + (["--limit", str(limit)] if limit else []),
        },
        {
            "step": "make_features",
            "script": "make_features.py",
            "timeout": 900,
            "args": [],
        },
        {
            "step": "make_labels",
            "script": "make_labels.py",
            "timeout": 600,
            "args": [],
        },
        {
            "step": "train_model",
            "script": "train_model.py",
            "timeout": 1800,
            "args": [],
        },
        {
            "step": "predict_candidates",
            "script": "predict_candidates.py",
            "timeout": 300,
            "args": [],
        },
        {
            "step": "select_top_candidates",
            "script": "select_top_candidates.py",
            "timeout": 120,
            "args": ["--top-n", str(top_n), "--all"],
        },
    ]

    step_results: List[Dict] = []

    for s in step_defs:
        sr = run_pipeline_step(
            step_name=s["step"],
            script_name=s["script"],
            args=s.get("args", []),
            timeout=s["timeout"],
        )
        step_results.append(sr)

        if not sr["success"]:
            print(
                f"[PIPELINE] END run_full_pipeline success=False at {sr['step']}",
                flush=True,
            )
            return {
                "success": False,
                "failed_step": sr["step"],
                "steps": step_results,
                "candidate_file": "",
                "candidate_count": 0,
                "error_message": (
                    f"[{sr['step']}] 실패 (exit {sr['returncode']})\n"
                    f"{sr['stderr'][-500:]}"
                ),
                "stdout_raw": sr["stdout"],
                "stderr_raw": sr["stderr"],
                "log_path": log_path,
            }

    # Top100 파일 존재 확인
    candidate_file_path = (
        PROJECT_ROOT / "reports" / "predictions" / f"top100_{today}.csv"
    )
    if not candidate_file_path.exists():
        last = step_results[-1] if step_results else {}
        print("[PIPELINE] END run_full_pipeline success=False top100 not found", flush=True)
        return {
            "success": False,
            "failed_step": "select_top_candidates",
            "steps": step_results,
            "candidate_file": str(candidate_file_path),
            "candidate_count": 0,
            "error_message": "Top100 file was not created",
            "stdout_raw": last.get("stdout", ""),
            "stderr_raw": last.get("stderr", ""),
            "log_path": log_path,
        }

    candidate_count = 0
    try:
        import pandas as _pd
        df_c = _pd.read_csv(candidate_file_path)
        candidate_count = len(df_c)
    except Exception:
        pass

    # 현재가 갱신 (선택) - 실패해도 파이프라인 성공으로 처리
    if refresh_prices:
        refresh_sr = run_pipeline_step(
            step_name="refresh_candidate_prices",
            script_name="refresh_candidate_prices.py",
            args=["--mode", mode, "--top", str(top_n), "--date", today],
            timeout=300,
        )
        step_results.append(refresh_sr)

    print("[PIPELINE] END run_full_pipeline success=True", flush=True)
    return {
        "success": True,
        "failed_step": "",
        "steps": step_results,
        "candidate_file": str(candidate_file_path),
        "candidate_count": candidate_count,
        "error_message": "",
        "stdout_raw": "",
        "stderr_raw": "",
        "log_path": log_path,
    }


def run_fast_candidate_pipeline(
    mode: str = "mock",
    top_n: int = 100,
) -> Dict:
    """빠른 후보 생성 파이프라인 - 데이터 수집/모델 학습 생략.

    기존 데이터와 모델을 그대로 사용하며 predict_candidates ->
    select_top_candidates 만 실행합니다. Render 환경 권장.

    반환 키: run_full_pipeline과 동일 구조.
    """
    print("[PIPELINE] ENTER run_fast_candidate_pipeline", flush=True)
    ensure_runtime_directories()

    today = datetime.now().strftime("%Y%m%d")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = str(PROJECT_ROOT / "logs" / f"pipeline_fast_{ts}.log")

    step_defs = [
        {
            "step": "predict_candidates",
            "script": "predict_candidates.py",
            "timeout": 300,
            "args": [],
        },
        {
            "step": "select_top_candidates",
            "script": "select_top_candidates.py",
            "timeout": 120,
            "args": ["--top-n", str(top_n), "--all"],
        },
    ]

    step_results: List[Dict] = []

    for s in step_defs:
        sr = run_pipeline_step(
            step_name=s["step"],
            script_name=s["script"],
            args=s.get("args", []),
            timeout=s["timeout"],
        )
        step_results.append(sr)

        if not sr["success"]:
            print(
                f"[PIPELINE] END run_fast_candidate_pipeline success=False at {sr['step']}",
                flush=True,
            )
            return {
                "success": False,
                "failed_step": sr["step"],
                "steps": step_results,
                "candidate_file": "",
                "candidate_count": 0,
                "error_message": (
                    f"[{sr['step']}] 실패 (exit {sr['returncode']})\n"
                    f"{sr['stderr'][-500:]}"
                ),
                "stdout_raw": sr["stdout"],
                "stderr_raw": sr["stderr"],
                "log_path": log_path,
            }

    candidate_file_path = (
        PROJECT_ROOT / "reports" / "predictions" / f"top100_{today}.csv"
    )
    if not candidate_file_path.exists():
        last = step_results[-1] if step_results else {}
        print(
            "[PIPELINE] END run_fast_candidate_pipeline success=False top100 not found",
            flush=True,
        )
        return {
            "success": False,
            "failed_step": "select_top_candidates",
            "steps": step_results,
            "candidate_file": str(candidate_file_path),
            "candidate_count": 0,
            "error_message": "Top100 file was not created",
            "stdout_raw": last.get("stdout", ""),
            "stderr_raw": last.get("stderr", ""),
            "log_path": log_path,
        }

    candidate_count = 0
    try:
        import pandas as _pd
        df_c = _pd.read_csv(candidate_file_path)
        candidate_count = len(df_c)
    except Exception:
        pass

    print(
        f"[PIPELINE] END run_fast_candidate_pipeline success=True count={candidate_count}",
        flush=True,
    )
    return {
        "success": True,
        "failed_step": "",
        "steps": step_results,
        "candidate_file": str(candidate_file_path),
        "candidate_count": candidate_count,
        "error_message": "",
        "stdout_raw": "",
        "stderr_raw": "",
        "log_path": log_path,
    }
