"""Build final candidate score after disclosure adjustment."""

from typing import Optional

import pandas as pd

from utils import load_config


def get_base_score_column(df: pd.DataFrame) -> Optional[str]:
    for col in ("prediction_score", "probability_2pct", "proba_up", "probability"):
        if col in df.columns:
            return col
    return None


def build_final_score(df: pd.DataFrame, config_path: str = "config.yaml") -> pd.DataFrame:
    cfg = load_config(config_path)
    apply_adjustment = bool(cfg.get("news_disclosure", {}).get("apply_score_adjustment", True))
    out = df.copy()
    score_col = get_base_score_column(out)
    if score_col:
        out["_base_score"] = pd.to_numeric(out[score_col], errors="coerce").fillna(0.0)
    else:
        out["_base_score"] = 0.0
    if "disclosure_score" not in out.columns:
        out["disclosure_score"] = 0.0
    adjustment = pd.to_numeric(out["disclosure_score"], errors="coerce").fillna(0.0) * 0.05
    out["final_score"] = out["_base_score"] + (adjustment if apply_adjustment else 0.0)
    out["final_score"] = out["final_score"].round(6)
    if "buy_allowed" not in out.columns:
        out["buy_allowed"] = True
    return out.drop(columns=["_base_score"])
