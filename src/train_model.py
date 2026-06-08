"""모델 학습 모듈.

LightGBM (또는 RandomForest) 분류 모델을 학습합니다.
시간 순서 기준으로 학습/검증을 분리합니다 (랜덤 셔플 없음).
Precision@Top20/50/100, Top100 Hit Rate를 핵심 지표로 평가합니다.

실행:
    python src/train_model.py
"""

import json
import os
import sys
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

sys.path.insert(0, os.path.dirname(__file__))
from make_features import FEATURE_COLUMNS
from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/train_model.log")
cfg = load_config("config.yaml")


def load_dataset(
    labels_path: str,
    feature_cols: List[str],
    target_col: str,
) -> Tuple[pd.DataFrame, pd.Series]:
    """학습 데이터셋 로드 및 전처리."""
    df = pd.read_csv(labels_path, parse_dates=["date"])

    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    df[code_col] = df[code_col].astype(str).str.zfill(6)
    df = df.sort_values(["date", code_col]).reset_index(drop=True)

    available_features = [c for c in feature_cols if c in df.columns]
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        logger.warning(f"피처 컬럼 없음 (건너뜀): {missing}")

    df = df.dropna(subset=[target_col] + available_features)

    X = df[available_features + ["date", code_col]]
    y = df[target_col].astype(int)

    logger.info(
        f"데이터셋: {len(df):,}행, {len(available_features)}개 피처, "
        f"양성 비율={y.mean():.3f}"
    )
    return X, y


def time_series_split(
    X: pd.DataFrame,
    y: pd.Series,
    train_ratio: float = 0.8,
) -> Tuple:
    """시간 순서 기반 학습/검증 분리."""
    dates = X["date"].sort_values().unique()
    split_idx = int(len(dates) * train_ratio)
    split_date = dates[split_idx]

    train_mask = X["date"] < split_date
    val_mask = X["date"] >= split_date

    code_col = "stock_code" if "stock_code" in X.columns else "ticker"
    drop_cols = ["date", code_col]

    X_train = X[train_mask].drop(columns=drop_cols)
    X_val = X[val_mask].drop(columns=drop_cols)
    y_train = y[train_mask]
    y_val = y[val_mask]

    logger.info(
        f"학습: {len(X_train):,}행 (~ {split_date.date()}), "
        f"검증: {len(X_val):,}행 ({split_date.date()} ~)"
    )
    return X_train, X_val, y_train, y_val, X[val_mask]


def train_lightgbm(X_train, y_train, X_val, y_val):
    """LightGBM 모델 학습."""
    try:
        import lightgbm as lgb
        model = lgb.LGBMClassifier(
            n_estimators=cfg["model"].get("n_estimators", 500),
            max_depth=cfg["model"].get("max_depth", 6),
            learning_rate=cfg["model"].get("learning_rate", 0.05),
            random_state=cfg["model"].get("random_state", 42),
            n_jobs=-1,
            verbose=-1,
            class_weight="balanced",
        )
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(False)],
        )
        logger.info("LightGBM 학습 완료")
        return model
    except ImportError:
        logger.warning("LightGBM 미설치. RandomForest로 대체합니다.")
        return train_random_forest(X_train, y_train)


def train_random_forest(X_train, y_train):
    """RandomForest 모델 학습 (LightGBM 대체용)."""
    from sklearn.ensemble import RandomForestClassifier
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=10,
        random_state=cfg["model"].get("random_state", 42),
        n_jobs=-1,
        class_weight="balanced",
    )
    model.fit(X_train, y_train)
    logger.info("RandomForest 학습 완료")
    return model


def evaluate_model(model, X_val, y_val, val_df_full) -> Dict:
    """모델 성과 평가 — Top20/50/100 Precision 및 Hit Rate 포함."""
    proba = model.predict_proba(X_val)[:, 1]
    pred = (proba >= 0.5).astype(int)

    metrics = {
        "accuracy": float(accuracy_score(y_val, pred)),
        "precision": float(precision_score(y_val, pred, zero_division=0)),
        "recall": float(recall_score(y_val, pred, zero_division=0)),
        "f1": float(f1_score(y_val, pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_val, proba)),
    }

    val_df = val_df_full.copy()
    val_df["proba"] = proba
    val_df["actual"] = y_val.values

    next_day_col = None
    for col in ["next_day_high_return", "next_day_return"]:
        if col in val_df_full.columns:
            val_df[col] = val_df_full[col].values
            next_day_col = col
            break

    for top_n in [20, 50, 100]:
        hits = []
        returns = []
        for date, grp in val_df.groupby("date"):
            if len(grp) < top_n:
                continue
            top = grp.nlargest(top_n, "proba")
            hits.append(top["actual"].mean())
            if next_day_col and next_day_col in top.columns:
                returns.append(top[next_day_col].mean())

        if hits:
            metrics[f"precision_at_{top_n}"] = float(np.mean(hits))
            metrics[f"top{top_n}_hit_days"] = len(hits)
        else:
            metrics[f"precision_at_{top_n}"] = 0.0

        if returns:
            metrics[f"top{top_n}_avg_return"] = float(np.mean(returns))

    # Top100 2% Hit Rate
    top100_hit_rates = []
    top100_drawdowns = []
    for date, grp in val_df.groupby("date"):
        if len(grp) < 100:
            continue
        top = grp.nlargest(100, "proba")
        top100_hit_rates.append(top["actual"].mean())
        if "next_day_low_return" in val_df_full.columns:
            idx = top.index
            lows = val_df_full.loc[val_df_full.index.isin(idx), "next_day_low_return"]
            if not lows.empty:
                top100_drawdowns.append(lows.min())

    if top100_hit_rates:
        metrics["top100_hit_rate_2pct"] = float(np.mean(top100_hit_rates))
    if top100_drawdowns:
        metrics["max_drawdown_top100"] = float(np.mean(top100_drawdowns))

    return metrics


def save_evaluation_report(metrics: Dict, txt_path: str, json_path: str, model_type: str) -> None:
    """평가 결과를 TXT/JSON으로 저장."""
    ensure_dir(os.path.dirname(txt_path))
    lines = [
        "=" * 60,
        "  모델 평가 결과",
        "=" * 60,
        f"  모델 유형: {model_type}",
        "",
        "--- 전체 검증 데이터 기준 ---",
        f"  Accuracy : {metrics.get('accuracy', 0):.4f}",
        f"  Precision: {metrics.get('precision', 0):.4f}",
        f"  Recall   : {metrics.get('recall', 0):.4f}",
        f"  F1       : {metrics.get('f1', 0):.4f}",
        f"  ROC-AUC  : {metrics.get('roc_auc', 0):.4f}",
        "",
        "--- 핵심 지표 (날짜별 상위 N종목 중 실제 +2% 도달 비율) ---",
        f"  Precision@Top20 : {metrics.get('precision_at_20', 0):.4f}",
        f"  Precision@Top50 : {metrics.get('precision_at_50', 0):.4f}",
        f"  Precision@Top100: {metrics.get('precision_at_100', 0):.4f}",
        "",
        "--- Top100 성과 ---",
        f"  Top100 평균수익: {metrics.get('top100_avg_return', 0):.4f}",
        f"  Top100 2% Hit Rate: {metrics.get('top100_hit_rate_2pct', 0):.4f}",
        f"  Top100 최대하락: {metrics.get('max_drawdown_top100', 0):.4f}",
        "=" * 60,
        "  ※ 수익을 보장하지 않습니다. 백테스트 결과는 미래를 보장하지 않습니다.",
        "=" * 60,
    ]
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    logger.info(f"평가 결과 저장: {txt_path}, {json_path}")


def main() -> None:
    labels_path = cfg["data"]["processed_labels_path"]
    model_path = cfg["paths"]["model_path"]
    feature_cols_path = "models/feature_columns.json"
    eval_txt = "reports/model_evaluation.txt"
    eval_json = "reports/model_evaluation.json"
    target_col = cfg["model"].get("target_column", "target_2pct_next_morning")
    train_ratio = cfg["model"].get("train_ratio", 0.8)
    model_type_cfg = cfg["model"].get("model_type", "lightgbm")

    logger.info("=== 모델 학습 시작 ===")

    if not os.path.exists(labels_path):
        logger.error(f"라벨 파일 없음: {labels_path}")
        logger.error("먼저 실행: python src/make_labels.py")
        sys.exit(1)

    X, y = load_dataset(labels_path, FEATURE_COLUMNS, target_col)
    X_train, X_val, y_train, y_val, val_df_full = time_series_split(X, y, train_ratio)

    if model_type_cfg == "lightgbm":
        model = train_lightgbm(X_train, y_train, X_val, y_val)
    else:
        model = train_random_forest(X_train, y_train)

    actual_model_type = type(model).__name__
    metrics = evaluate_model(model, X_val, y_val, val_df_full)

    logger.info(
        f"평가: Accuracy={metrics['accuracy']:.4f}, "
        f"Precision@Top20={metrics.get('precision_at_20', 0):.4f}, "
        f"Precision@Top100={metrics.get('precision_at_100', 0):.4f}, "
        f"ROC-AUC={metrics['roc_auc']:.4f}"
    )

    ensure_dir(os.path.dirname(model_path))
    joblib.dump(model, model_path)
    logger.info(f"모델 저장: {model_path}")

    # 사용된 피처 컬럼 목록 저장
    available_features = [c for c in FEATURE_COLUMNS if c in X_train.columns]
    ensure_dir("models")
    with open(feature_cols_path, "w", encoding="utf-8") as f:
        json.dump(available_features, f, ensure_ascii=False, indent=2)
    logger.info(f"피처 컬럼 목록 저장: {feature_cols_path}")

    save_evaluation_report(metrics, eval_txt, eval_json, actual_model_type)
    logger.info("=== 모델 학습 완료 ===")


if __name__ == "__main__":
    main()
