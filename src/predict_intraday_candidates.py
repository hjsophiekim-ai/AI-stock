"""장중 매매 예측 실행.

intraday_latest_features.csv에 학습된 intraday 모델 적용.
출력: reports/predictions/intraday_candidates_YYYYMMDD.csv

실행:
    python src/predict_intraday_candidates.py
    python src/predict_intraday_candidates.py --date 20260615
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

logger = setup_logger(__name__, "logs/predict_intraday_candidates.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_model_and_features(target_key: str = "2pct"):
    """모델 + feature 컬럼 로드."""
    import joblib

    models_dir = PROJECT_ROOT / "models"
    model_path = models_dir / f"intraday_{target_key}_model.joblib"
    feat_path = models_dir / "intraday_feature_columns.json"

    if not model_path.exists():
        raise FileNotFoundError(f"장중 모델 없음: {model_path}")

    model = joblib.load(model_path)
    feat_cols = []
    if feat_path.exists():
        with open(feat_path, encoding="utf-8") as f:
            feat_cols = json.load(f)

    return model, feat_cols


def predict_intraday(
    date_str: str = None,
    features_path: str = None,
    output_dir: str = None,
) -> dict:
    """장중 매매 예측 실행."""
    today = date_str or datetime.now().strftime("%Y%m%d")
    features_path = features_path or str(
        PROJECT_ROOT / "data" / "processed" / "intraday_latest_features.csv"
    )
    output_dir = output_dir or str(PROJECT_ROOT / "reports" / "predictions")
    ensure_dir(output_dir)

    if not Path(features_path).exists():
        return {
            "success": False,
            "error": f"피처 파일 없음: {features_path}",
            "output_file": "",
            "candidate_count": 0,
        }

    df = pd.read_csv(features_path, parse_dates=["date"])
    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    df[code_col] = df[code_col].astype(str).str.zfill(6)

    # 오늘 날짜 필터 (또는 최신 날짜)
    today_dt = pd.to_datetime(today)
    today_df = df[df["date"] == today_dt]
    if today_df.empty:
        max_date = df["date"].max()
        today_df = df[df["date"] == max_date]
        logger.warning(f"{today} 데이터 없음 — 최신 날짜 {max_date.date()} 사용")
        actual_date = max_date.strftime("%Y%m%d")
    else:
        actual_date = today

    # 2pct 모델 (main)
    try:
        model_2pct, feat_cols = _load_model_and_features("2pct")
    except FileNotFoundError as e:
        return {"success": False, "error": str(e), "output_file": "", "candidate_count": 0}

    available_feats = [c for c in feat_cols if c in today_df.columns]
    X = today_df[available_feats].fillna(today_df[available_feats].median())

    today_df = today_df.copy()
    today_df["prob_intraday_2pct"] = model_2pct.predict_proba(X)[:, 1]

    # 3pct, 5pct 모델 (선택)
    for key in ["3pct", "5pct"]:
        col = f"prob_intraday_{key}"
        try:
            m, _ = _load_model_and_features(key)
            today_df[col] = m.predict_proba(X)[:, 1]
        except Exception:
            today_df[col] = today_df["prob_intraday_2pct"] * (0.6 if key == "3pct" else 0.3)

    # 예상 수익 (prob 가중 기대값)
    today_df["expected_max_return_pct"] = (
        today_df["prob_intraday_2pct"] * 2.0 +
        today_df["prob_intraday_3pct"] * 1.0 +
        today_df["prob_intraday_5pct"] * 2.0
    )

    # 리스크 스코어 (확률 반비례 + 변동성)
    today_df["risk_score"] = (
        (1 - today_df["prob_intraday_2pct"]).clip(0, 1) * 0.7 +
        today_df.get("prev_volatility_20", pd.Series(0.02, index=today_df.index)).clip(0, 0.1) / 0.1 * 0.3
    )

    # 갭 필터 적용
    gap = today_df.get("gap_rate", pd.Series(0.0, index=today_df.index))
    prev_ret = today_df.get("prev_return_1d", pd.Series(0.0, index=today_df.index))

    today_df["filter_pass"] = True
    today_df["filter_fail_reason"] = ""

    # 하락 갭 (gap_rate < -3%) 제외
    mask_gap_down = gap < -0.03
    today_df.loc[mask_gap_down, "filter_pass"] = False
    today_df.loc[mask_gap_down, "filter_fail_reason"] = "하락갭"

    # 전일 급락 (-5% 이하) 제외
    mask_prev_drop = prev_ret < -0.05
    today_df.loc[mask_prev_drop & today_df["filter_pass"], "filter_pass"] = False
    today_df.loc[mask_prev_drop & ~today_df["filter_fail_reason"].astype(bool), "filter_fail_reason"] = "전일급락"

    # 정렬 및 출력
    today_df = today_df.sort_values("prob_intraday_2pct", ascending=False).reset_index(drop=True)

    output_file = os.path.join(output_dir, f"intraday_candidates_{actual_date}.csv")
    today_df.to_csv(output_file, index=False)

    count = len(today_df)
    pass_count = today_df["filter_pass"].sum()
    logger.info(
        f"장중 예측 완료: {count}개 후보 | 필터 통과: {pass_count}개 | {output_file}"
    )

    return {
        "success": True,
        "output_file": output_file,
        "candidate_count": int(count),
        "filter_pass_count": int(pass_count),
        "date": actual_date,
        "mean_prob_2pct": round(float(today_df["prob_intraday_2pct"].mean()), 4),
        "max_prob_2pct": round(float(today_df["prob_intraday_2pct"].max()), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="장중 매매 예측")
    parser.add_argument("--date", default=None)
    parser.add_argument("--features", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    result = predict_intraday(
        date_str=args.date,
        features_path=args.features,
        output_dir=args.output_dir,
    )
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
