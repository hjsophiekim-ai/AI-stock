"""Enrich AI candidate CSV with DART disclosure risk columns."""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from dart_api import DARTApiClient, has_dart_api_key
from disclosure_analyzer import DisclosureAnalyzer
from final_score_builder import build_final_score
from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/disclosure_analyzer.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _date_from_path(path: str) -> str:
    m = re.search(r"(20\d{6})", str(path))
    return m.group(1) if m else datetime.now().strftime("%Y%m%d")


def _normalize_code(value) -> str:
    text = str(value).replace(".0", "").strip()
    try:
        return str(int(text)).zfill(6)
    except Exception:
        return text.zfill(6)


def enrich_candidates(candidate_file: str, config_path: str = "config.yaml", date_str: Optional[str] = None) -> dict:
    cfg = load_config(config_path)
    nd = cfg.get("news_disclosure", {})
    enabled = bool(nd.get("enabled", True))
    use_dart = bool(nd.get("use_dart", True))
    lookback_days = int(nd.get("lookback_days", 3))
    ds = date_str or _date_from_path(candidate_file)

    df = pd.read_csv(candidate_file)
    if df.empty:
        raise ValueError(f"candidate file is empty: {candidate_file}")
    code_col = "stock_code" if "stock_code" in df.columns else ("ticker" if "ticker" in df.columns else None)
    if code_col is None:
        raise ValueError("stock_code/ticker column is required")
    df[code_col] = df[code_col].apply(_normalize_code)
    if "stock_code" not in df.columns:
        df["stock_code"] = df[code_col]

    disclosures = []
    dart_available = enabled and use_dart and has_dart_api_key()
    if dart_available:
        client = DARTApiClient()
        for code in df["stock_code"].astype(str).str.zfill(6).drop_duplicates():
            try:
                disclosures.extend(client.list_disclosures(code, lookback_days=lookback_days))
            except Exception as ex:
                logger.warning("DART disclosure lookup failed %s: %s", code, ex)
    else:
        logger.warning("DART_API_KEY missing or disclosure filter disabled; using neutral disclosure columns")

    disclosures_df = pd.DataFrame(disclosures)
    analyzer = DisclosureAnalyzer(config_path)
    analysis_df = analyzer.analyze_dataframe(disclosures_df)
    if analysis_df.empty:
        analysis_df = pd.DataFrame({
            "stock_code": df["stock_code"].astype(str).str.zfill(6).unique(),
            "buy_allowed": True,
            "disclosure_score": 0.0,
            "disclosure_risk_score": 0.0,
            "disclosure_summary": "" if dart_available else "DART_API_KEY missing or no recent disclosures",
            "disclosure_risk_reason": "",
            "disclosure_count": 0,
        })

    enriched = df.merge(analysis_df, on="stock_code", how="left")
    fill_values = {
        "buy_allowed": True,
        "disclosure_score": 0.0,
        "disclosure_risk_score": 0.0,
        "disclosure_summary": "",
        "disclosure_risk_reason": "",
        "disclosure_count": 0,
    }
    for col, val in fill_values.items():
        if col not in enriched.columns:
            enriched[col] = val
        enriched[col] = enriched[col].fillna(val)
    enriched = build_final_score(enriched, config_path)
    if "final_score" in enriched.columns:
        enriched = enriched.sort_values(["buy_allowed", "final_score"], ascending=[False, False], kind="stable")

    ensure_dir(str(PROJECT_ROOT / "reports"))
    enriched_path = PROJECT_ROOT / "reports" / f"enriched_candidates_{ds}.csv"
    disclosures_path = PROJECT_ROOT / "reports" / f"disclosures_{ds}.csv"
    enriched.to_csv(enriched_path, index=False, encoding="utf-8-sig")
    disclosures_df.to_csv(disclosures_path, index=False, encoding="utf-8-sig")
    return {
        "success": True,
        "dart_api_key_present": has_dart_api_key(),
        "dart_used": bool(dart_available),
        "candidate_count": int(len(enriched)),
        "blocked_count": int((enriched["buy_allowed"] == False).sum()),
        "disclosure_count": int(len(disclosures_df)),
        "enriched_path": str(enriched_path),
        "disclosures_path": str(disclosures_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    result = enrich_candidates(args.file, args.config, args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
