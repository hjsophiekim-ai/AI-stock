"""수익률 및 성과 분석 서비스."""

import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def get_today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _orders_dir() -> Path:
    return PROJECT_ROOT / "reports" / "orders"


def _paper_trades_dir() -> Path:
    return PROJECT_ROOT / "reports" / "paper_trades"


def _backtests_dir() -> Path:
    return PROJECT_ROOT / "reports" / "backtests"


def load_order_logs(date_str: Optional[str] = None) -> pd.DataFrame:
    """주문 로그 CSV 로드 (전체 또는 특정 날짜)."""
    dfs = []
    orders_dir = _orders_dir()
    if not orders_dir.exists():
        return pd.DataFrame()
    if date_str:
        p = orders_dir / f"orders_{date_str}.csv"
        if p.exists():
            dfs.append(pd.read_csv(p, encoding="utf-8-sig"))
    else:
        for p in sorted(orders_dir.glob("orders_*.csv")):
            try:
                dfs.append(pd.read_csv(p, encoding="utf-8-sig"))
            except Exception:
                pass
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


def load_paper_trade_logs() -> pd.DataFrame:
    """paper trade 로그 CSV 로드."""
    dfs = []
    pt_dir = _paper_trades_dir()
    if not pt_dir.exists():
        return pd.DataFrame()
    for fname in ["order_log.csv", "paper_trade_log.csv"]:
        p = pt_dir / fname
        if p.exists():
            try:
                dfs.append(pd.read_csv(p, encoding="utf-8-sig"))
            except Exception:
                pass
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


def load_backtest_trades() -> pd.DataFrame:
    """백테스트 거래 내역 로드."""
    p = _backtests_dir() / "backtest_trades.csv"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()


def load_positions_json() -> List[Dict]:
    """data/positions.json 로드."""
    import json
    pos_file = PROJECT_ROOT / "data" / "positions.json"
    if not pos_file.exists():
        return []
    try:
        with open(pos_file, encoding="utf-8") as f:
            return list(json.load(f).values())
    except Exception:
        return []


def calc_realized_pnl(df: Optional[pd.DataFrame] = None) -> float:
    """실현손익 계산 (sell 주문만)."""
    if df is None:
        df = load_order_logs()
    if df.empty or "side" not in df.columns:
        return 0.0
    sells = df[df["side"] == "sell"]
    if sells.empty:
        return 0.0
    total = 0.0
    for _, row in sells.iterrows():
        qty = float(row.get("quantity", 0) or 0)
        sell_price = float(row.get("sell_price", 0) or 0)
        entry_price = float(row.get("entry_price", 0) or 0)
        total += (sell_price - entry_price) * qty
    return total


def calc_unrealized_pnl(positions: Optional[List[Dict]] = None) -> float:
    """평가손익 계산 (현재가 없으면 0)."""
    if positions is None:
        positions = load_positions_json()
    total = 0.0
    for pos in positions:
        cp = float(pos.get("current_price", 0) or 0)
        ep = float(pos.get("entry_price", 0) or 0)
        qty = float(pos.get("quantity", 0) or 0)
        if cp > 0 and ep > 0:
            total += (cp - ep) * qty
    return total


def calc_win_rate(df: Optional[pd.DataFrame] = None) -> Tuple[float, int, int]:
    """승률 계산. Returns (win_rate, wins, total)."""
    if df is None:
        df = load_order_logs()
    if df.empty or "side" not in df.columns:
        return 0.0, 0, 0
    sells = df[df["side"] == "sell"]
    if sells.empty:
        return 0.0, 0, 0
    wins = 0
    total = 0
    for _, row in sells.iterrows():
        sp = float(row.get("sell_price", 0) or 0)
        ep = float(row.get("entry_price", 0) or 0)
        if ep > 0:
            total += 1
            if sp > ep:
                wins += 1
    rate = wins / total if total > 0 else 0.0
    return round(rate * 100, 1), wins, total


def calc_cumulative_return(initial_capital: float = 100_000_000,
                           df: Optional[pd.DataFrame] = None) -> float:
    """누적수익률 계산 (%)."""
    realized = calc_realized_pnl(df)
    unrealized = calc_unrealized_pnl()
    if initial_capital <= 0:
        return 0.0
    return round((realized + unrealized) / initial_capital * 100, 2)


def get_daily_pnl(df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """일별 실현손익 DataFrame 반환."""
    if df is None:
        df = load_order_logs()
    if df.empty or "side" not in df.columns:
        return pd.DataFrame(columns=["date", "pnl"])
    sells = df[df["side"] == "sell"].copy()
    if sells.empty:
        return pd.DataFrame(columns=["date", "pnl"])
    sells["pnl"] = (
        (sells.get("sell_price", pd.Series(dtype=float)).fillna(0) -
         sells.get("entry_price", pd.Series(dtype=float)).fillna(0))
        * sells.get("quantity", pd.Series(dtype=float)).fillna(0)
    )
    sells["date"] = pd.to_datetime(sells["datetime"]).dt.date if "datetime" in sells.columns else "unknown"
    return sells.groupby("date")["pnl"].sum().reset_index()


def get_summary() -> Dict:
    """전체 성과 요약 딕셔너리 반환."""
    order_df = load_order_logs()
    positions = load_positions_json()
    realized = calc_realized_pnl(order_df)
    unrealized = calc_unrealized_pnl(positions)
    win_rate, wins, total_trades = calc_win_rate(order_df)
    cum_return = calc_cumulative_return(df=order_df)

    trade_reasons: Dict[str, int] = {"take_profit": 0, "stop_loss": 0, "forced_exit": 0}
    sell_policy_summary: Dict[str, Dict[str, float]] = {}
    if not order_df.empty and "reason" in order_df.columns:
        for r, g in order_df.groupby("reason"):
            key = str(r)
            if "take_profit" in key:
                trade_reasons["take_profit"] += len(g)
            elif "stop_loss" in key:
                trade_reasons["stop_loss"] += len(g)
            elif "force" in key or "exit" in key:
                trade_reasons["forced_exit"] += len(g)

    if not order_df.empty and "sell_policy_id" in order_df.columns:
        for policy_id, g in order_df.groupby("sell_policy_id"):
            sells = g[g.get("side", "") == "sell"] if "side" in g.columns else g
            sell_policy_summary[str(policy_id)] = {
                "trade_count": float(len(sells)),
                "realized_pnl": float(calc_realized_pnl(sells)) if not sells.empty else 0.0,
            }

    for pos in positions:
        policy_id = str(pos.get("sell_policy_id", "unknown"))
        item = sell_policy_summary.setdefault(policy_id, {"trade_count": 0.0, "realized_pnl": 0.0})
        item["open_positions"] = item.get("open_positions", 0.0) + 1
        ep = float(pos.get("entry_price", 0) or 0)
        cp = float(pos.get("current_price", 0) or 0)
        if ep > 0 and cp >= ep * 1.02 and bool(pos.get("manual_only", False)):
            item["manual_hold_target_reached"] = item.get("manual_hold_target_reached", 0.0) + 1

    return {
        "total_trades": total_trades,
        "wins": wins,
        "win_rate": win_rate,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "cumulative_return_pct": cum_return,
        "take_profit_count": trade_reasons["take_profit"],
        "stop_loss_count": trade_reasons["stop_loss"],
        "forced_exit_count": trade_reasons["forced_exit"],
        "position_count": len(positions),
        "sell_policy_summary": sell_policy_summary,
    }
