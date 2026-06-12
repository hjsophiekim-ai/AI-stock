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


def _run_pipeline_steps_individually(years: int = 3, top_n: int = 100) -> Dict:
    """run_ai_prediction_pipeline.py 가 없을 때 단계별로 실행하는 fallback.

    각 단계의 stdout/stderr/returncode 를 모아서 반환한다.
    """
    steps = [
        ("데이터 수집",  "collect_daily_data.py",  1800),
        ("피처 생성",    "make_features.py",        600),
        ("라벨 생성",    "make_labels.py",          300),
        ("모델 학습",    "train_model.py",          900),
        ("예측 생성",    "predict_candidates.py",   300),
        (f"Top{top_n} 생성", "select_top_candidates.py", 120),
    ]

    created_files: List[str] = []
    step_results: List[Dict] = []

    for stage_name, script, timeout in steps:
        r = run_script(script, timeout=timeout)
        step_results.append({"stage": stage_name, **r})
        if not r.get("success"):
            return {
                "success": False,
                "stage": stage_name,
                "message": f"[{stage_name}] 실패: {r.get('message', '')}",
                "stdout": r.get("stdout", ""),
                "stderr": r.get("stderr", ""),
                "returncode": r.get("returncode", -1),
                "errors": [r.get("stderr") or r.get("message") or ""],
                "created_files": created_files,
                "step_results": step_results,
                "predictions_file": None,
                "top100_file": None,
            }
        created_files.extend(r.get("created_files", []))

    today = datetime.now().strftime("%Y%m%d")
    pred_file = PROJECT_ROOT / "reports" / "predictions" / f"top{top_n}_{today}.csv"
    return {
        "success": True,
        "stage": "complete",
        "message": f"전체 파이프라인 완료 ({len(steps)}단계)",
        "created_files": created_files,
        "step_results": step_results,
        "predictions_file": str(pred_file) if pred_file.exists() else None,
        "top100_file": str(pred_file) if pred_file.exists() else None,
    }


def run_full_pipeline(
    years: int = 3,
    limit: Optional[int] = None,
    budget: Optional[int] = None,
    top_n: int = 100,
) -> Dict:
    """전체 AI 예측 파이프라인 실행. 항상 dict를 반환한다 — 절대 None 반환 없음."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from run_ai_prediction_pipeline import run_pipeline
        result = run_pipeline(years=years, limit=limit, budget=budget, top_n=top_n)
        if not isinstance(result, dict):
            return {
                "success": False, "stage": "unknown",
                "message": f"파이프라인 반환값이 dict가 아님: {type(result).__name__}",
                "errors": ["None or non-dict result from run_pipeline"],
                "created_files": [], "predictions_file": None, "top100_file": None,
            }
        return result
    except ImportError:
        pipeline_script = PROJECT_ROOT / "src" / "run_ai_prediction_pipeline.py"
        if pipeline_script.exists():
            r = run_script("run_ai_prediction_pipeline.py", timeout=3600)
            if not isinstance(r, dict):
                return {"success": False, "stage": "import_fallback", "message": "subprocess 반환값 없음", "errors": []}
            return r
        # 파이프라인 통합 스크립트가 없으면 단계별 실행
        return _run_pipeline_steps_individually(years=years, top_n=top_n)
    except Exception as ex:
        return {
            "success": False, "stage": "exception",
            "message": f"파이프라인 오류: {ex}",
            "errors": [str(ex)], "created_files": [],
            "predictions_file": None, "top100_file": None,
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
