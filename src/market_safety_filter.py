"""Safety filters for orderable intraday buy candidates.

The orderable buy list must not include halted, warning, crash, low-liquidity,
or paper-priced candidates. This module keeps those checks reusable by
selection, validation, and order execution paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


BLOCKED_NAME_KEYWORDS = [
    "스팩", "기업인수목적", "리츠", "ETN", "ETF", "인버스", "레버리지",
]
PREFERRED_MARKERS = ["우", "우B", "우C", "우선주"]
WARNING_KEYWORDS = [
    "거래정지", "정리매매", "관리종목", "투자주의", "투자경고", "투자위험",
    "단기과열", "불성실공시", "상장폐지",
]


def _cfg(config: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "exclude_negative_change": True,
        "min_change_rate_pct": 0.5,
        "max_change_rate_pct": 7.0,
        "hard_exclude_change_rate_below_pct": -2.0,
        "hard_exclude_crash_below_pct": -5.0,
        "max_allowed_change_rate_pct": 12.0,
        "max_high_drawdown_pct": -4.0,
        "preferred_high_drawdown_pct": -3.0,
        "min_high_proximity": 0.965,
        "min_recent_return_15m_pct": -0.3,
        "min_recent_return_30m_pct": -0.8,
        "exclude_below_open_when_negative": True,
        "exclude_gap_and_fade": True,
        "require_above_vwap": True,
        "min_trading_value_krw": 30_000_000_000,
        "hard_min_trading_value_krw": 10_000_000_000,
        "max_trading_value_rank": 200,
        "fallback_max_trading_value_rank": 250,
        "min_prob_intraday_2pct": 0.55,
        "min_candidates_to_trade": 10,
    }
    if config:
        base.update(config)
    return base


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def normalize_code(value: Any) -> str:
    try:
        return str(int(float(str(value).strip()))).zfill(6)
    except Exception:
        return str(value).strip().zfill(6)


def _num(series: pd.Series, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default)


def ensure_legacy_probability_name(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "probability_2pct" in out.columns and "legacy_nextday_probability_2pct" not in out.columns:
        out = out.rename(columns={"probability_2pct": "legacy_nextday_probability_2pct"})
    return out


def add_market_safety_columns(df: pd.DataFrame, config: dict[str, Any] | None = None) -> pd.DataFrame:
    cfg = _cfg(config)
    out = ensure_legacy_probability_name(df)

    name_col = "stock_name" if "stock_name" in out.columns else ("name" if "name" in out.columns else None)
    names = out[name_col].fillna("").astype(str) if name_col else pd.Series("", index=out.index)

    out["is_spac"] = names.str.contains("스팩|기업인수목적", regex=True)
    out["is_preferred_stock"] = names.str.contains("우B|우C|우선주", regex=True) | names.str.endswith("우")
    out["is_etf_etn"] = names.str.contains("리츠|ETN|ETF|인버스|레버리지", regex=True)

    joined_warning = "|".join(WARNING_KEYWORDS)
    existing_reason = out.get("market_status", pd.Series("", index=out.index)).fillna("").astype(str)
    existing_reason = existing_reason + " " + out.get("status", pd.Series("", index=out.index)).fillna("").astype(str)
    existing_reason = existing_reason + " " + out.get("warning_type", pd.Series("", index=out.index)).fillna("").astype(str)
    out["is_trading_halted"] = existing_reason.str.contains("거래정지|정리매매|상장폐지", regex=True)
    out["is_management_issue"] = existing_reason.str.contains("관리종목|불성실공시", regex=True)
    out["is_investment_warning"] = existing_reason.str.contains("투자주의|투자경고|투자위험|단기과열", regex=True)

    price = _num(out.get("current_price", pd.Series(0, index=out.index)))
    volume = _num(out.get("volume_intraday", out.get("volume", pd.Series(0, index=out.index))))
    price_mode = out.get("price_mode", pd.Series("", index=out.index)).fillna("").astype(str).str.upper()
    price_source = out.get("price_source", pd.Series("", index=out.index)).fillna("").astype(str).str.upper()

    fail_reasons: list[list[str]] = [[] for _ in range(len(out))]

    def add_reason(mask: pd.Series, reason: str) -> None:
        for pos in np.flatnonzero(mask.to_numpy(dtype=bool)):
            fail_reasons[pos].append(reason)

    add_reason(out["is_trading_halted"], "거래정지/정리매매/상장폐지")
    add_reason(out["is_management_issue"], "관리종목/불성실공시")
    add_reason(out["is_investment_warning"], "투자주의/경고/위험/단기과열")
    add_reason(out["is_spac"], "스팩")
    add_reason(out["is_preferred_stock"], "우선주")
    add_reason(out["is_etf_etn"], "ETF/ETN/리츠/인버스/레버리지")
    add_reason(price < 1000, "가격 1000원 미만")
    add_reason(price <= 0, "현재가 없음")
    add_reason(volume <= 0, "당일 거래량 0")
    add_reason(price_mode.eq("PAPER"), "price_mode PAPER")
    add_reason(price_source.eq("PAPER_CLOSE"), "price_source PAPER_CLOSE")

    out["market_safety_fail_reason"] = [";".join(r) for r in fail_reasons]
    out["market_safety_pass"] = out["market_safety_fail_reason"].eq("")
    return out


def apply_safe_intraday_filter(df: pd.DataFrame, config: dict[str, Any] | None = None) -> pd.DataFrame:
    cfg = _cfg(config)
    out = add_market_safety_columns(df, cfg)

    idx = out.index
    price = _num(out.get("current_price", pd.Series(0, index=idx)))
    open_price = _num(out.get("open_price", pd.Series(0, index=idx)))
    high_price = _num(out.get("high_price", pd.Series(0, index=idx)))
    change_rate = _num(out.get("change_rate", pd.Series(np.nan, index=idx)), default=np.nan)
    tv = _num(out.get("trading_value_intraday", pd.Series(np.nan, index=idx)), default=np.nan)
    rank = _num(out.get("trading_value_rank_market", pd.Series(np.nan, index=idx)), default=np.nan)
    recent15 = _num(out.get("recent_return_15m", pd.Series(0.0, index=idx)))
    recent30 = _num(out.get("recent_return_30m", pd.Series(0.0, index=idx)))
    prob = _num(out.get("prob_intraday_2pct", pd.Series(np.nan, index=idx)), default=np.nan)

    high_proximity = price / high_price.replace(0, np.nan)
    out["high_proximity"] = out.get("high_proximity", high_proximity).fillna(high_proximity).fillna(0)
    high_drawdown = (price / high_price.replace(0, np.nan) - 1.0) * 100.0
    out["high_drawdown_pct"] = out.get("high_drawdown_pct", high_drawdown).fillna(high_drawdown).fillna(-999)

    if "above_vwap" in out.columns:
        above_vwap = out["above_vwap"].apply(normalize_bool)
    else:
        vwap = _num(out.get("vwap_proxy", pd.Series(0, index=idx)))
        above_vwap = price > vwap
        out["above_vwap"] = above_vwap

    if "gap_and_fade" in out.columns:
        gap_and_fade = out["gap_and_fade"].apply(normalize_bool)
    elif "is_gap_and_fade" in out.columns:
        gap_and_fade = out["is_gap_and_fade"].apply(normalize_bool)
    else:
        gap_and_fade = (open_price > 0) & (price < open_price) & (change_rate < 0)
    out["gap_and_fade"] = gap_and_fade

    fail_reasons = out["market_safety_fail_reason"].fillna("").astype(str).tolist()

    def add(mask: pd.Series, reason: str) -> None:
        for pos in np.flatnonzero(mask.to_numpy(dtype=bool)):
            fail_reasons[pos] = (fail_reasons[pos] + ";" + reason).strip(";")

    add(change_rate.isna(), "등락률 없음")
    add(change_rate < float(cfg["min_change_rate_pct"]), "당일 상승률 미달")
    add(change_rate <= float(cfg["hard_exclude_change_rate_below_pct"]), "당일 급락 -2% 이하")
    add(change_rate <= float(cfg["hard_exclude_crash_below_pct"]), "당일 급락 -5% 이하")
    add(change_rate > float(cfg["max_allowed_change_rate_pct"]), "과도한 급등")
    add(out["high_drawdown_pct"] <= float(cfg["max_high_drawdown_pct"]), "고점 대비 하락 과다")
    add((price < open_price) & (change_rate < 0), "시가 아래 및 음봉")
    add(recent15 < float(cfg["min_recent_return_15m_pct"]), "15분 모멘텀 약화")
    add(recent30 < float(cfg["min_recent_return_30m_pct"]), "30분 모멘텀 약화")
    add(gap_and_fade, "갭 상승 후 이탈")
    add(~above_vwap, "VWAP 아래")
    add(out["high_proximity"] < float(cfg["min_high_proximity"]), "고가 근접도 미달")
    add(prob.isna(), "prob_intraday_2pct 없음")
    add(prob < float(cfg["min_prob_intraday_2pct"]), "intraday 확률 미달")

    has_rank = rank.notna()
    liquidity_pass = (
        (tv >= float(cfg["min_trading_value_krw"]))
        | (has_rank & (rank <= float(cfg["max_trading_value_rank"])))
    )
    hard_liquidity_fail = tv.isna() | (tv < float(cfg["hard_min_trading_value_krw"]))
    rank_fail = has_rank & (rank > float(cfg["fallback_max_trading_value_rank"]))
    add(hard_liquidity_fail, "거래대금 100억 미만/없음")
    add(~liquidity_pass, "거래대금/시장순위 미달")
    add(rank_fail, "거래대금 순위 250위 초과")

    out["market_safety_fail_reason"] = fail_reasons
    out["market_safety_pass"] = out["market_safety_fail_reason"].eq("")

    tv_score = (np.log1p(tv.clip(lower=0)) / np.log1p(max(float(cfg["min_trading_value_krw"]) * 3, 1))).clip(0, 1)
    vwap_score = above_vwap.astype(float)
    hp_score = ((out["high_proximity"] - 0.94) / (1.0 - 0.94)).clip(0, 1)
    momentum_score = ((recent15.clip(-1, 2) + 1) / 3 * 0.5 + (recent30.clip(-2, 3) + 2) / 5 * 0.5).clip(0, 1)
    sector_score = _num(out.get("sector_strength_score", pd.Series(0.5, index=idx)), default=0.5).clip(0, 1)
    crash_penalty = (
        (change_rate < 0).astype(float) * 0.5
        + (out["high_drawdown_pct"] < float(cfg["preferred_high_drawdown_pct"])).astype(float) * 0.5
    ).clip(0, 1)
    halt_penalty = (~out["market_safety_pass"]).astype(float)

    out["liquidity_score"] = tv_score.fillna(0)
    out["vwap_trend_score"] = vwap_score
    out["high_proximity_score"] = hp_score.fillna(0)
    out["recent_momentum_score"] = momentum_score.fillna(0)
    out["sector_strength_score"] = sector_score
    out["crash_risk_penalty"] = crash_penalty.fillna(1)
    out["halt_warning_penalty"] = halt_penalty
    out["final_safe_intraday_score"] = (
        prob.fillna(0).clip(0, 1) * 0.30
        + out["liquidity_score"] * 0.20
        + out["vwap_trend_score"] * 0.15
        + out["high_proximity_score"] * 0.15
        + out["recent_momentum_score"] * 0.10
        + out["sector_strength_score"] * 0.10
        - out["crash_risk_penalty"] * 0.20
        - out["halt_warning_penalty"] * 1.00
    )
    return out


def summarize_filter_failures(df: pd.DataFrame) -> dict[str, int]:
    reasons = df.get("market_safety_fail_reason", pd.Series("", index=df.index)).fillna("").astype(str)
    return {
        "excluded_market_risk": int(reasons.str.contains("거래정지|관리종목|투자주의|투자경고|투자위험|단기과열|스팩|우선주|ETF|ETN|리츠").sum()),
        "excluded_crash": int(reasons.str.contains("급락|상승률 미달|고점 대비|시가 아래|모멘텀|갭 상승").sum()),
        "excluded_low_liquidity": int(reasons.str.contains("거래대금|시장순위").sum()),
        "excluded_paper": int(reasons.str.contains("PAPER").sum()),
    }


def validate_orderable_buy_top20_df(df: pd.DataFrame, config: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    cfg = _cfg(config)
    failed: list[str] = []
    if len(df) > 20:
        failed.append("candidate_count_gt_20")
    if "prob_intraday_2pct" not in df.columns:
        failed.append("missing_prob_intraday_2pct")
    if "market_safety_pass" not in df.columns:
        failed.append("missing_market_safety_pass")
    else:
        if (~df["market_safety_pass"].apply(normalize_bool)).any():
            failed.append("market_safety_pass_false")
    if (df.get("price_source", pd.Series("", index=df.index)).astype(str).str.upper() == "PAPER_CLOSE").any():
        failed.append("paper_close_present")
    if (df.get("price_mode", pd.Series("", index=df.index)).astype(str).str.upper() == "PAPER").any():
        failed.append("paper_mode_present")
    if (_num(df.get("current_price", pd.Series(0, index=df.index))) <= 0).any():
        failed.append("current_price_non_positive")
    if (_num(df.get("change_rate", pd.Series(-999, index=df.index))) < 0).any():
        failed.append("negative_change_rate")
    if (_num(df.get("trading_value_intraday", pd.Series(0, index=df.index))) < float(cfg["hard_min_trading_value_krw"])).any():
        failed.append("low_trading_value")
    if "trading_value_rank_market" in df.columns:
        rank = _num(df["trading_value_rank_market"], default=np.nan)
        if (rank > float(cfg["fallback_max_trading_value_rank"])).any():
            failed.append("trading_value_rank_gt_250")
    if "above_vwap" in df.columns and (~df["above_vwap"].apply(normalize_bool)).any():
        failed.append("below_vwap")
    if "high_drawdown_pct" in df.columns and (_num(df["high_drawdown_pct"]) < float(cfg["max_high_drawdown_pct"])).any():
        failed.append("high_drawdown_too_low")
    return len(failed) == 0, failed


def copy_if_valid(safe_path: Path, final_path: Path, config: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    df = pd.read_csv(safe_path)
    ok, failed = validate_orderable_buy_top20_df(df, config)
    if ok:
        final_path.write_bytes(safe_path.read_bytes())
    return ok, failed
