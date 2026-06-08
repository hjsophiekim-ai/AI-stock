"""AI 후보 종목 예측 관련 서비스."""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

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


def run_script(script_name: str, timeout: int = 120) -> Dict:
    """src/ 스크립트를 subprocess로 실행."""
    script_path = str(PROJECT_ROOT / "src" / script_name)
    if not os.path.exists(script_path):
        return {"success": False, "message": f"스크립트 없음: {script_name}"}
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            return {"success": True, "message": f"{script_name} 완료", "stdout": result.stdout[-2000:]}
        return {"success": False, "message": f"{script_name} 실패", "stderr": result.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": f"{script_name} 타임아웃 ({timeout}초)"}
    except Exception as e:
        return {"success": False, "message": f"{script_name} 오류: {e}"}


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
