"""파이프라인 서비스 - 전체 AI 예측 파이프라인을 단계별로 실행합니다.

항상 dict를 반환하며 문자열만 반환하는 경우는 절대 없습니다.
subprocess는 sys.executable + Path(PROJECT_ROOT/"src"/script) 절대경로만 사용합니다.
shell=True 사용 금지. Windows 하드코딩 경로 금지.
json.loads(stdout) 파싱 금지.
"""

import json
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


def _is_render_env() -> bool:
    """Render 배포 환경 여부 감지."""
    return bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("RENDER_SERVICE_ID")
    )


def _render_collect_limit() -> int:
    """Render에서 데이터 수집 종목 수 제한 (환경변수 RENDER_COLLECT_LIMIT, 기본 300)."""
    return int(os.environ.get("RENDER_COLLECT_LIMIT", "300"))


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


def inspect_runtime_artifacts() -> dict:
    """현재 런타임 산출물 파일 수를 집계하여 반환.

    반환 키:
        raw_count           - data/raw/*.csv 파일 수
        raw_daily_exists    - data/raw/daily_prices.csv 존재 여부
        processed_count     - data/processed/*.csv 파일 수
        features_exists     - data/processed/features.csv 존재 여부
        labels_exists       - data/processed/labeled_dataset.csv 존재 여부
        models_count        - models/ 파일 수
        data_models_count   - data/models/ 파일 수
        top100_count        - reports/predictions/top100_*.csv 파일 수
        buy_top20_count     - reports/predictions/buy_top20_*.csv 파일 수
        logs_count          - logs/*.log 파일 수
    """
    def _count(d: Path, pattern: str = "*") -> int:
        try:
            return sum(1 for _ in d.glob(pattern) if _.is_file())
        except Exception:
            return 0

    raw_dir = PROJECT_ROOT / "data" / "raw"
    proc_dir = PROJECT_ROOT / "data" / "processed"
    models_dir = PROJECT_ROOT / "models"
    data_models_dir = PROJECT_ROOT / "data" / "models"
    preds_dir = PROJECT_ROOT / "reports" / "predictions"
    logs_dir = PROJECT_ROOT / "logs"

    return {
        "raw_count": _count(raw_dir, "*.csv"),
        "raw_daily_exists": (raw_dir / "daily_prices.csv").exists(),
        "processed_count": _count(proc_dir, "*.csv"),
        "features_exists": (proc_dir / "features.csv").exists(),
        "labels_exists": (proc_dir / "labeled_dataset.csv").exists(),
        "models_count": _count(models_dir),
        "data_models_count": _count(data_models_dir),
        "top100_count": _count(preds_dir, "top100_*.csv"),
        "buy_top20_count": _count(preds_dir, "buy_top20_*.csv"),
        "logs_count": _count(logs_dir, "*.log"),
    }


def _save_step_result(step_name: str, result: dict) -> None:
    """단계별 마지막 결과를 logs/<step>_last_result.json 에 저장."""
    try:
        logs_dir = PROJECT_ROOT / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        out = {
            "script": step_name,
            "success": result.get("success", False),
            "returncode": result.get("returncode", -1),
            "duration_sec": result.get("duration_sec", 0),
            "run_at": datetime.now().isoformat(),
            "artifacts_after": result.get("artifacts_after", {}),
            "stdout_tail": (result.get("stdout", "") or "")[-1000:],
            "stderr_tail": (result.get("stderr", "") or "")[-1000:],
        }
        with open(logs_dir / f"{step_name}_last_result.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def run_pipeline_step(
    step_name: str,
    script_name: str,
    args: Optional[List[str]] = None,
    timeout: int = 900,
) -> Dict:
    """단일 파이프라인 단계를 subprocess로 실행. 항상 dict를 반환한다.

    반환 키: step, success, returncode, stdout, stderr, duration_sec, artifacts_after
    """
    print(f"[PIPELINE] START {step_name} script={script_name}", flush=True)

    script_path = PROJECT_ROOT / "src" / script_name
    # -u: Python unbuffered mode (stdout flush 즉시)
    cmd = [sys.executable, "-u", str(script_path)] + (args or [])

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
            env=os.environ.copy(),
        )
        duration = round(time.time() - t0, 1)
        success = proc.returncode == 0
        stdout = (proc.stdout or "")[-4000:]
        stderr = (proc.stderr or "")[-4000:]
        artifacts = inspect_runtime_artifacts()

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
                    f"[PIPELINE] STDERR {step_name}: {stderr[:2000]}",
                    flush=True,
                )

        result = {
            "step": step_name,
            "success": success,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_sec": duration,
            "artifacts_after": artifacts,
        }
        _save_step_result(step_name, result)
        return result

    except subprocess.TimeoutExpired:
        duration = round(time.time() - t0, 1)
        print(f"[PIPELINE] TIMEOUT {step_name} after {timeout}s", flush=True)
        result = {
            "step": step_name,
            "success": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"TimeoutExpired after {timeout}s",
            "duration_sec": duration,
            "artifacts_after": inspect_runtime_artifacts(),
        }
        _save_step_result(step_name, result)
        return result

    except Exception as ex:
        duration = round(time.time() - t0, 1)
        print(f"[PIPELINE] ERROR {step_name}: {ex}", flush=True)
        result = {
            "step": step_name,
            "success": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(ex),
            "duration_sec": duration,
            "artifacts_after": inspect_runtime_artifacts(),
        }
        _save_step_result(step_name, result)
        return result


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


def _fail_result(
    failed_step: str,
    step_results: List[Dict],
    error_message: str,
    log_path: str,
) -> Dict:
    """실패 result dict 헬퍼."""
    last = step_results[-1] if step_results else {}
    print(
        f"[PIPELINE] END run_full_pipeline success=False at {failed_step}: {error_message[:200]}",
        flush=True,
    )
    return {
        "success": False,
        "failed_step": failed_step,
        "steps": step_results,
        "candidate_file": "",
        "buy_top20_file": "",
        "candidate_count": 0,
        "error_message": error_message,
        "stdout_raw": last.get("stdout", ""),
        "stderr_raw": last.get("stderr", ""),
        "log_path": log_path,
        "artifacts_final": inspect_runtime_artifacts(),
    }


def run_full_pipeline(
    mode: str = "paper",
    top_n: int = 100,
    refresh_prices: bool = True,
    years: int = 3,
    limit: Optional[int] = None,
) -> Dict:
    """전체 AI 예측 파이프라인 실행. 항상 JSON-직렬화 가능한 dict를 반환한다.

    WebSocket 연결이 끊어져도 Render Logs에 모든 단계 로그가 남도록
    st.write() 대신 print()만 사용합니다. 호출자에서 UI 표시를 담당합니다.

    반환 키:
        success         - bool
        failed_step     - 실패 단계 이름 (성공 시 "")
        steps           - 단계별 결과 list (artifacts_after 포함)
        candidate_file  - 생성된 top100 CSV 경로 (성공 시)
        buy_top20_file  - 생성된 buy_top20 CSV 경로 (성공 시)
        candidate_count - top100 행 수
        error_message   - 실패 메시지
        stdout_raw      - 실패 단계 stdout
        stderr_raw      - 실패 단계 stderr
        log_path        - 로그 파일 경로
        artifacts_final - 최종 산출물 현황
    """
    print("[PIPELINE] ENTER run_full_pipeline", flush=True)
    ensure_runtime_directories()

    today = datetime.now().strftime("%Y%m%d")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = str(PROJECT_ROOT / "logs" / f"pipeline_{ts}.json")

    collect_args = (
        ["--years", str(years)]
        + (["--limit", str(limit)] if limit else (
            ["--limit", str(_render_collect_limit())] if _is_render_env() else []
        ))
    )

    step_defs = [
        {
            "step": "collect_daily_data",
            "script": "collect_daily_data.py",
            "timeout": 3600,
            "args": collect_args,
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
            "timeout": 300,
            "args": ["--top-n", str(top_n), "--all"],
        },
    ]

    step_results: List[Dict] = []

    for s in step_defs:
        step_name = s["step"]

        # ── 입력 산출물 사전 검증 ──────────────────────────────────────
        precheck_err = _precheck_step_inputs(step_name)
        if precheck_err:
            sr = {
                "step": step_name,
                "success": False,
                "returncode": -2,
                "stdout": "",
                "stderr": precheck_err,
                "duration_sec": 0.0,
                "artifacts_after": inspect_runtime_artifacts(),
                "precheck_failed": True,
            }
            step_results.append(sr)
            _save_step_result(step_name, sr)
            return _fail_result(
                f"{step_name}_precheck",
                step_results,
                f"[{step_name}] 입력 파일 없음: {precheck_err}",
                log_path,
            )

        # ── 단계 실행 ─────────────────────────────────────────────────
        print(f"[PIPELINE] ABOUT_TO_START {step_name}", flush=True)
        sr = run_pipeline_step(
            step_name=step_name,
            script_name=s["script"],
            args=s.get("args", []),
            timeout=s["timeout"],
        )
        step_results.append(sr)

        # ── 산출물 후처리 검증 ─────────────────────────────────────────
        if sr["success"]:
            output_err = _postcheck_step_outputs(step_name, sr)
            if output_err:
                sr["success"] = False
                sr["stderr"] = (sr.get("stderr", "") + "\n" + output_err).strip()
                sr["output_missing"] = True
                step_results[-1] = sr
                _save_step_result(step_name, sr)
                return _fail_result(
                    step_name,
                    step_results,
                    output_err,
                    log_path,
                )

        if not sr["success"]:
            _save_pipeline_result(log_path, step_results, False, step_name, "", 0, sr.get("stderr", ""), today)
            return _fail_result(
                step_name,
                step_results,
                (
                    f"[{step_name}] 실패 (exit {sr['returncode']})\n"
                    f"{sr['stderr'][-500:]}"
                ),
                log_path,
            )

    # ── Top100 파일 존재 확인 ─────────────────────────────────────────
    candidate_file_path = PROJECT_ROOT / "reports" / "predictions" / f"top100_{today}.csv"
    if not candidate_file_path.exists():
        last = step_results[-1] if step_results else {}
        msg = (
            f"Top100 파일이 생성되지 않았습니다: {candidate_file_path}\n"
            f"artifacts: {inspect_runtime_artifacts()}"
        )
        print(f"[PIPELINE] END run_full_pipeline success=False top100 not found", flush=True)
        _save_pipeline_result(log_path, step_results, False, "select_top_candidates", "", 0, msg, today)
        return {
            "success": False,
            "failed_step": "select_top_candidates",
            "steps": step_results,
            "candidate_file": str(candidate_file_path),
            "buy_top20_file": "",
            "candidate_count": 0,
            "error_message": msg,
            "stdout_raw": last.get("stdout", ""),
            "stderr_raw": last.get("stderr", ""),
            "log_path": log_path,
            "artifacts_final": inspect_runtime_artifacts(),
        }

    candidate_count = 0
    try:
        import pandas as _pd
        df_c = _pd.read_csv(candidate_file_path)
        candidate_count = len(df_c)
    except Exception:
        pass

    # ── 현재가 갱신 (선택) ─────────────────────────────────────────────
    optional_mode = (mode or "paper").lower().strip()
    if optional_mode not in {"paper", "mock", "real"}:
        optional_mode = "paper"

    if refresh_prices:
        print(f"[PIPELINE] ABOUT_TO_START refresh_candidate_prices", flush=True)
        refresh_sr = run_pipeline_step(
            step_name="refresh_candidate_prices",
            script_name="refresh_candidate_prices.py",
            args=[
                "--mode", optional_mode,
                "--input", str(candidate_file_path),
                "--top", str(top_n),
                "--date", today,
            ],
            timeout=120 if optional_mode == "paper" else 300,
        )
        step_results.append(refresh_sr)

    # ── Intraday AI buy_top20 generation ─────────────────────────────
    print(f"[PIPELINE] ABOUT_TO_START predict_intraday_candidates", flush=True)
    intraday_pred_sr = run_pipeline_step(
        step_name="predict_intraday_candidates",
        script_name="predict_intraday_candidates.py",
        args=["--date", today],
        timeout=180,
    )
    step_results.append(intraday_pred_sr)
    if not intraday_pred_sr["success"]:
        _save_pipeline_result(log_path, step_results, False, "predict_intraday_candidates", str(candidate_file_path), candidate_count, intraday_pred_sr.get("stderr", ""), today)
        return _fail_result(
            "predict_intraday_candidates",
            step_results,
            f"[predict_intraday_candidates] 실패\n{intraday_pred_sr.get('stderr', '')[-500:]}",
            log_path,
        )

    print(f"[PIPELINE] ABOUT_TO_START select_today_buy_top20", flush=True)
    buy20_sr = run_pipeline_step(
        step_name="select_today_buy_top20",
        script_name="select_today_buy_top20.py",
        args=["--date", today, "--mode", "paper"],
        timeout=60,
    )
    step_results.append(buy20_sr)
    if not buy20_sr["success"]:
        _save_pipeline_result(log_path, step_results, False, "select_today_buy_top20", str(candidate_file_path), candidate_count, buy20_sr.get("stderr", ""), today)
        return _fail_result(
            "select_today_buy_top20",
            step_results,
            f"[select_today_buy_top20] 실패\n{buy20_sr.get('stderr', '')[-500:]}",
            log_path,
        )

    buy_top20_file = ""
    buy20_path = PROJECT_ROOT / "reports" / "predictions" / f"buy_top20_{today}.csv"
    if buy20_path.exists():
        buy_top20_file = str(buy20_path)

    artifacts_final = inspect_runtime_artifacts()
    print(
        f"[PIPELINE] END run_full_pipeline success=True "
        f"count={candidate_count} top100={artifacts_final['top100_count']} "
        f"buy_top20={artifacts_final['buy_top20_count']}",
        flush=True,
    )

    result = {
        "success": True,
        "failed_step": "",
        "steps": step_results,
        "candidate_file": str(candidate_file_path),
        "buy_top20_file": buy_top20_file,
        "candidate_count": candidate_count,
        "error_message": "",
        "stdout_raw": "",
        "stderr_raw": "",
        "log_path": log_path,
        "artifacts_final": artifacts_final,
    }
    _save_pipeline_result(log_path, step_results, True, "", str(candidate_file_path), candidate_count, "", today)
    return result


def _precheck_step_inputs(step_name: str) -> str:
    """단계 실행 전 필요한 입력 파일이 있는지 확인. 문제 없으면 "" 반환."""
    raw_daily = PROJECT_ROOT / "data" / "raw" / "daily_prices.csv"
    proc_features = PROJECT_ROOT / "data" / "processed" / "features.csv"
    proc_labels = PROJECT_ROOT / "data" / "processed" / "labeled_dataset.csv"

    if step_name == "make_features":
        if not raw_daily.exists():
            return (
                f"data/raw/daily_prices.csv 없음 — collect_daily_data를 먼저 실행하세요. "
                f"(cwd={Path.cwd()}, project_root={PROJECT_ROOT})"
            )
    elif step_name == "make_labels":
        if not proc_features.exists():
            return (
                f"data/processed/features.csv 없음 — make_features를 먼저 실행하세요."
            )
    elif step_name == "train_model":
        if not proc_labels.exists():
            return (
                f"data/processed/labeled_dataset.csv 없음 — make_labels를 먼저 실행하세요."
            )
    elif step_name == "predict_candidates":
        if not proc_features.exists():
            return (
                f"data/processed/features.csv 없음 — make_features를 먼저 실행하세요."
            )
    return ""


def _postcheck_step_outputs(step_name: str, sr: dict) -> str:
    """단계 성공(returncode==0) 후 산출물 파일이 실제로 생성됐는지 확인.
    문제 없으면 "" 반환."""
    raw_daily = PROJECT_ROOT / "data" / "raw" / "daily_prices.csv"
    proc_features = PROJECT_ROOT / "data" / "processed" / "features.csv"
    proc_labels = PROJECT_ROOT / "data" / "processed" / "labeled_dataset.csv"
    models_dir = PROJECT_ROOT / "models"
    data_models_dir = PROJECT_ROOT / "data" / "models"

    if step_name == "collect_daily_data":
        if not raw_daily.exists():
            return (
                f"collect_daily_data가 returncode=0 이었지만 "
                f"data/raw/daily_prices.csv 가 생성되지 않았습니다. "
                f"stdout 마지막 줄: {(sr.get('stdout') or '')[-300:]}"
            )
    elif step_name == "make_features":
        if not proc_features.exists():
            return (
                f"make_features가 returncode=0 이었지만 "
                f"data/processed/features.csv 가 생성되지 않았습니다."
            )
    elif step_name == "make_labels":
        if not proc_labels.exists():
            return (
                f"make_labels가 returncode=0 이었지만 "
                f"data/processed/labeled_dataset.csv 가 생성되지 않았습니다."
            )
    elif step_name == "train_model":
        has_model = (
            any(models_dir.glob("*.joblib")) if models_dir.exists() else False
        ) or (
            any(data_models_dir.glob("*.joblib")) if data_models_dir.exists() else False
        )
        if not has_model:
            return (
                f"train_model이 returncode=0 이었지만 "
                f"models/*.joblib 가 생성되지 않았습니다."
            )
    return ""


def _save_pipeline_result(
    log_path: str,
    step_results: List[Dict],
    success: bool,
    failed_step: str,
    candidate_file: str,
    candidate_count: int,
    error_message: str,
    today: str,
) -> None:
    """전체 파이프라인 결과를 logs/pipeline_TIMESTAMP.json 에 저장."""
    try:
        out = {
            "run_at": datetime.now().isoformat(),
            "today": today,
            "success": success,
            "failed_step": failed_step,
            "candidate_file": candidate_file,
            "candidate_count": candidate_count,
            "error_message": error_message,
            "artifacts_final": inspect_runtime_artifacts(),
            "steps": [
                {
                    "step": s.get("step", ""),
                    "success": s.get("success", False),
                    "returncode": s.get("returncode", -1),
                    "duration_sec": s.get("duration_sec", 0),
                    "artifacts_after": s.get("artifacts_after", {}),
                    "stderr_tail": (s.get("stderr", "") or "")[-500:],
                }
                for s in step_results
            ],
        }
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def run_fast_candidate_pipeline(
    mode: str = "paper",
    top_n: int = 100,
) -> Dict:
    """빠른 후보 생성 파이프라인 - 데이터 수집/모델 학습 생략.

    기존 데이터와 모델을 그대로 사용하며 predict_candidates ->
    select_top_candidates -> refresh_prices -> select_intraday_buy 실행.
    Render 환경 권장.

    반환 키: run_full_pipeline과 동일 구조.
    """
    print("[PIPELINE] ENTER run_fast_candidate_pipeline", flush=True)
    ensure_runtime_directories()

    today = datetime.now().strftime("%Y%m%d")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = str(PROJECT_ROOT / "logs" / f"pipeline_fast_{ts}.json")
    artifacts = inspect_runtime_artifacts()

    # 빠른 파이프라인 사전 검증 — 모델/features 없으면 명확히 실패
    models_dir = PROJECT_ROOT / "models"
    data_models_dir = PROJECT_ROOT / "data" / "models"
    features_file = PROJECT_ROOT / "data" / "processed" / "features.csv"
    has_model = (
        any(models_dir.glob("*.joblib")) if models_dir.exists() else False
    ) or (
        any(data_models_dir.glob("*.joblib")) if data_models_dir.exists() else False
    )
    if not has_model:
        msg = (
            "Render 서버에 학습된 모델 파일이 없습니다 (models/*.joblib). "
            "'전체 파이프라인 실행'으로 모델을 먼저 학습하거나, "
            "prebuilt model/data를 배포해야 합니다."
        )
        print(f"[PIPELINE] FAIL predict_candidates_precheck: {msg}", flush=True)
        return {
            "success": False,
            "failed_step": "predict_candidates_precheck",
            "steps": [],
            "candidate_file": "",
            "buy_top20_file": "",
            "candidate_count": 0,
            "error_message": msg,
            "stdout_raw": "",
            "stderr_raw": msg,
            "log_path": log_path,
            "artifacts_final": artifacts,
        }

    if not features_file.exists():
        msg = (
            "features.csv 파일이 없습니다 (data/processed/features.csv). "
            "'전체 파이프라인 실행'으로 데이터를 먼저 수집/처리하세요."
        )
        print(f"[PIPELINE] FAIL predict_candidates_precheck: {msg}", flush=True)
        return {
            "success": False,
            "failed_step": "predict_candidates_precheck",
            "steps": [],
            "candidate_file": "",
            "buy_top20_file": "",
            "candidate_count": 0,
            "error_message": msg,
            "stdout_raw": "",
            "stderr_raw": msg,
            "log_path": log_path,
            "artifacts_final": artifacts,
        }

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
    optional_mode = (mode or "paper").lower().strip()
    if optional_mode not in {"paper", "mock", "real"}:
        optional_mode = "paper"

    for s in step_defs:
        print(f"[PIPELINE] ABOUT_TO_START {s['step']}", flush=True)
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
                "buy_top20_file": "",
                "candidate_count": 0,
                "error_message": (
                    f"[{sr['step']}] 실패 (exit {sr['returncode']})\n"
                    f"{sr['stderr'][-500:]}"
                ),
                "stdout_raw": sr["stdout"],
                "stderr_raw": sr["stderr"],
                "log_path": log_path,
                "artifacts_final": inspect_runtime_artifacts(),
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
            "buy_top20_file": "",
            "candidate_count": 0,
            "error_message": "Top100 file was not created",
            "stdout_raw": last.get("stdout", ""),
            "stderr_raw": last.get("stderr", ""),
            "log_path": log_path,
            "artifacts_final": inspect_runtime_artifacts(),
        }

    candidate_count = 0
    try:
        import pandas as _pd
        df_c = _pd.read_csv(candidate_file_path)
        candidate_count = len(df_c)
    except Exception:
        pass

    # Intraday AI buy_top20 generation. This is required for orderable output.
    print(f"[PIPELINE] ABOUT_TO_START predict_intraday_candidates", flush=True)
    intraday_pred_sr = run_pipeline_step(
        step_name="predict_intraday_candidates",
        script_name="predict_intraday_candidates.py",
        args=["--date", today],
        timeout=180,
    )
    step_results.append(intraday_pred_sr)
    if not intraday_pred_sr["success"]:
        return {
            "success": False,
            "failed_step": "predict_intraday_candidates",
            "steps": step_results,
            "candidate_file": str(candidate_file_path),
            "buy_top20_file": "",
            "candidate_count": candidate_count,
            "error_message": f"[predict_intraday_candidates] 실패\n{intraday_pred_sr.get('stderr', '')[-500:]}",
            "stdout_raw": intraday_pred_sr.get("stdout", ""),
            "stderr_raw": intraday_pred_sr.get("stderr", ""),
            "log_path": log_path,
            "artifacts_final": inspect_runtime_artifacts(),
        }

    print(f"[PIPELINE] ABOUT_TO_START select_today_buy_top20", flush=True)
    buy20_sr = run_pipeline_step(
        step_name="select_today_buy_top20",
        script_name="select_today_buy_top20.py",
        args=["--date", today, "--mode", "paper"],
        timeout=60,
    )
    step_results.append(buy20_sr)
    if not buy20_sr["success"]:
        return {
            "success": False,
            "failed_step": "select_today_buy_top20",
            "steps": step_results,
            "candidate_file": str(candidate_file_path),
            "buy_top20_file": "",
            "candidate_count": candidate_count,
            "error_message": f"[select_today_buy_top20] 실패\n{buy20_sr.get('stderr', '')[-500:]}",
            "stdout_raw": buy20_sr.get("stdout", ""),
            "stderr_raw": buy20_sr.get("stderr", ""),
            "log_path": log_path,
            "artifacts_final": inspect_runtime_artifacts(),
        }

    buy_top20_file = ""
    buy20_path = PROJECT_ROOT / "reports" / "predictions" / f"buy_top20_{today}.csv"
    if buy20_path.exists():
        buy_top20_file = str(buy20_path)

    artifacts_final = inspect_runtime_artifacts()
    print(
        f"[PIPELINE] END run_fast_candidate_pipeline success=True count={candidate_count}",
        flush=True,
    )
    return {
        "success": True,
        "failed_step": "",
        "steps": step_results,
        "candidate_file": str(candidate_file_path),
        "buy_top20_file": buy_top20_file,
        "candidate_count": candidate_count,
        "error_message": "",
        "stdout_raw": "",
        "stderr_raw": "",
        "log_path": log_path,
        "artifacts_final": artifacts_final,
    }
