"""장중 매매 전략 백테스트.

오전 10:30~11:30 매수 → 당일 2%, 3%, 5% 도달 여부 검증.
일봉 데이터에서 open을 entry_price로 근사.

실행:
    python src/backtest_intraday_strategy.py
    python src/backtest_intraday_strategy.py --top-n 20 --min-prob 0.58
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

logger = setup_logger(__name__, "logs/backtest_intraday_strategy.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_predictions_and_labels(
    features_path: str,
    labels_path: str,
    model_path: str,
    feat_cols_path: str,
) -> pd.DataFrame:
    """피처 + 라벨 + 예측확률 병합."""
    import joblib

    df_f = pd.read_csv(features_path, parse_dates=["date"])
    df_l = pd.read_csv(labels_path, parse_dates=["date"])

    code_col = "stock_code"
    label_cols = [
        "target_intraday_2pct", "target_intraday_3pct", "target_intraday_5pct",
        "good_trade_label", "max_future_return_pct", "max_adverse_return_pct",
    ]
    label_cols = [c for c in label_cols + [code_col, "date"] if c in df_l.columns]
    df = df_f.merge(df_l[label_cols], on=["date", code_col], how="inner")

    model = joblib.load(model_path)
    with open(feat_cols_path, encoding="utf-8") as f:
        feat_cols = json.load(f)

    avail = [c for c in feat_cols if c in df.columns]
    X = df[avail].fillna(df[avail].median())
    df["prob_intraday_2pct"] = model.predict_proba(X)[:, 1]
    return df


def run_backtest(
    top_n: int = 20,
    min_prob: float = 0.58,
    slippage_pct: float = 0.15,
    commission_tax_pct: float = 0.23,
    target_profit_pct: float = 2.0,
    stop_loss_pct: float = 2.0,
    train_end_ratio: float = 0.8,
) -> dict:
    """walk-forward 백테스트."""
    features_path = str(PROJECT_ROOT / "data" / "processed" / "intraday_features.csv")
    labels_path = str(PROJECT_ROOT / "data" / "processed" / "intraday_labeled_dataset.csv")
    model_path = str(PROJECT_ROOT / "models" / "intraday_2pct_model.joblib")
    feat_cols_path = str(PROJECT_ROOT / "models" / "intraday_feature_columns.json")

    for p in [features_path, labels_path, model_path, feat_cols_path]:
        if not Path(p).exists():
            return {"success": False, "error": f"파일 없음: {p}"}

    df = _load_predictions_and_labels(features_path, labels_path, model_path, feat_cols_path)
    df = df.sort_values(["date", "stock_code"]).reset_index(drop=True)

    # 검증 구간만 백테스트 (train에서 본 데이터 제외)
    dates = df["date"].sort_values().unique()
    split_idx = int(len(dates) * train_end_ratio)
    split_date = dates[split_idx]
    df_test = df[df["date"] >= split_date].copy()

    if df_test.empty:
        return {"success": False, "error": "검증 구간 데이터 없음"}

    total_cost_pct = slippage_pct + commission_tax_pct  # 편도 비용

    trades = []
    for date, day_df in df_test.groupby("date"):
        # 확률 임계값 필터
        candidates = day_df[day_df["prob_intraday_2pct"] >= min_prob]
        if candidates.empty:
            candidates = day_df.nlargest(top_n, "prob_intraday_2pct")
        else:
            candidates = candidates.nlargest(top_n, "prob_intraday_2pct")

        for _, row in candidates.iterrows():
            entry = float(row.get("entry_price", row.get("open", 0)) or 0)
            if entry <= 0:
                continue
            max_ret = float(row.get("max_future_return_pct", 0) or 0)
            min_ret = float(row.get("max_adverse_return_pct", 0) or 0)

            # exit rule: +target_profit% 익절 OR -stop_loss% 손절 OR 장마감
            hit_profit = max_ret >= target_profit_pct
            hit_stop = min_ret <= -stop_loss_pct

            # 비용 차감
            if hit_profit:
                net_return = target_profit_pct - total_cost_pct * 2
            elif hit_stop:
                net_return = -stop_loss_pct - total_cost_pct * 2
            else:
                net_return = max_ret - total_cost_pct * 2

            trades.append({
                "date": date,
                "stock_code": row.get("stock_code", ""),
                "prob_2pct": row["prob_intraday_2pct"],
                "hit_2pct": int(hit_profit),
                "hit_3pct": int(max_ret >= 3.0),
                "hit_5pct": int(max_ret >= 5.0),
                "stop_hit": int(hit_stop),
                "max_return_pct": round(max_ret, 3),
                "min_return_pct": round(min_ret, 3),
                "net_return_pct": round(net_return, 3),
            })

    if not trades:
        return {"success": False, "error": "거래 없음"}

    df_trades = pd.DataFrame(trades)
    total = len(df_trades)
    hit2 = df_trades["hit_2pct"].mean()
    hit3 = df_trades["hit_3pct"].mean()
    hit5 = df_trades["hit_5pct"].mean()
    stop_rate = df_trades["stop_hit"].mean()
    avg_ret = df_trades["net_return_pct"].mean()
    med_ret = df_trades["net_return_pct"].median()
    win_rate = (df_trades["net_return_pct"] > 0).mean()

    # 누적수익 (단순 합)
    cumulative = df_trades.groupby("date")["net_return_pct"].mean()
    cum_ret = cumulative.sum()
    cum_series = cumulative.cumsum()
    running_max = cum_series.cummax()
    drawdown = cum_series - running_max
    max_dd = float(drawdown.min())

    wins = df_trades[df_trades["net_return_pct"] > 0]["net_return_pct"]
    losses = df_trades[df_trades["net_return_pct"] < 0]["net_return_pct"].abs()
    profit_factor = (wins.sum() / losses.sum()) if losses.sum() > 0 else float("inf")

    report = {
        "success": True,
        "backtest_period": f"{df_test['date'].min().date()} ~ {df_test['date'].max().date()}",
        "total_trades": total,
        "daily_avg_candidates": round(total / df_test["date"].nunique(), 1),
        "min_prob_filter": min_prob,
        "top_n": top_n,
        "target_profit_pct": target_profit_pct,
        "stop_loss_pct": stop_loss_pct,
        "slippage_pct": slippage_pct,
        "commission_tax_pct": commission_tax_pct,
        "hit_rate_2pct": round(hit2, 4),
        "hit_rate_3pct": round(hit3, 4),
        "hit_rate_5pct": round(hit5, 4),
        "stop_loss_rate": round(stop_rate, 4),
        "win_rate": round(win_rate, 4),
        "avg_net_return_pct": round(avg_ret, 4),
        "median_net_return_pct": round(med_ret, 4),
        "cumulative_return_pct": round(cum_ret, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "profit_factor": round(profit_factor, 2),
        "quality_grade": (
            "강함 (65%+)" if hit2 >= 0.65 else
            "양호 (60%+)" if hit2 >= 0.60 else
            "사용가능 (55%+)" if hit2 >= 0.55 else
            "주의 (<55%)"
        ),
    }

    # 저장
    ensure_dir(str(PROJECT_ROOT / "reports" / "backtest"))
    today = datetime.now().strftime("%Y%m%d")
    result_path = str(PROJECT_ROOT / "reports" / "backtest" / f"intraday_backtest_{today}.json")
    trades_path = str(PROJECT_ROOT / "reports" / "backtest" / f"intraday_backtest_trades_{today}.csv")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    df_trades.to_csv(trades_path, index=False)

    logger.info(
        f"백테스트 완료: hit_rate_2pct={hit2:.4f} | avg_return={avg_ret:.4f}% | "
        f"profit_factor={profit_factor:.2f}"
    )
    report["result_path"] = result_path
    report["trades_path"] = trades_path
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="장중 매매 전략 백테스트")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--min-prob", type=float, default=0.58)
    parser.add_argument("--slippage", type=float, default=0.15)
    parser.add_argument("--commission-tax", type=float, default=0.23)
    parser.add_argument("--target-profit", type=float, default=2.0)
    parser.add_argument("--stop-loss", type=float, default=2.0)
    args = parser.parse_args()

    result = run_backtest(
        top_n=args.top_n,
        min_prob=args.min_prob,
        slippage_pct=args.slippage,
        commission_tax_pct=args.commission_tax,
        target_profit_pct=args.target_profit,
        stop_loss_pct=args.stop_loss,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("success"):
        print(f"\n=== 백테스트 요약 ===")
        print(f"  기간: {result['backtest_period']}")
        print(f"  전체 거래: {result['total_trades']}건")
        print(f"  hit_rate_2pct: {result['hit_rate_2pct']*100:.1f}%  [{result['quality_grade']}]")
        print(f"  hit_rate_3pct: {result['hit_rate_3pct']*100:.1f}%")
        print(f"  hit_rate_5pct: {result['hit_rate_5pct']*100:.1f}%")
        print(f"  손절 발생률: {result['stop_loss_rate']*100:.1f}%")
        print(f"  평균 순수익률: {result['avg_net_return_pct']:.3f}%")
        print(f"  누적수익률: {result['cumulative_return_pct']:.2f}%")
        print(f"  최대 낙폭: {result['max_drawdown_pct']:.2f}%")
        print(f"  Profit Factor: {result['profit_factor']}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
