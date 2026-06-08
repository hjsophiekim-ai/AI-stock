"""모의투자 (Paper Trade) 모듈.

Top 20 종목을 가상으로 매수하고, +2% 익절/손절/-09:30 강제청산 규칙을
일봉 데이터로 시뮬레이션합니다. 실제 주문은 발생하지 않습니다.

실행:
    python src/paper_trade.py
"""

import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from order_manager import OrderManager
from trade_rules import TradeRules
from utils import ensure_dir, get_today_str, load_config, save_csv, setup_logger

logger = setup_logger(__name__, "logs/paper_trade.log")
cfg = load_config("config.yaml")


def run_paper_trade_day(
    top20_path: str,
    daily_data_path: str,
    trade_date: str,
    order_mgr: OrderManager,
    trade_rules: TradeRules,
) -> List[dict]:
    """하루 모의투자 시뮬레이션.

    Args:
        top20_path: 당일 Top 20 CSV 경로
        daily_data_path: 일봉 데이터 CSV 경로
        trade_date: 매수일 'YYYYMMDD'
        order_mgr: 주문 관리자
        trade_rules: 매매 규칙

    Returns:
        당일 거래 결과 리스트
    """
    if not os.path.exists(top20_path):
        logger.warning(f"Top 20 파일 없음: {top20_path}")
        return []

    top20 = pd.read_csv(top20_path)
    if top20.empty:
        return []

    # 일봉 데이터 로드
    daily_df = pd.read_csv(daily_data_path, parse_dates=["date"])
    trade_dt = pd.to_datetime(trade_date, format="%Y%m%d")

    # 다음 거래일 찾기
    all_dates = sorted(daily_df["date"].unique())
    trade_idx = next((i for i, d in enumerate(all_dates) if d >= trade_dt), None)
    if trade_idx is None or trade_idx + 1 >= len(all_dates):
        logger.warning(f"다음 거래일 없음: {trade_date}")
        return []

    entry_date = all_dates[trade_idx]
    exit_date = all_dates[trade_idx + 1]

    results = []

    for _, row in top20.iterrows():
        ticker = str(row["ticker"])
        name = str(row.get("name", ticker))
        entry_price = float(row.get("close", 0))

        if entry_price <= 0:
            continue

        # 매수 (가상)
        buy_time = datetime.combine(entry_date.date(), datetime.strptime("14:40", "%H:%M").time())
        order_mgr.buy(
            ticker=ticker,
            name=name,
            price=entry_price,
            qty=1,  # paper trade는 수량 1로 단순화
            now=buy_time,
        )

        # 다음날 데이터로 매도 조건 체크
        next_day = daily_df[
            (daily_df["ticker"] == ticker) & (daily_df["date"] == exit_date)
        ]

        if next_day.empty:
            # 데이터 없으면 강제청산 (매수가로 처리)
            results.append({
                "trade_date": trade_date,
                "ticker": ticker,
                "name": name,
                "entry_price": entry_price,
                "exit_price": entry_price,
                "exit_reason": "no_data",
                "pnl_rate": 0.0,
            })
            order_mgr.sell(ticker, entry_price, "forced_exit")
            continue

        next_row = next_day.iloc[0]
        next_high = float(next_row["high"])
        next_low = float(next_row["low"])
        next_open = float(next_row["open"])

        target_price = entry_price * (1 + cfg["strategy"].get("target_profit_rate", 0.02))
        stop_price = entry_price * (1 + cfg["strategy"].get("stop_loss_rate", -0.03))

        # 익절/손절/강제청산 판단 (보수적: 시가로 오픈갭 적용)
        if next_open >= target_price:
            exit_price = target_price
            exit_reason = "take_profit_gap"
        elif next_open <= stop_price:
            exit_price = next_open
            exit_reason = "stop_loss_gap"
        elif next_high >= target_price:
            exit_price = target_price
            exit_reason = "take_profit"
        elif next_low <= stop_price:
            exit_price = stop_price
            exit_reason = "stop_loss"
        else:
            # 강제청산: 시가 사용 (09:30 근사)
            exit_price = next_open
            exit_reason = "forced_exit_0930"

        pnl_rate = (exit_price - entry_price) / entry_price

        results.append({
            "trade_date": trade_date,
            "ticker": ticker,
            "name": name,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_rate": pnl_rate,
        })

        order_mgr.sell(ticker, exit_price, exit_reason)

    return results


def main() -> None:
    """메인 실행 함수."""
    today = get_today_str("%Y%m%d")
    predictions_dir = cfg["paths"]["predictions_dir"]
    paper_trades_dir = cfg["paths"]["paper_trades_dir"]
    daily_data_path = cfg["data"]["raw_daily_path"]

    top20_path = os.path.join(predictions_dir, f"top20_{today}.csv")
    log_path = os.path.join(paper_trades_dir, "paper_trade_log.csv")

    logger.info("=== 모의투자 시작 ===")
    logger.info("live_trade=false: 실제 주문 없음")

    order_mgr = OrderManager()
    trade_rules = TradeRules()

    results = run_paper_trade_day(
        top20_path=top20_path,
        daily_data_path=daily_data_path,
        trade_date=today,
        order_mgr=order_mgr,
        trade_rules=trade_rules,
    )

    if results:
        df = pd.DataFrame(results)
        ensure_dir(paper_trades_dir)
        if os.path.exists(log_path):
            existing = pd.read_csv(log_path)
            df = pd.concat([existing, df], ignore_index=True)
        save_csv(df, log_path)

        avg_pnl = df[df["trade_date"] == today]["pnl_rate"].mean()
        logger.info(
            f"오늘 모의투자 결과: {len(results)}건, 평균 수익률={avg_pnl:.2%}"
        )
        logger.info(f"누적 로그 저장: {log_path}")
    else:
        logger.warning("오늘 모의투자 결과 없음")

    logger.info("=== 모의투자 완료 ===")


if __name__ == "__main__":
    main()
