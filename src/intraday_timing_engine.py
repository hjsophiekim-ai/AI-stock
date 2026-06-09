"""Evaluate intraday buy timing signals for AI candidates."""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from intraday_indicators import add_intraday_indicators
from utils import ensure_dir, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TIMING_METHODS = {
    "immediate": "즉시 매수",
    "rsi_macd_rebound": "RSI/MACD 반등 매수",
    "vwap_reclaim": "VWAP 회복 매수",
    "momentum_breakout": "모멘텀 돌파 매수",
    "pullback_then_up": "눌림 후 반등 매수",
}


def _normalize_code(code) -> str:
    text = str(code).replace(".0", "").strip()
    try:
        return str(int(text)).zfill(6)
    except Exception:
        return text.zfill(6)


def _interval_minutes(interval: str) -> int:
    return 3 if str(interval).startswith("3") else 1


def load_intraday_data(stock_code: str, interval: str = "1min", config_path: str = "config.yaml") -> pd.DataFrame:
    cfg = load_config(config_path)
    code = _normalize_code(stock_code)
    today = datetime.now().strftime("%Y%m%d")
    candidates = [
        PROJECT_ROOT / cfg.get("data", {}).get("raw_intraday_dir", "data/intraday") / today / f"{code}_{interval}.csv",
        PROJECT_ROOT / cfg.get("data", {}).get("raw_intraday_dir", "data/intraday") / today / f"{code}.csv",
    ]
    for path in candidates:
        if path.exists():
            df = pd.read_csv(path)
            if not df.empty:
                return df
    try:
        from kis_api import KISApiClient
        from safety_gate import SafetyGate
        gate = SafetyGate(config_path, runtime_mode="mock")
        api = KISApiClient(config_path, gate=gate)
        return api.get_intraday_ohlcv(code, interval=str(_interval_minutes(interval)))
    except Exception:
        return pd.DataFrame()


def _latest_signal(stock_code: str, stock_name: str, method: str, interval: str, df: pd.DataFrame) -> dict:
    code = _normalize_code(stock_code)
    if method == "immediate":
        current_price = float(df["close"].iloc[-1]) if df is not None and not df.empty and "close" in df.columns else 0
        return {
            "stock_code": code, "stock_name": stock_name, "timing_method": method,
            "buy_now": True, "timing_score": 1.0, "rsi": 0, "macd": 0,
            "macd_signal": 0, "macd_hist": 0, "vwap": 0, "current_price": current_price,
            "reason": "immediate", "checked_at": datetime.now().isoformat(),
        }
    if df is None or df.empty or len(df) < 30 or "close" not in df.columns:
        return {
            "stock_code": code, "stock_name": stock_name, "timing_method": method,
            "buy_now": False, "timing_score": 0.0, "rsi": 0, "macd": 0,
            "macd_signal": 0, "macd_hist": 0, "vwap": 0, "current_price": 0,
            "reason": "no_intraday_data", "checked_at": datetime.now().isoformat(),
        }

    data = add_intraday_indicators(df).dropna(subset=["close"]).reset_index(drop=True)
    if len(data) < 30:
        return {
            "stock_code": code, "stock_name": stock_name, "timing_method": method,
            "buy_now": False, "timing_score": 0.0, "rsi": 0, "macd": 0,
            "macd_signal": 0, "macd_hist": 0, "vwap": 0, "current_price": 0,
            "reason": "insufficient_indicator_data", "checked_at": datetime.now().isoformat(),
        }
    last = data.iloc[-1]
    prev = data.iloc[-2]
    close = float(last["close"])
    rsi = float(last.get("rsi_14", 0) or 0)
    macd = float(last.get("macd", 0) or 0)
    macd_signal = float(last.get("macd_signal", 0) or 0)
    macd_hist = float(last.get("macd_hist", 0) or 0)
    prev_hist = float(prev.get("macd_hist", 0) or 0)
    vwap = float(last.get("vwap", 0) or 0)
    vol_ratio = float(last.get("volume_ratio_intraday", 0) or 0)
    day_high = float(data["high"].max()) if "high" in data.columns else float(data["close"].max())
    recent_low = float(data["low"].tail(10).min()) if "low" in data.columns else float(data["close"].tail(10).min())
    previous_low = float(data["low"].tail(20).head(10).min()) if "low" in data.columns else recent_low
    reason = []
    score = 0.0

    if method == "rsi_macd_rebound":
        conditions = [
            30 <= rsi <= 55 and rsi >= float(prev.get("rsi_14", rsi)),
            macd_hist > prev_hist,
            close >= recent_low * 1.003,
        ]
        reason = ["RSI recovery", "MACD histogram improving", "rebound from recent low"]
        score = sum(conditions) / len(conditions)
        buy_now = all(conditions)
    elif method == "vwap_reclaim":
        conditions = [
            close >= vwap,
            float(prev.get("close", close)) < float(prev.get("vwap", vwap)),
            vol_ratio >= 1.1,
        ]
        reason = ["price reclaimed VWAP", "prior bar below VWAP", "volume expanding"]
        score = sum(conditions) / len(conditions)
        buy_now = all(conditions)
    elif method == "momentum_breakout":
        conditions = [
            close >= day_high * 0.985,
            vol_ratio >= 1.2,
            macd >= macd_signal,
            rsi < 80,
        ]
        reason = ["near intraday high", "volume expanding", "MACD positive", "RSI not overheated"]
        score = sum(conditions) / len(conditions)
        buy_now = all(conditions)
    elif method == "pullback_then_up":
        conditions = [
            previous_low <= recent_low,
            40 <= rsi <= 65,
            close >= vwap * 0.995,
            macd_hist > prev_hist,
        ]
        reason = ["higher low", "RSI recovered", "VWAP support/reclaim", "MACD improving"]
        score = sum(conditions) / len(conditions)
        buy_now = all(conditions)
    else:
        buy_now = False
        reason = ["unknown timing method"]

    return {
        "stock_code": code,
        "stock_name": stock_name,
        "timing_method": method,
        "interval": interval,
        "buy_now": bool(buy_now),
        "timing_score": round(float(score), 4),
        "rsi": round(rsi, 2),
        "macd": round(macd, 4),
        "macd_signal": round(macd_signal, 4),
        "macd_hist": round(macd_hist, 4),
        "vwap": round(vwap, 2),
        "current_price": close,
        "reason": "; ".join(reason),
        "checked_at": datetime.now().isoformat(),
    }


def evaluate_timing_signal(
    stock_code,
    stock_name: str = "",
    interval: str = "1min",
    method: str = "rsi_macd_rebound",
    config_path: str = "config.yaml",
) -> dict:
    df = load_intraday_data(str(stock_code), interval=interval, config_path=config_path)
    return _latest_signal(str(stock_code), stock_name or str(stock_code), method, interval, df)


def evaluate_candidates_timing(
    candidates_df: pd.DataFrame,
    interval: str = "1min",
    method: str = "rsi_macd_rebound",
    config_path: str = "config.yaml",
) -> pd.DataFrame:
    code_col = "stock_code" if "stock_code" in candidates_df.columns else ("ticker" if "ticker" in candidates_df.columns else None)
    name_col = "stock_name" if "stock_name" in candidates_df.columns else ("name" if "name" in candidates_df.columns else None)
    if code_col is None:
        raise ValueError("stock_code/ticker column is required")
    rows = []
    for _, row in candidates_df.iterrows():
        rows.append(evaluate_timing_signal(
            row[code_col],
            str(row[name_col]) if name_col else str(row[code_col]),
            interval=interval,
            method=method,
            config_path=config_path,
        ))
    out = pd.DataFrame(rows)
    ensure_dir(str(PROJECT_ROOT / "reports"))
    out.to_csv(PROJECT_ROOT / "reports" / f"intraday_timing_signals_{datetime.now().strftime('%Y%m%d')}.csv", index=False, encoding="utf-8-sig")
    return out
