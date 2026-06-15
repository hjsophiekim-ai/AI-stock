"""장중 매매 모델 학습.

target_intraday_2pct: 당일 시가 대비 고가 +2% 이상 도달 여부
날짜 기준 walk-forward split (랜덤 셔플 금지).
LightGBM → sklearn HistGradientBoosting → RandomForest 순으로 fallback.

실행:
    python src/train_intraday_model.py
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from make_intraday_features import INTRADAY_FEATURE_COLUMNS
from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/train_intraday_model.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
cfg = load_config(str(PROJECT_ROOT / "config.yaml"))

TARGETS = {
    "2pct": "target_intraday_2pct",
    "3pct": "target_intraday_3pct",
    "5pct": "target_intraday_5pct",
}

MODEL_OUTPUT_DIR = PROJECT_ROOT / "models"


def _get_classifier():
    """LightGBM → sklearn fallback."""
    try:
        import lightgbm as lgb
        return lgb.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.05,
            num_leaves=63,
            max_depth=8,
            min_child_samples=30,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=0.1,
            random_state=42,
            n_jobs=-1,
            verbose=-1,
        )
    except ImportError:
        pass
    try:
        import xgboost as xgb
        return xgb.XGBClassifier(
            n_estimators=300,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
            eval_metric="auc",
            use_label_encoder=False,
            verbosity=0,
        )
    except ImportError:
        pass
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.05,
        max_depth=8,
        min_samples_leaf=30,
        random_state=42,
    )


def load_dataset(
    features_path: str,
    labels_path: str,
    target_col: str,
) -> Tuple[pd.DataFrame, pd.Series]:
    """피처 + 라벨 데이터 로드 및 merge."""
    df_f = pd.read_csv(features_path, parse_dates=["date"])
    df_l = pd.read_csv(labels_path, parse_dates=["date"])

    code_col = "stock_code"
    merge_cols = ["date", code_col]

    label_cols = [target_col, "good_trade_label"] + merge_cols
    label_cols = [c for c in label_cols if c in df_l.columns]
    df_l = df_l[label_cols]

    df = df_f.merge(df_l, on=merge_cols, how="inner")
    df = df.sort_values(["date", code_col]).reset_index(drop=True)
    df = df.dropna(subset=[target_col])

    feat_cols = [c for c in INTRADAY_FEATURE_COLUMNS if c in df.columns]
    missing = [c for c in INTRADAY_FEATURE_COLUMNS if c not in df.columns]
    if missing:
        logger.warning(f"피처 없음 (건너뜀): {missing}")

    X = df[feat_cols + ["date", code_col]]
    y = df[target_col].astype(int)

    logger.info(
        f"데이터: {len(df):,}행 | {len(feat_cols)}개 피처 | "
        f"양성={y.mean():.3f}"
    )
    return X, y, feat_cols


def time_series_split(
    X: pd.DataFrame,
    y: pd.Series,
    train_ratio: float = 0.8,
) -> Tuple:
    """날짜 기준 학습/검증 분리."""
    dates = X["date"].sort_values().unique()
    split_idx = int(len(dates) * train_ratio)
    split_date = dates[split_idx]

    train_mask = X["date"] < split_date
    val_mask = X["date"] >= split_date

    code_col = "stock_code"
    drop_cols = [c for c in ["date", code_col] if c in X.columns]

    X_train = X[train_mask].drop(columns=drop_cols)
    X_val = X[val_mask].drop(columns=drop_cols)
    y_train = y[train_mask]
    y_val = y[val_mask]

    logger.info(
        f"학습: {len(X_train):,}행 (<{split_date.date()}) | "
        f"검증: {len(X_val):,}행 (>={split_date.date()})"
    )
    return X_train, X_val, y_train, y_val


def evaluate(model, X_val: pd.DataFrame, y_val: pd.Series, feat_cols: List[str]) -> Dict:
    """검증 지표 계산."""
    from sklearn.metrics import roc_auc_score, brier_score_loss

    probs = model.predict_proba(X_val)[:, 1]
    preds = (probs >= 0.5).astype(int)

    auc = roc_auc_score(y_val, probs) if y_val.nunique() > 1 else 0.5
    brier = brier_score_loss(y_val, probs)

    # precision@top20 (상위 20개 중 실제 양성 비율)
    top20_mask = probs >= np.percentile(probs, 95)
    top20_count = top20_mask.sum()
    precision_top5pct = y_val[top20_mask].mean() if top20_count > 0 else 0.0

    # 임계값별 precision
    precision_at = {}
    for thresh in [0.52, 0.55, 0.58, 0.60, 0.65, 0.70]:
        mask = probs >= thresh
        cnt = mask.sum()
        precision_at[f"precision_at_{int(thresh*100)}"] = float(y_val[mask].mean()) if cnt > 0 else 0.0
        precision_at[f"count_at_{int(thresh*100)}"] = int(cnt)

    metrics = {
        "auc": round(float(auc), 4),
        "brier_score": round(float(brier), 4),
        "val_size": len(y_val),
        "positive_rate": round(float(y_val.mean()), 4),
        "precision_top5pct": round(float(precision_top5pct), 4),
        **precision_at,
    }
    return metrics


def train_intraday_model(
    target_key: str = "2pct",
    features_path: str = None,
    labels_path: str = None,
    output_dir: str = None,
    train_ratio: float = 0.8,
) -> Dict:
    """장중 모델 학습 및 저장."""
    from sklearn.calibration import CalibratedClassifierCV

    features_path = features_path or str(PROJECT_ROOT / "data" / "processed" / "intraday_features.csv")
    labels_path = labels_path or str(PROJECT_ROOT / "data" / "processed" / "intraday_labeled_dataset.csv")
    output_dir = output_dir or str(MODEL_OUTPUT_DIR)
    ensure_dir(output_dir)

    target_col = TARGETS[target_key]
    logger.info(f"모델 학습 시작: target={target_col}")

    X, y, feat_cols = load_dataset(features_path, labels_path, target_col)
    X_train, X_val, y_train, y_val = time_series_split(X, y, train_ratio)

    drop_cols = [c for c in ["date", "stock_code"] if c in X_train.columns]
    X_train = X_train.drop(columns=drop_cols, errors="ignore")
    X_val = X_val.drop(columns=drop_cols, errors="ignore")

    X_train = X_train.fillna(X_train.median())
    X_val = X_val.fillna(X_train.median())

    base_clf = _get_classifier()
    logger.info(f"기본 분류기: {type(base_clf).__name__}")

    # Calibrate
    clf = CalibratedClassifierCV(base_clf, method="isotonic", cv=3)
    clf.fit(X_train, y_train)

    metrics = evaluate(clf, X_val, y_val, feat_cols)
    logger.info(f"검증 AUC={metrics['auc']:.4f} | Brier={metrics['brier_score']:.4f} | precision@5%={metrics['precision_top5pct']:.4f}")

    # 저장
    model_path = os.path.join(output_dir, f"intraday_{target_key}_model.joblib")
    joblib.dump(clf, model_path)

    feat_path = os.path.join(output_dir, "intraday_feature_columns.json")
    with open(feat_path, "w", encoding="utf-8") as f:
        json.dump(feat_cols, f, ensure_ascii=False, indent=2)

    eval_dir = str(PROJECT_ROOT / "reports" / "model_eval")
    ensure_dir(eval_dir)
    import datetime
    today = datetime.date.today().strftime("%Y%m%d")
    eval_path = os.path.join(eval_dir, f"intraday_model_eval_{today}.json")
    eval_result = {
        "target_key": target_key,
        "target_col": target_col,
        "model_type": type(base_clf).__name__,
        "n_features": len(feat_cols),
        "feature_columns": feat_cols,
        "train_rows": len(X_train),
        "val_rows": len(X_val),
        "metrics": metrics,
        "model_path": model_path,
        "trained_at": today,
    }
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(eval_result, f, ensure_ascii=False, indent=2)

    logger.info(f"모델 저장: {model_path}")
    return eval_result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="장중 매매 모델 학습")
    parser.add_argument("--target", default="2pct", choices=["2pct", "3pct", "5pct"])
    parser.add_argument("--all", action="store_true", help="2pct/3pct/5pct 모두 학습")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    args = parser.parse_args()

    targets = ["2pct", "3pct", "5pct"] if args.all else [args.target]
    for t in targets:
        result = train_intraday_model(
            target_key=t,
            train_ratio=args.train_ratio,
        )
        m = result["metrics"]
        print(f"\n=== {t} 모델 ===")
        print(f"  AUC: {m['auc']:.4f}")
        print(f"  Brier score: {m['brier_score']:.4f}")
        print(f"  Precision@top5%: {m['precision_top5pct']:.4f}")
        print(f"  Precision@58%: {m.get('precision_at_58', 0):.4f} (n={m.get('count_at_58', 0)})")
        print(f"  Precision@65%: {m.get('precision_at_65', 0):.4f} (n={m.get('count_at_65', 0)})")
        print(f"  모델 저장: {result['model_path']}")


if __name__ == "__main__":
    main()
