"""장중 실시간 필터로 Top100 후보 중 최종 매수 후보 Top20 선정.

입력: reports/predictions/top100_YYYYMMDD.csv
출력: reports/predictions/buy_top20_YYYYMMDD.csv
stdout: JSON (success, candidate_count, output_file, ...)

모드:
  paper  - KIS API 미사용. CSV 기존 데이터로만 처리 (Render 환경 / 장 외)
  mock   - KIS 모의투자 API (get_current_price) 로 OHLCV 보강
  real   - KIS 실전 API (현재가 조회만, 주문 없음)

실행 예시:
  python src/select_intraday_buy_candidates.py --mode paper
  python src/select_intraday_buy_candidates.py --mode mock --date 20260615 --top-n 20
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

_SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SRC_DIR.parent
sys.path.insert(0, str(_SRC_DIR))

from utils import ensure_dir, get_today_str, load_config, save_csv, setup_logger

logger = setup_logger(__name__, "logs/select_intraday_buy_candidates.log")
cfg = load_config(str(PROJECT_ROOT / "config.yaml"))


def _find_top100_file(date_str: str) -> Optional[str]:
    preds_dir = PROJECT_ROOT / "reports" / "predictions"
    today_file = preds_dir / f"top100_{date_str}.csv"
    if today_file.exists():
        return str(today_file)
    found = sorted(preds_dir.glob("top100_????????.csv"), reverse=True)
    return str(found[0]) if found else None


def _enrich_with_kis_data(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """KIS get_current_price()로 OHLCV 보강. paper면 CSV 데이터 그대로 사용."""
    if mode == "paper":
        df = df.copy()
        if "current_price" not in df.columns:
            pc = next((c for c in ("close", "stck_prpr") if c in df.columns), None)
            df["current_price"] = df[pc].fillna(0) if pc else 0
        for col in ("open_price", "high_price", "low_price"):
            if col not in df.columns:
                df[col] = df["current_price"]
        if "change_rate" not in df.columns:
            df["change_rate"] = 0.0
        if "trading_value_intraday" not in df.columns:
            tv_col = next((c for c in ("trading_value",) if c in df.columns), None)
            df["trading_value_intraday"] = df[tv_col].fillna(0) if tv_col else 0
        if "volume_intraday" not in df.columns:
            vol_col = next((c for c in ("volume",) if c in df.columns), None)
            df["volume_intraday"] = df[vol_col].fillna(0) if vol_col else 0
        df["_data_source"] = "paper_csv"
        return df

    try:
        from kis_api import KISApiClient
        from safety_gate import SafetyGate
        gate = SafetyGate(str(PROJECT_ROOT / "config.yaml"), runtime_mode=mode)
        api = KISApiClient(str(PROJECT_ROOT / "config.yaml"), gate=gate)
        api.auth.get_access_token()
        logger.info(f"KIS API 연결 성공 (mode={mode})")
    except Exception as ex:
        logger.warning(f"KIS API 연결 실패 ({ex}) — paper fallback")
        return _enrich_with_kis_data(df, "paper")

    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    rows = []
    for _, row in df.iterrows():
        row = row.copy()
        code = str(row[code_col]).zfill(6)
        try:
            d = api.get_current_price(code)
            if d is None:
                raise ValueError("get_current_price returned None")
            def _f(d, *keys):
                for k in keys:
                    v = d.get(k)
                    if v is not None:
                        try:
                            return float(str(v).replace(",", "") or 0)
                        except Exception:
                            pass
                return 0.0
            row["current_price"] = _f(d, "current_price", "stck_prpr") or float(row.get("close", 0) or 0)
            row["open_price"] = _f(d, "open", "stck_oprc") or row["current_price"]
            row["high_price"] = _f(d, "high", "stck_hgpr") or row["current_price"]
            row["low_price"] = _f(d, "low", "stck_lwpr") or row["current_price"]
            row["volume_intraday"] = _f(d, "volume", "acml_vol")
            row["trading_value_intraday"] = _f(d, "trading_value", "acml_tr_pbmn")
            row["change_rate"] = _f(d, "change_rate", "prdy_ctrt")
            row["_data_source"] = "kis_api"
        except Exception as ex:
            logger.debug(f"현재가 조회 실패 [{code}]: {ex}")
            row["current_price"] = float(row.get("close", 0) or 0)
            row["open_price"] = row["current_price"]
            row["high_price"] = row["current_price"]
            row["low_price"] = row["current_price"]
            row["volume_intraday"] = float(row.get("volume", 0) or 0)
            row["trading_value_intraday"] = float(row.get("trading_value", 0) or 0)
            row["change_rate"] = 0.0
            row["_data_source"] = "csv_fallback"
        rows.append(row)

    return pd.DataFrame(rows).reset_index(drop=True)


def _calc_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    eps = 1e-9

    cp = df["current_price"].astype(float)
    hp = df["high_price"].astype(float)
    op = df["open_price"].astype(float)

    df["high_proximity"] = cp / (hp + eps)
    df["high_drawdown_pct"] = (cp - hp) / (hp + eps) * 100

    tv = df.get("trading_value_intraday", pd.Series(0, index=df.index)).astype(float)
    vol = df.get("volume_intraday", pd.Series(0, index=df.index)).astype(float)
    df["vwap_proxy"] = tv / (vol + eps)
    df["vwap_proxy"] = df["vwap_proxy"].where(vol > 0, cp)
    df["above_vwap"] = cp > df["vwap_proxy"]

    close_col = next((c for c in ("close",) if c in df.columns), None)
    if close_col:
        pc = df[close_col].astype(float)
        gap_up = op > pc * 1.02
        fading = cp < op
        df["is_gap_and_fade"] = gap_up & fading
    else:
        df["is_gap_and_fade"] = False

    return df


def _apply_filters(df: pd.DataFrame, intra_cfg: dict) -> pd.DataFrame:
    min_tv = intra_cfg.get("min_trading_value_krw", 30_000_000_000)
    min_cr = intra_cfg.get("min_change_rate_pct", 1.5)
    max_cr = intra_cfg.get("max_change_rate_pct", 12.0)
    min_hp = intra_cfg.get("min_high_proximity", 0.97)
    max_hd = intra_cfg.get("max_high_drawdown_pct", -3.0)
    req_vwap = intra_cfg.get("require_above_vwap", True)
    excl_gaf = intra_cfg.get("exclude_gap_and_fade", True)

    df = df.copy()
    df["filter_pass"] = True
    df["filter_fail_reason"] = ""

    tv = df.get("trading_value_intraday", pd.Series(0, index=df.index)).astype(float)
    cr = df.get("change_rate", pd.Series(0, index=df.index)).astype(float)
    hp = df.get("high_proximity", pd.Series(1, index=df.index)).astype(float)
    hd = df.get("high_drawdown_pct", pd.Series(0, index=df.index)).astype(float)

    is_paper = ("_data_source" in df.columns) and (df["_data_source"] == "paper_csv").any()

    if not is_paper:
        mask = tv < min_tv
        df.loc[mask, "filter_fail_reason"] += "거래대금부족;"
        df.loc[mask, "filter_pass"] = False

        mask = cr < min_cr
        df.loc[mask, "filter_fail_reason"] += f"상승률미달({min_cr}%);"
        df.loc[mask, "filter_pass"] = False

        mask = cr > max_cr
        df.loc[mask, "filter_fail_reason"] += f"상승률과다({max_cr}%);"
        df.loc[mask, "filter_pass"] = False

        mask = hp < min_hp
        df.loc[mask, "filter_fail_reason"] += f"고가근접도미달({min_hp});"
        df.loc[mask, "filter_pass"] = False

        mask = hd < max_hd
        df.loc[mask, "filter_fail_reason"] += f"고점낙폭초과({max_hd}%);"
        df.loc[mask, "filter_pass"] = False

        if req_vwap and "above_vwap" in df.columns:
            mask = ~df["above_vwap"].astype(bool)
            if "_data_source" in df.columns:
                mask = mask & (df["_data_source"] != "paper_csv")
            df.loc[mask, "filter_fail_reason"] += "VWAP아래;"
            df.loc[mask, "filter_pass"] = False

        if excl_gaf and "is_gap_and_fade" in df.columns:
            mask = df["is_gap_and_fade"].astype(bool)
            df.loc[mask, "filter_fail_reason"] += "갭상승밀림;"
            df.loc[mask, "filter_pass"] = False

    return df


def _calc_score(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    sw = cfg.get("intraday_filter", {}).get("score_weights", {})

    def _norm(s: pd.Series, lo=None, hi=None) -> pd.Series:
        if lo is not None or hi is not None:
            s = s.clip(lo, hi)
        rng = s.max() - s.min()
        return (s - s.min()) / rng * 100 if rng > 0 else pd.Series(50.0, index=s.index)

    prob_col = next((c for c in ("probability_2pct", "prediction_score", "final_score") if c in df.columns), None)
    ai_score = _norm(df[prob_col].astype(float)) if prob_col else pd.Series(50.0, index=df.index)

    tv = df.get("trading_value_intraday", pd.Series(1, index=df.index)).astype(float).clip(1)
    tv_score = _norm(np.log1p(tv))

    cr = df.get("change_rate", pd.Series(0, index=df.index)).astype(float)
    cr_score = _norm(cr, lo=0, hi=10)

    hp = df.get("high_proximity", pd.Series(1, index=df.index)).astype(float)
    hp_score = _norm(hp, lo=0.9, hi=1.0)

    vwap_score = df.get("above_vwap", pd.Series(False, index=df.index)).astype(float) * 100

    trend_score = pd.Series(50.0, index=df.index)

    w_ai = sw.get("ai_score", 0.30)
    w_tv = sw.get("trading_value_score", 0.20)
    w_cr = sw.get("change_rate_score", 0.15)
    w_hp = sw.get("high_proximity_score", 0.15)
    w_vw = sw.get("vwap_score", 0.10)
    w_tr = sw.get("recent_momentum_score", 0.10)

    df["final_buy_score"] = (
        ai_score * w_ai
        + tv_score * w_tv
        + cr_score * w_cr
        + hp_score * w_hp
        + vwap_score * w_vw
        + trend_score * w_tr
    ).round(2)

    return df


def _select_top_n(
    df: pd.DataFrame, top_n: int, intra_cfg: dict
) -> Tuple[pd.DataFrame, list]:
    fallback_min = intra_cfg.get("fallback_min_candidates", 10)
    passed = df[df["filter_pass"]].copy()
    warnings = []

    if len(passed) >= top_n:
        result = passed.nlargest(top_n, "final_buy_score")
    elif len(passed) >= fallback_min:
        warnings.append(f"필터 통과 {len(passed)}개 < {top_n}: 통과분 전체 사용")
        result = passed.nlargest(len(passed), "final_buy_score")
    else:
        warnings.append(f"필터 통과 {len(passed)}개 < {fallback_min}: 필터 완화 적용")
        relaxed = dict(intra_cfg)
        relaxed["min_trading_value_krw"] = max(
            intra_cfg.get("min_trading_value_krw", 30_000_000_000) // 3,
            10_000_000_000,
        )
        relaxed["min_high_proximity"] = 0.94
        relaxed["min_change_rate_pct"] = 0.5
        relaxed["require_above_vwap"] = False
        df2 = df.copy()
        df2["filter_pass"] = True
        df2["filter_fail_reason"] = ""
        df2 = _apply_filters(df2, relaxed)
        passed2 = df2[df2["filter_pass"]]
        if len(passed2) >= fallback_min:
            result = passed2.nlargest(min(top_n, len(passed2)), "final_buy_score")
            warnings.append(f"완화 필터 후 {len(result)}개 선정")
        else:
            result = df.nlargest(min(top_n, len(df)), "final_buy_score")
            warnings.append(f"전체 점수 기준 {len(result)}개 선정 (필터 미통과 포함 — 경고)")

    result = result.reset_index(drop=True)
    result["final_buy_rank"] = range(1, len(result) + 1)
    result["allocation_version"] = "TOP20_INTRADAY_FILTERED_V1"
    return result, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="장중 매수 후보 Top20 필터 선정")
    parser.add_argument("--date", default=None, help="YYYYMMDD (기본: 오늘)")
    parser.add_argument("--top-n", type=int, default=20, help="선정 종목 수 (기본: 20)")
    parser.add_argument("--mode", default="paper", choices=["paper", "mock", "real"])
    args = parser.parse_args()

    today = args.date or get_today_str("%Y%m%d")
    top_n = min(int(args.top_n), 20)
    intra_cfg = cfg.get("intraday_filter", {})

    input_file = _find_top100_file(today)
    if not input_file:
        result = {
            "success": False,
            "error": f"top100_{today}.csv 없음",
            "candidate_count": 0,
        }
        print(json.dumps(result, ensure_ascii=False), flush=True)
        sys.exit(1)

    output_dir = PROJECT_ROOT / "reports" / "predictions"
    ensure_dir(str(output_dir))
    output_file = str(output_dir / f"buy_top{top_n}_{today}.csv")

    print(f"[INTRADAY] START mode={args.mode} top_n={top_n} input={input_file}", flush=True)
    logger.info(f"장중 필터 시작: {input_file}")

    df = pd.read_csv(input_file)
    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    df[code_col] = df[code_col].astype(str).str.zfill(6)

    df = _enrich_with_kis_data(df, args.mode)
    df = _calc_metrics(df)
    df = _apply_filters(df, intra_cfg)
    df = _calc_score(df)
    result_df, warnings = _select_top_n(df, top_n, intra_cfg)

    save_csv(result_df, output_file)
    logger.info(f"buy_top{top_n} 저장: {output_file} ({len(result_df)}개)")
    print(f"[INTRADAY] END count={len(result_df)} output={output_file}", flush=True)

    result = {
        "success": True,
        "input_file": input_file,
        "output_file": output_file,
        "candidate_count": len(result_df),
        "total_input": len(df),
        "filter_pass_count": int(df["filter_pass"].sum()),
        "mode": args.mode,
        "filters": {
            "min_trading_value_krw": intra_cfg.get("min_trading_value_krw", 30_000_000_000),
            "min_change_rate_pct": intra_cfg.get("min_change_rate_pct", 1.5),
            "max_change_rate_pct": intra_cfg.get("max_change_rate_pct", 12.0),
            "min_high_proximity": intra_cfg.get("min_high_proximity", 0.97),
            "max_high_drawdown_pct": intra_cfg.get("max_high_drawdown_pct", -3.0),
            "require_above_vwap": intra_cfg.get("require_above_vwap", True),
        },
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
