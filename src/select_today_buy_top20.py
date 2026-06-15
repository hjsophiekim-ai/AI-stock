"""오늘 당일 매수 후보 Top20 선정.

intraday_candidates_YYYYMMDD.csv에서 강한 필터 + 점수 기반으로 Top20 선정.
출력: reports/predictions/buy_top20_YYYYMMDD.csv

실행:
    python src/select_today_buy_top20.py
    python src/select_today_buy_top20.py --date 20260615
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/select_today_buy_top20.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
cfg = load_config(str(PROJECT_ROOT / "config.yaml"))


def _get_intraday_config() -> dict:
    return cfg.get("intraday_trading_model", {
        "min_prob_2pct": 0.58,
        "fallback_min_prob_2pct": 0.52,
        "max_buy_candidates": 20,
        "min_trading_value_krw": 30_000_000_000,
        "fallback_trading_value_krw": 10_000_000_000,
        "min_change_rate_pct": 1.0,
        "max_change_rate_pct": 10.0,
        "min_high_proximity": 0.965,
        "require_above_vwap": True,
        "min_recent_return_15m_pct": -0.3,
        "exclude_gap_and_fade": True,
    })


def select_buy_top20(
    date_str: str = None,
    candidates_path: str = None,
    output_dir: str = None,
) -> dict:
    """장중 후보에서 Top20 선정."""
    today = date_str or datetime.now().strftime("%Y%m%d")
    output_dir = output_dir or str(PROJECT_ROOT / "reports" / "predictions")
    ensure_dir(output_dir)

    intra_cfg = _get_intraday_config()
    max_n = int(intra_cfg.get("max_buy_candidates", 20))

    # 입력 파일 탐색
    if not candidates_path:
        preds_dir = PROJECT_ROOT / "reports" / "predictions"
        candidates_path = str(preds_dir / f"intraday_candidates_{today}.csv")
        if not Path(candidates_path).exists():
            # fallback: top100
            top100_path = preds_dir / f"top100_{today}.csv"
            if top100_path.exists():
                candidates_path = str(top100_path)
                logger.info(f"intraday_candidates 없음 — top100 fallback: {candidates_path}")
            else:
                found = sorted(preds_dir.glob("intraday_candidates_????????.csv"), reverse=True)
                if not found:
                    found = sorted(preds_dir.glob("top100_????????.csv"), reverse=True)
                if found:
                    candidates_path = str(found[0])
                    logger.warning(f"{today} 파일 없음 — 최신 파일 사용: {candidates_path}")
                else:
                    return {
                        "success": False,
                        "error": "후보 파일 없음 (intraday_candidates 및 top100)",
                        "output_file": "",
                        "candidate_count": 0,
                    }

    df = pd.read_csv(candidates_path)
    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    df[code_col] = df[code_col].astype(str).str.zfill(6)
    original_count = len(df)

    # ── prob_intraday_2pct 없으면 fallback ────────────────────────────────
    if "prob_intraday_2pct" not in df.columns:
        # 기존 probability_2pct를 legacy 이름으로 처리
        if "probability_2pct" in df.columns:
            df["prob_intraday_2pct"] = df["probability_2pct"]
            df.rename(columns={"probability_2pct": "nextday_prob_2pct"}, inplace=True)
            logger.info("probability_2pct → prob_intraday_2pct (legacy 호환)")
        else:
            # 점수 기반 대체
            score_col = next(
                (c for c in ["final_score", "momentum_score", "volume_score"] if c in df.columns),
                None,
            )
            if score_col:
                df["prob_intraday_2pct"] = (
                    df[score_col] - df[score_col].min()
                ) / (df[score_col].max() - df[score_col].min() + 1e-9) * 0.7 + 0.3
            else:
                df["prob_intraday_2pct"] = 0.5
            logger.warning("prob_intraday_2pct 없음 — 점수 기반 fallback 사용")

    if "prob_intraday_3pct" not in df.columns:
        df["prob_intraday_3pct"] = df["prob_intraday_2pct"] * 0.6
    if "prob_intraday_5pct" not in df.columns:
        df["prob_intraday_5pct"] = df["prob_intraday_2pct"] * 0.3
    if "expected_max_return_pct" not in df.columns:
        df["expected_max_return_pct"] = df["prob_intraday_2pct"] * 2.5
    if "risk_score" not in df.columns:
        df["risk_score"] = 1 - df["prob_intraday_2pct"]

    # ── 필터 초기화 ───────────────────────────────────────────────────────
    if "filter_pass" not in df.columns:
        df["filter_pass"] = True
    if "filter_fail_reason" not in df.columns:
        df["filter_fail_reason"] = ""
    df["filter_pass"] = df["filter_pass"].astype(bool)

    # 이미 filter_pass=False인 종목 제외 유지
    # 추가 필터 적용

    # 필터 1: 확률 임계값
    min_prob = float(intra_cfg.get("min_prob_2pct", 0.58))
    fallback_prob = float(intra_cfg.get("fallback_min_prob_2pct", 0.52))

    def _apply_filter(mask_fail: pd.Series, reason: str) -> None:
        fail_mask = mask_fail & df["filter_pass"]
        df.loc[fail_mask, "filter_pass"] = False
        df.loc[fail_mask & (df["filter_fail_reason"] == ""), "filter_fail_reason"] = reason

    # 확률 필터 (우선 0.58, 부족하면 0.52로 완화)
    mask_prob_fail = df["prob_intraday_2pct"] < min_prob
    pass_after_prob = (~mask_prob_fail & df["filter_pass"]).sum()
    if pass_after_prob < 5:
        min_prob = fallback_prob
        mask_prob_fail = df["prob_intraday_2pct"] < fallback_prob
        logger.info(f"확률 임계값 완화: {min_prob:.2f} (후보 부족)")
    _apply_filter(mask_prob_fail, f"prob_2pct<{min_prob:.2f}")

    # 필터 2: 거래대금 (일봉 기준 prev_trading_value 또는 trading_value)
    tv_col = next(
        (c for c in ["prev_trading_value", "trading_value_until_entry", "trading_value"] if c in df.columns),
        None,
    )
    min_tv = float(intra_cfg.get("min_trading_value_krw", 30_000_000_000))
    fallback_tv = float(intra_cfg.get("fallback_trading_value_krw", 10_000_000_000))
    if tv_col:
        pass_after_tv = (df[tv_col] >= min_tv).sum()
        if pass_after_tv < 5:
            min_tv = fallback_tv
        _apply_filter(df[tv_col] < min_tv, f"tv<{int(min_tv/1e8)}억")

    # 필터 3: 하락 종목 원칙적 제외 (gap_rate < 0)
    if "gap_rate" in df.columns:
        _apply_filter(df["gap_rate"] < 0, "하락갭")

    # 필터 4: 과도한 갭 상승 후 밀림 제외 (gap_rate > 8%)
    if "gap_rate" in df.columns:
        _apply_filter(df["gap_rate"] > 0.08, "과도한갭")

    # ── 최종 점수 계산 ────────────────────────────────────────────────────
    # 거래대금 정규화 점수
    if tv_col and df[tv_col].max() > 0:
        tv_score = (df[tv_col] / df[tv_col].max()).clip(0, 1)
    else:
        tv_score = pd.Series(0.5, index=df.index)

    # 모멘텀 점수 (gap_rate + prev_return)
    if "gap_rate" in df.columns:
        gap_score = df["gap_rate"].clip(-0.05, 0.10) / 0.10
    else:
        gap_score = pd.Series(0.5, index=df.index)

    if "prev_return_1d" in df.columns:
        prev_ret_score = df["prev_return_1d"].clip(-0.05, 0.05) / 0.05 * 0.5 + 0.5
    else:
        prev_ret_score = pd.Series(0.5, index=df.index)

    momentum_score = (gap_score * 0.5 + prev_ret_score * 0.5).clip(0, 1)

    df["final_intraday_score"] = (
        df["prob_intraday_2pct"] * 0.40 +
        tv_score * 0.20 +
        momentum_score * 0.25 +
        (1 - df["risk_score"].clip(0, 1)) * 0.15
    )

    # ── Top20 선정 ────────────────────────────────────────────────────────
    df_pass = df[df["filter_pass"]].copy()
    df_fail = df[~df["filter_pass"]].copy()

    # 통과 후보가 max_n보다 적으면 점수 하위 종목도 추가 (최대 max_n까지)
    if len(df_pass) < max_n and len(df_fail) > 0:
        needed = max_n - len(df_pass)
        extra = df_fail.nlargest(needed, "final_intraday_score")
        extra = extra.copy()
        extra["filter_pass"] = True
        extra["filter_fail_reason"] = extra["filter_fail_reason"] + " (완화)"
        df_pass = pd.concat([df_pass, extra], ignore_index=True)
        logger.info(f"후보 부족 — 필터 완화 {needed}개 추가")

    df_top20 = df_pass.nlargest(max_n, "final_intraday_score").reset_index(drop=True)
    df_top20["final_buy_rank"] = range(1, len(df_top20) + 1)

    # ── 출력 ─────────────────────────────────────────────────────────────
    output_file = os.path.join(output_dir, f"buy_top20_{today}.csv")
    df_top20.to_csv(output_file, index=False)

    selected_count = len(df_top20)
    logger.info(
        f"Top20 선정 완료: {selected_count}/{original_count}개 | "
        f"평균 prob_2pct={df_top20['prob_intraday_2pct'].mean():.4f} | "
        f"{output_file}"
    )

    return {
        "success": True,
        "output_file": output_file,
        "candidate_count": selected_count,
        "original_count": original_count,
        "filter_pass_count": int(df["filter_pass"].sum()),
        "date": today,
        "mean_prob_2pct": round(float(df_top20["prob_intraday_2pct"].mean()), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="오늘 당일 매수 후보 Top20 선정")
    parser.add_argument("--date", default=None)
    parser.add_argument("--input", default=None, help="intraday_candidates 파일 경로")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    result = select_buy_top20(
        date_str=args.date,
        candidates_path=args.input,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
