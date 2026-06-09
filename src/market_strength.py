"""Market strength classification used by sell policies."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from utils import ensure_dir, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _score_ratio(value: float, neutral: float = 0.0, strong: float = 0.01) -> float:
    if value <= -strong:
        return 0.0
    if value >= strong:
        return 1.0
    return max(0.0, min(1.0, (value + strong) / (2 * strong)))


def classify_market_strength(score: float, config: dict | None = None) -> str:
    cfg = config or load_config()
    thresholds = cfg.get("market_strength", {}).get("thresholds", {})
    if score >= float(thresholds.get("very_strong", 0.80)):
        return "very_strong"
    if score >= float(thresholds.get("strong", 0.65)):
        return "strong"
    if score >= float(thresholds.get("normal", 0.50)):
        return "normal"
    return "weak"


def get_market_strength(candidates_df: pd.DataFrame | None = None, kis_api: Any = None, config: dict | None = None) -> dict:
    cfg = config or load_config()
    ms_cfg = cfg.get("market_strength", {})
    warnings: list[str] = []
    kospi_return = 0.0
    kosdaq_return = 0.0
    advancer_ratio = 1.0
    candidate_positive_ratio = 0.5

    try:
        if kis_api and hasattr(kis_api, "get_index_price"):
            kospi = kis_api.get_index_price("0001")
            kosdaq = kis_api.get_index_price("1001")
            kospi_return = float(kospi.get("return_rate", 0) or 0)
            kosdaq_return = float(kosdaq.get("return_rate", 0) or 0)
        else:
            warnings.append("index data unavailable; using neutral index returns")
    except Exception as exc:
        warnings.append(f"index data unavailable: {exc}")

    try:
        if candidates_df is not None and not candidates_df.empty:
            df = candidates_df.copy()
            price_col = "current_price" if "current_price" in df.columns else "close"
            prev_col = next((c for c in ("prev_close", "yesterday_close", "base_price") if c in df.columns), None)
            if price_col in df.columns and prev_col in df.columns:
                ret = (pd.to_numeric(df[price_col], errors="coerce") / pd.to_numeric(df[prev_col], errors="coerce") - 1).dropna()
                if len(ret):
                    candidate_positive_ratio = float((ret > 0).mean())
            elif "return_1d" in df.columns:
                ret = pd.to_numeric(df["return_1d"], errors="coerce").dropna()
                if len(ret):
                    candidate_positive_ratio = float((ret > 0).mean())
            else:
                warnings.append("candidate return columns unavailable; using neutral candidate ratio")
        else:
            warnings.append("candidate data unavailable; using neutral candidate ratio")
    except Exception as exc:
        warnings.append(f"candidate strength unavailable: {exc}")

    try:
        advancer_score = max(0.0, min(1.0, advancer_ratio / 2.0))
        score = (
            float(ms_cfg.get("kospi_weight", 0.25)) * _score_ratio(kospi_return)
            + float(ms_cfg.get("kosdaq_weight", 0.25)) * _score_ratio(kosdaq_return)
            + float(ms_cfg.get("advancer_ratio_weight", 0.25)) * advancer_score
            + float(ms_cfg.get("top_candidates_positive_ratio_weight", 0.25)) * candidate_positive_ratio
        )
    except Exception:
        score = 0.50
        warnings.append("score calculation failed; using normal")

    result = {
        "timestamp": datetime.now().isoformat(),
        "score": round(float(score), 4),
        "level": classify_market_strength(float(score), cfg),
        "kospi_return": kospi_return,
        "kosdaq_return": kosdaq_return,
        "advancer_ratio": advancer_ratio,
        "candidate_positive_ratio": candidate_positive_ratio,
        "warnings": warnings,
    }
    save_market_strength_report(result)
    return result


def save_market_strength_report(result: dict) -> str:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    path = PROJECT_ROOT / "reports" / f"market_strength_{datetime.now().strftime('%Y%m%d')}.csv"
    pd.DataFrame([result]).to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)
