"""Intraday strategy backtest using real minute bars.

Entry is calculated from the 10:30-11:30 minute-bar window:
- preferred: VWAP over the entry window
- fallback: first valid bar at or after 10:30

Exit metrics are calculated from bars after entry through 15:20. The backtest
does not fall back to daily open/high/low data. If minute data is unavailable,
it returns a failure result.
"""

import argparse
import json
import os
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/backtest_intraday_strategy.log")
PROJECT_ROOT = Path(__file__).resolve().parent.parent

ENTRY_START = time(10, 30)
ENTRY_END = time(11, 30)
EXIT_END = time(15, 20)


def _normalize_code(value) -> str:
    try:
        return str(int(float(str(value).strip()))).zfill(6)
    except Exception:
        return str(value).strip().zfill(6)


def _load_predictions(
    features_path: str,
    model_path: str,
    feat_cols_path: str,
) -> pd.DataFrame:
    """Load features and add prob_intraday_2pct from the trained model."""
    import joblib

    df = pd.read_csv(features_path, parse_dates=["date"])
    code_col = "stock_code" if "stock_code" in df.columns else "ticker"
    df[code_col] = df[code_col].apply(_normalize_code)
    if code_col != "stock_code":
        df = df.rename(columns={code_col: "stock_code"})

    model = joblib.load(model_path)
    with open(feat_cols_path, encoding="utf-8") as f:
        feat_cols = json.load(f)

    avail = [c for c in feat_cols if c in df.columns]
    if not avail:
        raise ValueError("No model feature columns are available in intraday_features.csv")
    X = df[avail].fillna(df[avail].median(numeric_only=True))
    df["prob_intraday_2pct"] = model.predict_proba(X)[:, 1]
    return df


def _find_minute_files(raw_intraday_dir: Path) -> list[Path]:
    if not raw_intraday_dir.exists():
        return []
    patterns = ["*.csv", "*/*.csv", "*/*/*.csv"]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(raw_intraday_dir.glob(pattern))
    return sorted({p.resolve() for p in files if p.is_file()})


def _read_minute_file(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    dt_col = next((c for c in ("datetime", "date_time", "timestamp", "time") if c in df.columns), None)
    if dt_col is None:
        raise ValueError(f"datetime column not found: {path}")

    out = df.copy()
    out["datetime"] = pd.to_datetime(out[dt_col], errors="coerce")
    out = out.dropna(subset=["datetime"])

    code_col = next((c for c in ("stock_code", "ticker", "code", "symbol") if c in out.columns), None)
    if code_col:
        out["stock_code"] = out[code_col].apply(_normalize_code)
    else:
        out["stock_code"] = path.stem[:6].zfill(6)

    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            raise ValueError(f"{col} column not found: {path}")
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=["open", "high", "low", "close"])
    if "volume" not in out.columns:
        out["volume"] = 0
    out["date"] = out["datetime"].dt.normalize()
    return out[["datetime", "date", "stock_code", "open", "high", "low", "close", "volume"]]


def _load_minute_bars(raw_intraday_dir: Path) -> pd.DataFrame:
    files = _find_minute_files(raw_intraday_dir)
    if not files:
        raise FileNotFoundError(f"분봉 데이터 없음: {raw_intraday_dir}")

    parts = []
    errors = []
    for path in files:
        try:
            part = _read_minute_file(path)
            if not part.empty:
                parts.append(part)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")

    if not parts:
        detail = "; ".join(errors[:5])
        raise FileNotFoundError(f"분봉 데이터 없음: {raw_intraday_dir}. {detail}")

    bars = pd.concat(parts, ignore_index=True)
    bars = bars.sort_values(["date", "stock_code", "datetime"]).reset_index(drop=True)
    return bars


def _entry_and_path_metrics(day_bars: pd.DataFrame, target_profit_pct: float) -> Optional[dict]:
    if day_bars.empty:
        return None

    t = day_bars["datetime"].dt.time
    entry_window = day_bars[(t >= ENTRY_START) & (t <= ENTRY_END)].copy()
    if entry_window.empty:
        entry_window = day_bars[t >= ENTRY_START].head(1).copy()
    if entry_window.empty:
        return None

    first_entry_time = entry_window.iloc[0]["datetime"]
    vol = pd.to_numeric(entry_window["volume"], errors="coerce").fillna(0)
    typical = (entry_window["high"] + entry_window["low"] + entry_window["close"]) / 3
    if vol.sum() > 0:
        entry_price = float((typical * vol).sum() / vol.sum())
        entry_method = "entry_window_vwap"
    else:
        entry_price = float(entry_window.iloc[0]["close"])
        entry_method = "first_valid_bar_after_1030"

    if entry_price <= 0:
        return None

    t_all = day_bars["datetime"].dt.time
    future = day_bars[(day_bars["datetime"] >= first_entry_time) & (t_all <= EXIT_END)].copy()
    if future.empty:
        return None

    future_high = float(future["high"].max())
    future_low = float(future["low"].min())
    max_future_return_pct = (future_high / entry_price - 1.0) * 100.0
    max_adverse_return_pct = (future_low / entry_price - 1.0) * 100.0

    target_price = entry_price * (1.0 + target_profit_pct / 100.0)
    hit_rows = future[future["high"] >= target_price]
    time_to_2pct_minutes = None
    if not hit_rows.empty:
        hit_time = hit_rows.iloc[0]["datetime"]
        time_to_2pct_minutes = int((hit_time - first_entry_time).total_seconds() // 60)

    return {
        "entry_time": first_entry_time,
        "entry_method": entry_method,
        "entry_price": entry_price,
        "future_high_after_entry": future_high,
        "future_low_after_entry": future_low,
        "max_future_return_pct": max_future_return_pct,
        "max_adverse_return_pct": max_adverse_return_pct,
        "time_to_2pct_minutes": time_to_2pct_minutes,
    }


def run_backtest(
    top_n: int = 20,
    min_prob: float = 0.58,
    slippage_pct: float = 0.15,
    commission_tax_pct: float = 0.23,
    target_profit_pct: float = 2.0,
    stop_loss_pct: float = 2.0,
    train_end_ratio: float = 0.8,
) -> dict:
    cfg = load_config(str(PROJECT_ROOT / "config.yaml"))
    features_path = str(PROJECT_ROOT / "data" / "processed" / "intraday_features.csv")
    model_path = str(PROJECT_ROOT / "models" / "intraday_2pct_model.joblib")
    feat_cols_path = str(PROJECT_ROOT / "models" / "intraday_feature_columns.json")
    raw_intraday_dir = PROJECT_ROOT / cfg.get("data", {}).get("raw_intraday_dir", "data/intraday")

    for p in [features_path, model_path, feat_cols_path]:
        if not Path(p).exists():
            return {"success": False, "error": f"파일 없음: {p}"}

    try:
        minute_bars = _load_minute_bars(raw_intraday_dir)
    except Exception as exc:
        return {"success": False, "error": f"분봉 데이터 없음: {exc}"}

    try:
        df = _load_predictions(features_path, model_path, feat_cols_path)
    except Exception as exc:
        return {"success": False, "error": f"예측 데이터 생성 실패: {exc}"}

    df = df.sort_values(["date", "stock_code"]).reset_index(drop=True)
    dates = df["date"].sort_values().unique()
    if len(dates) < 2:
        return {"success": False, "error": "검증 구간 데이터 없음"}
    split_idx = min(int(len(dates) * train_end_ratio), len(dates) - 1)
    split_date = dates[split_idx]
    df_test = df[df["date"] >= split_date].copy()
    if df_test.empty:
        return {"success": False, "error": "검증 구간 데이터 없음"}

    minute_bars["date"] = pd.to_datetime(minute_bars["date"])
    total_cost_pct = (slippage_pct + commission_tax_pct) * 2

    trades = []
    skipped_no_bars = 0
    for date, day_df in df_test.groupby("date"):
        candidates = day_df[day_df["prob_intraday_2pct"] >= min_prob]
        candidates = (candidates if not candidates.empty else day_df).nlargest(top_n, "prob_intraday_2pct")
        day_key = pd.to_datetime(date).normalize()

        for _, row in candidates.iterrows():
            code = _normalize_code(row.get("stock_code", ""))
            bars = minute_bars[(minute_bars["date"] == day_key) & (minute_bars["stock_code"] == code)]
            metrics = _entry_and_path_metrics(bars, target_profit_pct)
            if metrics is None:
                skipped_no_bars += 1
                continue

            max_ret = float(metrics["max_future_return_pct"])
            min_ret = float(metrics["max_adverse_return_pct"])
            hit_profit = max_ret >= target_profit_pct
            hit_stop = min_ret <= -stop_loss_pct
            if hit_profit:
                net_return = target_profit_pct - total_cost_pct
            elif hit_stop:
                net_return = -stop_loss_pct - total_cost_pct
            else:
                exit_price = float(bars[bars["datetime"].dt.time <= EXIT_END].iloc[-1]["close"])
                net_return = (exit_price / metrics["entry_price"] - 1.0) * 100.0 - total_cost_pct

            trades.append({
                "date": day_key.strftime("%Y-%m-%d"),
                "stock_code": code,
                "prob_intraday_2pct": float(row["prob_intraday_2pct"]),
                "entry_time": metrics["entry_time"].isoformat(),
                "entry_method": metrics["entry_method"],
                "entry_price": round(metrics["entry_price"], 4),
                "future_high_after_entry": round(metrics["future_high_after_entry"], 4),
                "max_future_return_pct": round(max_ret, 4),
                "max_adverse_return_pct": round(min_ret, 4),
                "time_to_2pct_minutes": metrics["time_to_2pct_minutes"],
                "hit_2pct": int(hit_profit),
                "hit_3pct": int(max_ret >= 3.0),
                "hit_5pct": int(max_ret >= 5.0),
                "stop_hit": int(hit_stop),
                "net_return_pct": round(net_return, 4),
            })

    if not trades:
        return {
            "success": False,
            "error": "분봉 데이터 없음: 검증 후보와 매칭되는 10:30~15:20 분봉이 없습니다.",
            "skipped_no_minute_bars": skipped_no_bars,
        }

    df_trades = pd.DataFrame(trades)
    daily = df_trades.groupby("date")["net_return_pct"].mean()
    cum_series = daily.cumsum()
    drawdown = cum_series - cum_series.cummax()
    wins = df_trades[df_trades["net_return_pct"] > 0]["net_return_pct"]
    losses = df_trades[df_trades["net_return_pct"] < 0]["net_return_pct"].abs()
    profit_factor = float(wins.sum() / losses.sum()) if losses.sum() > 0 else float("inf")

    report = {
        "success": True,
        "entry_basis": "10:30~11:30 VWAP; fallback first valid minute bar at or after 10:30",
        "future_high_basis": "minute-bar high after entry through 15:20",
        "backtest_period": f"{df_trades['date'].min()} ~ {df_trades['date'].max()}",
        "total_trades": int(len(df_trades)),
        "skipped_no_minute_bars": int(skipped_no_bars),
        "min_prob_filter": float(min_prob),
        "top_n": int(top_n),
        "target_profit_pct": float(target_profit_pct),
        "stop_loss_pct": float(stop_loss_pct),
        "slippage_pct": float(slippage_pct),
        "commission_tax_pct": float(commission_tax_pct),
        "hit_rate_2pct": round(float(df_trades["hit_2pct"].mean()), 4),
        "hit_rate_3pct": round(float(df_trades["hit_3pct"].mean()), 4),
        "hit_rate_5pct": round(float(df_trades["hit_5pct"].mean()), 4),
        "stop_loss_rate": round(float(df_trades["stop_hit"].mean()), 4),
        "win_rate": round(float((df_trades["net_return_pct"] > 0).mean()), 4),
        "avg_net_return_pct": round(float(df_trades["net_return_pct"].mean()), 4),
        "median_net_return_pct": round(float(df_trades["net_return_pct"].median()), 4),
        "cumulative_return_pct": round(float(daily.sum()), 4),
        "max_drawdown_pct": round(float(drawdown.min()), 4),
        "profit_factor": round(profit_factor, 4) if np.isfinite(profit_factor) else "inf",
        "avg_time_to_2pct_minutes": (
            round(float(df_trades["time_to_2pct_minutes"].dropna().mean()), 2)
            if df_trades["time_to_2pct_minutes"].notna().any()
            else None
        ),
    }

    ensure_dir(str(PROJECT_ROOT / "reports" / "backtest"))
    today = datetime.now().strftime("%Y%m%d")
    result_path = PROJECT_ROOT / "reports" / "backtest" / f"intraday_backtest_{today}.json"
    trades_path = PROJECT_ROOT / "reports" / "backtest" / f"intraday_backtest_trades_{today}.csv"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    df_trades.to_csv(trades_path, index=False, encoding="utf-8-sig")

    report["result_path"] = str(result_path)
    report["trades_path"] = str(trades_path)
    logger.info(
        "backtest complete: hit_rate_2pct=%s avg_net_return_pct=%s",
        report["hit_rate_2pct"],
        report["avg_net_return_pct"],
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Intraday strategy backtest")
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
        print("\n=== backtest summary ===")
        print(f"  period: {result['backtest_period']}")
        print(f"  hit_rate_2pct: {result['hit_rate_2pct']}")
        print(f"  avg_net_return_pct: {result['avg_net_return_pct']}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
