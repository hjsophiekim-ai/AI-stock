"""Top 후보 종목 선정 모듈 (top20/top50/top100).

predictions_YYYYMMDD.csv에서 필터를 적용하여 최종 후보를 생성합니다.
hard exclusion(거래정지, 관리종목, 우선주, 스팩, ETF/ETN)은 절대 완화하지 않습니다.

실행:
    python src/select_top_candidates.py --top-n 100
    python src/select_top_candidates.py --top-n 20
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from utils import (
    ensure_dir, get_today_str, is_etf_etn, is_preferred_stock, is_spac,
    load_config, save_csv, setup_logger,
)

logger = setup_logger(__name__, "logs/select_top_candidates.log")
cfg = load_config("config.yaml")

HARD_EXCLUSION_MIN_PRICE = 1000
SOFT_MIN_TRADING_VALUE = 3_000_000_000
SOFT_RELAXED_TRADING_VALUE = 500_000_000


def _get_code_col(df: pd.DataFrame) -> str:
    return "stock_code" if "stock_code" in df.columns else "ticker"


def _get_name_col(df: pd.DataFrame) -> str:
    return "stock_name" if "stock_name" in df.columns else "name"


def apply_hard_exclusions(df: pd.DataFrame) -> pd.DataFrame:
    """Hard exclusion 필터 — 절대 완화 불가."""
    risk = cfg.get("risk", {})
    code_col = _get_code_col(df)
    name_col = _get_name_col(df)

    original = len(df)
    excluded_reasons = pd.Series([""] * len(df), index=df.index)

    # 거래정지
    if "is_halted" in df.columns:
        mask = df["is_halted"].astype(bool)
        excluded_reasons[mask] = "거래정지"
        df = df[~mask]

    # 관리종목
    if "is_management" in df.columns:
        mask = df["is_management"].astype(bool)
        excluded_reasons.loc[mask[mask].index] = "관리종목"
        df = df[~mask]

    # 우선주
    if code_col in df.columns and risk.get("exclude_preferred_stock", True):
        mask = df[code_col].astype(str).str.zfill(6).apply(is_preferred_stock)
        excluded_reasons.loc[mask[mask].index] = "우선주"
        df = df[~mask]

    # 스팩
    if name_col in df.columns and risk.get("exclude_spac", True):
        mask = df[name_col].astype(str).apply(is_spac)
        excluded_reasons.loc[mask[mask].index] = "스팩"
        df = df[~mask]

    # ETF/ETN
    if name_col in df.columns and risk.get("exclude_etf_etn", True):
        mask = df[name_col].astype(str).apply(is_etf_etn)
        excluded_reasons.loc[mask[mask].index] = "ETF/ETN"
        df = df[~mask]

    # 최소 가격
    price_col = next((c for c in ("close", "current_price") if c in df.columns), None)
    if price_col:
        mask = df[price_col] < HARD_EXCLUSION_MIN_PRICE
        excluded_reasons.loc[mask[mask].index] = f"{HARD_EXCLUSION_MIN_PRICE}원 미만"
        df = df[~mask]

    removed = original - len(df)
    if removed > 0:
        logger.info(f"Hard exclusion: {removed}개 제외 → {len(df)}개 남음")

    return df.reset_index(drop=True)


def apply_soft_filters(df: pd.DataFrame, relax: bool = False) -> pd.DataFrame:
    """Soft 필터 — 후보 부족 시 완화 가능."""
    tv_col = next((c for c in ("trading_value", "tv") if c in df.columns), None)

    if tv_col:
        min_tv = SOFT_RELAXED_TRADING_VALUE if relax else SOFT_MIN_TRADING_VALUE
        df = df[df[tv_col] >= min_tv]

    # 상한가 근접 제외 (당일 수익률 25% 이상)
    ret_col = next((c for c in ("return_1d", "ret_1d") if c in df.columns), None)
    if ret_col:
        df = df[df[ret_col] < 0.25]

    return df.reset_index(drop=True)


def calc_score(df: pd.DataFrame) -> pd.DataFrame:
    """종합 점수 계산."""
    df = df.copy()

    prob_col = next((c for c in ("probability_2pct", "proba_up", "prediction_score") if c in df.columns), None)
    if prob_col is None:
        df["_score"] = 0.0
        return df

    def norm(s: pd.Series) -> pd.Series:
        rng = s.max() - s.min()
        return (s - s.min()) / rng if rng > 0 else pd.Series(0.5, index=s.index)

    score = norm(df[prob_col]) * 0.6

    tv_col = next((c for c in ("trading_value",) if c in df.columns), None)
    if tv_col:
        score += norm(df[tv_col].clip(0, 1e11)) * 0.2

    vr_col = next((c for c in ("volume_ratio_20", "vol_ratio") if c in df.columns), None)
    if vr_col:
        score += norm(df[vr_col].clip(0, 5)) * 0.2

    df["_score"] = score
    return df


def select_top_n(
    predictions_path: str,
    top_n: int,
    date_str: str,
    predictions_dir: str,
) -> pd.DataFrame:
    """predictions 파일에서 top_n 종목을 선정하고 저장."""
    if not os.path.exists(predictions_path):
        logger.error(f"예측 파일 없음: {predictions_path}")
        sys.exit(1)

    code_col_hint = "stock_code"
    df = pd.read_csv(predictions_path)

    code_col = _get_code_col(df)
    name_col = _get_name_col(df)

    # 6자리 복원
    df[code_col] = df[code_col].astype(str).str.zfill(6)

    # Hard exclusion (절대 완화 없음)
    df = apply_hard_exclusions(df)

    if df.empty:
        logger.warning("Hard exclusion 후 종목 없음")
        return pd.DataFrame()

    # Soft 필터 적용
    filtered = apply_soft_filters(df, relax=False)
    relax_used = ""

    if len(filtered) < top_n:
        logger.info(f"soft filter 통과 {len(filtered)}개 < {top_n} → 필터 완화")
        filtered = apply_soft_filters(df, relax=True)
        relax_used = "(거래대금 완화)"

    if len(filtered) < top_n:
        logger.info(f"완화 후에도 {len(filtered)}개 < {top_n} → 전체 hard exclusion 통과 종목 사용")
        filtered = df.copy()
        relax_used = "(거래대금 필터 제거)"

    # 점수 계산 및 정렬
    scored = calc_score(filtered)
    top = scored.nlargest(min(top_n, len(scored)), "_score").reset_index(drop=True)

    # 출력 컬럼 정리
    prob_col = next((c for c in ("probability_2pct", "proba_up", "prediction_score") if c in top.columns), None)
    tv_col = next((c for c in ("trading_value",) if c in top.columns), None)

    top["rank"] = range(1, len(top) + 1)
    top["date"] = date_str
    top["target_profit_rate"] = cfg.get("strategy", {}).get("target_profit_rate", 0.02)
    top["stop_loss_rate"] = cfg.get("strategy", {}).get("stop_loss_rate", -0.03)
    top["selected_filter_step"] = relax_used or "normal"
    top["exclusion_reason"] = ""

    out_base_cols = [
        "rank", "date", code_col, name_col, "close",
        prob_col, "prediction_score",
        tv_col, "trading_value_ma20",
        "return_1d", "return_5d", "return_20d",
        "volume_ratio_20",
        "selected_filter_step", "exclusion_reason",
        "target_profit_rate", "stop_loss_rate",
    ]
    out_cols = [c for c in out_base_cols if c and c in top.columns]
    top_out = top[out_cols].copy()

    # 컬럼 이름 정규화
    if code_col != "stock_code":
        top_out = top_out.rename(columns={code_col: "stock_code"})
    if name_col != "stock_name":
        top_out = top_out.rename(columns={name_col: "stock_name"})

    output_path = os.path.join(predictions_dir, f"top{top_n}_{date_str}.csv")
    ensure_dir(predictions_dir)
    save_csv(top_out, output_path)
    logger.info(f"Top{top_n} 저장: {output_path} ({len(top_out)}개 종목) {relax_used}")

    return top_out


def main() -> None:
    parser = argparse.ArgumentParser(description="Top 후보 종목 선정 (top20/50/100)")
    parser.add_argument("--top-n", type=int, default=100, help="후보 수 (기본값: 100)")
    parser.add_argument("--date", default=None, help="날짜 YYYYMMDD (기본값: 오늘)")
    parser.add_argument("--all", action="store_true", help="top20, top50, top100 모두 생성")
    args = parser.parse_args()

    today = args.date or get_today_str("%Y%m%d")
    predictions_dir = cfg["paths"]["predictions_dir"]
    predictions_path = os.path.join(predictions_dir, f"predictions_{today}.csv")

    if not os.path.exists(predictions_path):
        logger.error(f"예측 파일 없음: {predictions_path}")
        logger.error("먼저 실행: python src/predict_candidates.py")
        sys.exit(1)

    logger.info("=== Top 후보 선정 시작 ===")

    if args.all or args.top_n == 100:
        for n in [20, 50, 100]:
            select_top_n(predictions_path, n, today, predictions_dir)
    else:
        select_top_n(predictions_path, args.top_n, today, predictions_dir)

    logger.info("=== Top 후보 선정 완료 ===")
    top100_path = os.path.join(predictions_dir, f"top100_{today}.csv")
    if os.path.exists(top100_path):
        print(f"\nTop100 후보 파일: {top100_path}")


if __name__ == "__main__":
    main()
