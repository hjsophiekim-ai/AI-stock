"""Disclosure keyword risk analysis."""

from typing import Dict, List

import pandas as pd

from utils import load_config


def _contains_any(text: str, keywords: List[str]) -> List[str]:
    source = str(text or "")
    return [kw for kw in keywords if kw and kw in source]


class DisclosureAnalyzer:
    def __init__(self, config_path: str = "config.yaml") -> None:
        cfg = load_config(config_path)
        nd = cfg.get("news_disclosure", {})
        self.enabled = bool(nd.get("enabled", True))
        self.block_high_risk = bool(nd.get("block_high_risk_disclosures", True))
        self.hard_keywords = list(nd.get("hard_block_keywords", []))
        self.negative_keywords = list(nd.get("negative_keywords", []))
        self.positive_keywords = list(nd.get("positive_keywords", []))

    def analyze_stock(self, stock_code: str, disclosures: List[Dict]) -> Dict:
        if not self.enabled:
            return {
                "stock_code": str(stock_code).zfill(6),
                "buy_allowed": True,
                "disclosure_score": 0.0,
                "disclosure_risk_score": 0.0,
                "disclosure_summary": "disclosure filter disabled",
                "disclosure_risk_reason": "",
                "disclosure_count": 0,
            }

        hard_hits = []
        negative_hits = []
        positive_hits = []
        summaries = []
        for row in disclosures:
            title = str(row.get("report_nm", ""))
            summaries.append(title)
            hard_hits.extend(_contains_any(title, self.hard_keywords))
            negative_hits.extend(_contains_any(title, self.negative_keywords))
            positive_hits.extend(_contains_any(title, self.positive_keywords))

        hard_hits = sorted(set(hard_hits))
        negative_hits = sorted(set(negative_hits))
        positive_hits = sorted(set(positive_hits))
        risk_score = len(hard_hits) * 1.0 + len(negative_hits) * 0.35
        pos_score = len(positive_hits) * 0.2
        disclosure_score = max(-1.0, min(1.0, pos_score - risk_score))
        buy_allowed = not (self.block_high_risk and bool(hard_hits))
        reason_parts = []
        if hard_hits:
            reason_parts.append("hard_block=" + ",".join(hard_hits))
        if negative_hits:
            reason_parts.append("negative=" + ",".join(negative_hits))
        if positive_hits:
            reason_parts.append("positive=" + ",".join(positive_hits))

        return {
            "stock_code": str(stock_code).zfill(6),
            "buy_allowed": bool(buy_allowed),
            "disclosure_score": round(disclosure_score, 4),
            "disclosure_risk_score": round(risk_score, 4),
            "disclosure_summary": " | ".join(summaries[:3]),
            "disclosure_risk_reason": "; ".join(reason_parts),
            "disclosure_count": len(disclosures),
        }

    def analyze_dataframe(self, disclosures_df: pd.DataFrame) -> pd.DataFrame:
        if disclosures_df is None or disclosures_df.empty:
            return pd.DataFrame(columns=[
                "stock_code", "buy_allowed", "disclosure_score", "disclosure_risk_score",
                "disclosure_summary", "disclosure_risk_reason", "disclosure_count",
            ])
        rows = []
        for code, group in disclosures_df.groupby("stock_code"):
            rows.append(self.analyze_stock(str(code).zfill(6), group.to_dict("records")))
        return pd.DataFrame(rows)
