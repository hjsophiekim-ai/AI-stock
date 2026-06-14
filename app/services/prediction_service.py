"""AI 후보 종목 예측 관련 서비스."""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def get_today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _predictions_dir() -> Path:
    return PROJECT_ROOT / "reports" / "predictions"


def get_top20_path(date_str: Optional[str] = None) -> Optional[Path]:
    """오늘(또는 지정 날짜)의 top20 파일 경로 반환 (없으면 None)."""
    ds = date_str or get_today_str()
    p = _predictions_dir() / f"top20_{ds}.csv"
    return p if p.exists() else None


def get_force_candidates_path(date_str: Optional[str] = None) -> Optional[Path]:
    """force_trade 후보 파일 경로 반환."""
    ds = date_str or get_today_str()
    p = PROJECT_ROOT / "reports" / f"force_trade_candidates_{ds}.csv"
    return p if p.exists() else None


def load_top20(date_str: Optional[str] = None) -> Optional[pd.DataFrame]:
    """top20 CSV 로드. 없으면 None."""
    p = get_top20_path(date_str)
    if p is None:
        return None
    return pd.read_csv(p, encoding="utf-8-sig")


def load_force_candidates(date_str: Optional[str] = None) -> Optional[pd.DataFrame]:
    """force_trade 후보 CSV 로드."""
    p = get_force_candidates_path(date_str)
    if p is None:
        return None
    return pd.read_csv(p, encoding="utf-8-sig")


def run_script(script_name: str, timeout: int = 120, args: Optional[List] = None) -> Dict:
    """src/ 스크립트를 subprocess로 실행.

    항상 stdout, stderr, returncode 를 반환한다.
    """
    script_path = str(PROJECT_ROOT / "src" / script_name)
    if not os.path.exists(script_path):
        return {
            "success": False,
            "message": f"스크립트 없음: {script_name}",
            "stdout": "",
            "stderr": f"파일을 찾을 수 없습니다: {script_path}",
            "returncode": -1,
        }
    try:
        cmd = [sys.executable, script_path] + (args or [])
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
        stdout = (result.stdout or "")[-4000:]
        stderr = (result.stderr or "")[-4000:]
        if result.returncode == 0:
            return {
                "success": True,
                "message": f"{script_name} 완료",
                "stdout": stdout,
                "stderr": stderr,
                "returncode": 0,
            }
        return {
            "success": False,
            "message": f"{script_name} 실패 (exit {result.returncode})",
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": f"{script_name} 타임아웃 ({timeout}초)",
            "stdout": "",
            "stderr": f"TimeoutExpired after {timeout}s",
            "returncode": -1,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"{script_name} 오류: {e}",
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
        }


def run_full_pipeline(
    years: int = 3,
    limit: Optional[int] = None,
    budget: Optional[int] = None,
    top_n: int = 100,
) -> Dict:
    """전체 AI 예측 파이프라인 실행. 항상 JSON-직렬화 가능한 dict를 반환한다.

    반환 키:
      success, failed_step, steps, candidate_file, candidate_count,
      error_message, stdout_raw, stderr_raw, log_path
    """
    import time

    # 필수 폴더 생성
    try:
        from startup_service import ensure_dirs
        ensure_dirs()
    except Exception:
        pass

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
            "timeout": 600,
            "args": [],
        },
        {
            "step": "make_labels",
            "script": "make_labels.py",
            "timeout": 300,
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
    failed_step = ""

    for s in step_defs:
        t0 = time.time()
        script_path = PROJECT_ROOT / "src" / s["script"]
        cmd = [sys.executable, str(script_path)] + s.get("args", [])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=s["timeout"],
                cwd=str(PROJECT_ROOT),
            )
            success = proc.returncode == 0
            stdout = (proc.stdout or "")[-4000:]
            stderr = (proc.stderr or "")[-4000:]
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            success = False
            stdout = ""
            stderr = f"TimeoutExpired after {s['timeout']}s"
            returncode = -1
        except Exception as ex:
            success = False
            stdout = ""
            stderr = str(ex)
            returncode = -1

        duration = round(time.time() - t0, 1)
        sr: Dict = {
            "step": s["step"],
            "success": success,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_sec": duration,
        }
        step_results.append(sr)

        if not success:
            failed_step = s["step"]
            return {
                "success": False,
                "failed_step": failed_step,
                "steps": step_results,
                "candidate_file": "",
                "candidate_count": 0,
                "error_message": f"[{s['step']}] 실패 (exit {returncode})\n{stderr[-500:]}",
                "stdout_raw": stdout,
                "stderr_raw": stderr,
                "log_path": log_path,
                # 하위 호환 키
                "stage": failed_step,
                "message": f"[{s['step']}] 실패 (exit {returncode})",
                "stderr": stderr,
                "stdout": stdout,
                "errors": [stderr[-500:]],
                "created_files": [],
                "predictions_file": None,
                "top100_file": None,
            }

    # Top100 파일 존재 확인
    candidate_file = PROJECT_ROOT / "reports" / "predictions" / f"top100_{today}.csv"
    candidate_count = 0

    if candidate_file.exists():
        try:
            df_c = pd.read_csv(candidate_file)
            candidate_count = len(df_c)
        except Exception:
            pass
    else:
        last = step_results[-1] if step_results else {}
        return {
            "success": False,
            "failed_step": "select_top_candidates",
            "steps": step_results,
            "candidate_file": str(candidate_file),
            "candidate_count": 0,
            "error_message": "Top100 파일이 생성되지 않았습니다.",
            "stdout_raw": last.get("stdout", ""),
            "stderr_raw": last.get("stderr", ""),
            "log_path": log_path,
            # 하위 호환 키
            "stage": "select_top_candidates",
            "message": "Top100 파일이 생성되지 않았습니다.",
            "stderr": last.get("stderr", ""),
            "stdout": last.get("stdout", ""),
            "errors": ["Top100 파일 미생성"],
            "created_files": [],
            "predictions_file": None,
            "top100_file": None,
        }

    return {
        "success": True,
        "failed_step": "",
        "steps": step_results,
        "candidate_file": str(candidate_file),
        "candidate_count": candidate_count,
        "error_message": "",
        "stdout_raw": "",
        "stderr_raw": "",
        "log_path": log_path,
        # 하위 호환 키
        "stage": "completed",
        "message": f"전체 파이프라인 완료 — Top100: {candidate_count}개 종목",
        "created_files": [str(candidate_file)],
        "predictions_file": str(candidate_file),
        "top100_file": str(candidate_file),
    }


def run_backtest_service(
    start_date: str = "2024-01-01",
    end_date: str = "2024-12-31",
    top_n: int = 20,
    strategy_id: str = "morning_0930",
) -> Dict:
    """백테스트 실행 서비스. 항상 dict를 반환한다."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from backtest import run_backtest
        result = run_backtest(
            start_date=start_date, end_date=end_date,
            top_n=top_n, strategy_id=strategy_id,
        )
        if not isinstance(result, dict):
            return {"success": False, "stage": "unknown", "message": "백테스트 None 반환", "errors": []}
        return result
    except ImportError:
        r = run_script("backtest.py", args=["--start", start_date, "--end", end_date,
                                            "--top-n", str(top_n), "--strategy", strategy_id], timeout=300)
        if not isinstance(r, dict):
            return {"success": False, "stage": "import_fallback", "message": "subprocess 반환값 없음", "errors": []}
        return r
    except Exception as ex:
        return {"success": False, "stage": "exception", "message": f"백테스트 오류: {ex}", "errors": [str(ex)]}


def run_force_trade_selector(date_str: Optional[str] = None, min_candidates: int = 1) -> Dict:
    """force_trade_selector 실행."""
    try:
        from force_trade_selector import ForceTradeSelector, ForceTradeSelectorError
        config_path = str(PROJECT_ROOT / "config.yaml")
        selector = ForceTradeSelector(config_path)
        ds = date_str or get_today_str()
        df = selector.select(date_str=ds, min_candidates=min_candidates)
        return {"success": True, "data": df, "count": len(df)}
    except Exception as e:
        return {"success": False, "message": str(e), "data": pd.DataFrame()}


def run_no_trade_analysis() -> Dict:
    """no_trade_analyzer 실행."""
    try:
        from no_trade_analyzer import NoTradeAnalyzer
        config_path = str(PROJECT_ROOT / "config.yaml")
        analyzer = NoTradeAnalyzer(config_path)
        result = analyzer.analyze()
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "message": str(e), "data": {}}
