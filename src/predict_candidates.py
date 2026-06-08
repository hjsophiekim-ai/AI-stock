"""전 종목 상승확률 예측 모듈.

latest_features.csv 기반으로 전 종목의 다음 거래일 +2% 상승확률을 예측합니다.
predictions_YYYYMMDD.csv에 전체 종목 예측 결과를 저장합니다.

실행:
    python src/predict_candidates.py
"""

import json
import os
import sys
from datetime import datetime

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from make_features import FEATURE_COLUMNS
from utils import ensure_dir, get_today_str, load_config, save_csv, setup_logger

logger = setup_logger(__name__, "logs/predict_candidates.log")
cfg = load_config("config.yaml")


def load_feature_columns(feature_cols_path: str = "models/feature_columns.json") -> list:
    """저장된 피처 컬럼 목록 로드."""
    if os.path.exists(feature_cols_path):
        with open(feature_cols_path, encoding="utf-8") as f:
            cols = json.load(f)
        logger.info(f"피처 컬럼 로드: {feature_cols_path} ({len(cols)}개)")
        return cols
    return FEATURE_COLUMNS


def predict_all(
    features_path: str,
    model_path: str,
) -> pd.DataFrame:
    """최신 피처 기반 전 종목 상승확률 예측.

    Args:
        features_path: latest_features.csv 경로
        model_path: 모델 파일 경로

    Returns:
        전 종목 예측 결과 DataFrame
    """
    if not os.path.exists(features_path):
        logger.error(f"피처 파일 없음: {features_path}")
        logger.error("먼저 실행: python src/make_labels.py  (latest_features.csv 생성)")
        sys.exit(1)

    if not os.path.exists(model_path):
        logger.error(f"모델 파일 없음: {model_path}")
        logger.error("먼저 실행: python src/train_model.py")
        sys.exit(1)

    df = pd.read_csv(features_path, parse_dates=["date"])
    model = joblib.load(model_path)

    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    name_col = "stock_name" if "stock_name" in df.columns else "name"

    df[code_col] = df[code_col].astype(str).str.zfill(6)

    # 종목별 최신 행
    latest_df = df.sort_values("date").groupby(code_col).last().reset_index()
    latest_date = latest_df["date"].max()
    logger.info(f"예측 기준 날짜: {latest_date}, 종목 수: {len(latest_df)}")

    feature_cols = load_feature_columns()
    available_features = [c for c in feature_cols if c in latest_df.columns]
    missing = [c for c in feature_cols if c not in latest_df.columns]
    if missing:
        logger.warning(f"피처 없음 (0으로 채움): {missing}")

    X = latest_df[available_features].fillna(0)

    proba = model.predict_proba(X)[:, 1]
    latest_df["probability_2pct"] = proba
    latest_df["prediction_score"] = proba  # 하위 호환

    # next_action_candidate: 확률 기준
    latest_df["next_action_candidate"] = (
        latest_df["probability_2pct"]
        .apply(lambda p: "매수후보" if p >= 0.5 else "관망")
    )

    # 출력 컬럼 정리
    out_cols = [
        "date", code_col, name_col, "close",
        "probability_2pct", "prediction_score",
        "trading_value", "trading_value_ma20",
        "return_1d", "return_5d", "return_20d",
        "volume_ratio_20", "volatility_20",
        "close_to_ma20", "close_to_20d_high",
        "next_action_candidate",
    ]
    # 구버전 컬럼명 호환
    out_cols_compat = [c.replace("trading_value_ma20", "tv_ma20") for c in out_cols]

    available_out = [c for c in out_cols if c in latest_df.columns]
    result = latest_df[available_out].sort_values("probability_2pct", ascending=False)

    # stock_code / stock_name 통일
    if code_col != "stock_code" and code_col in result.columns:
        result = result.rename(columns={code_col: "stock_code"})
    if name_col != "stock_name" and name_col in result.columns:
        result = result.rename(columns={name_col: "stock_name"})

    return result.reset_index(drop=True)


def main() -> None:
    today = get_today_str("%Y%m%d")
    latest_path = "data/processed/latest_features.csv"
    features_path = cfg["data"]["processed_features_path"]
    model_path = cfg["paths"]["model_path"]
    predictions_dir = cfg["paths"]["predictions_dir"]

    # latest_features.csv 우선, 없으면 features.csv에서 최신 행 사용
    if os.path.exists(latest_path):
        use_path = latest_path
    elif os.path.exists(features_path):
        logger.info(f"latest_features.csv 없음. features.csv에서 최신 행 추출")
        df = pd.read_csv(features_path, parse_dates=["date"])
        code_col = "stock_code" if "stock_code" in df.columns else "ticker"
        df[code_col] = df[code_col].astype(str).str.zfill(6)
        latest = df.sort_values("date").groupby(code_col).last().reset_index()
        tmp_path = "data/processed/latest_features.csv"
        ensure_dir("data/processed")
        latest.to_csv(tmp_path, index=False, encoding="utf-8-sig")
        use_path = tmp_path
    else:
        logger.error(f"피처 파일 없음: {features_path}")
        logger.error("먼저 실행: python src/make_features.py && python src/make_labels.py")
        sys.exit(1)

    logger.info("=== 예측 시작 ===")
    result = predict_all(use_path, model_path)

    output_path = os.path.join(predictions_dir, f"predictions_{today}.csv")
    ensure_dir(predictions_dir)
    save_csv(result, output_path)

    logger.info(f"예측 결과 저장: {output_path} ({len(result)}개 종목)")
    logger.info(f"상위 10개:\n{result.head(10).to_string(index=False)}")
    logger.info("=== 예측 완료 ===")


if __name__ == "__main__":
    main()
