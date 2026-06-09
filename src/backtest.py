"""백테스트 모듈.

과거 기간에 대해 매일 장 마감 직전(14:40) 매수, 다음날 09:30 전 청산 전략을
시뮬레이션합니다. 수수료·세금·슬리피지를 반영합니다.

실행:
    python src/backtest.py --start 2023-01-01 --end 2024-12-31
    python src/backtest.py --start 2024-01-01 --end 2024-12-31 --top-n 20 --threshold 0.6
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from make_features import FEATURE_COLUMNS
from utils import ensure_dir, load_config, save_csv, setup_logger

logger = setup_logger(__name__, "logs/backtest.log")
cfg = load_config("config.yaml")


class BacktestEngine:
    """백테스트 엔진.

    Args:
        start_date: 백테스트 시작일 'YYYY-MM-DD'
        end_date: 백테스트 종료일 'YYYY-MM-DD'
        top_n: 매일 매수할 종목 수
        threshold: 상승확률 최소 기준 (이 값 이상인 종목만 매수)
        initial_capital: 초기 투자금
    """

    def __init__(
        self,
        start_date: str,
        end_date: str,
        top_n: int = 20,
        threshold: float = 0.5,
        initial_capital: float = 100_000_000,
    ) -> None:
        self.start_date = pd.to_datetime(start_date)
        self.end_date = pd.to_datetime(end_date)
        self.top_n = top_n
        self.threshold = threshold
        self.initial_capital = initial_capital
        self.capital = initial_capital

        bt_cfg = cfg.get("backtest", {})
        self.fee_rate = bt_cfg.get("fee_rate", 0.00015)
        self.tax_rate = bt_cfg.get("tax_rate", 0.0018)
        self.slippage = bt_cfg.get("slippage_rate", 0.001)

        strategy = cfg.get("strategy", {})
        self.target_profit = strategy.get("target_profit_rate", 0.02)
        self.stop_loss = strategy.get("stop_loss_rate", -0.03)

        self.trades: List[dict] = []
        self.daily_returns: List[dict] = []

    def _apply_costs(self, entry_price: float, exit_price: float) -> Tuple[float, float]:
        """수수료·세금·슬리피지 적용.

        Args:
            entry_price: 매수가
            exit_price: 매도가

        Returns:
            (실효 매수가, 실효 매도가) 튜플
        """
        # 매수: 슬리피지 (조금 더 비싸게 삼) + 매수 수수료
        effective_entry = entry_price * (1 + self.slippage) * (1 + self.fee_rate)
        # 매도: 슬리피지 (조금 더 싸게 팔) + 매도 수수료 + 거래세
        effective_exit = exit_price * (1 - self.slippage) * (1 - self.fee_rate - self.tax_rate)
        return effective_entry, effective_exit

    def simulate_day(
        self,
        trade_date: pd.Timestamp,
        candidates: pd.DataFrame,
        daily_df: pd.DataFrame,
        all_dates: List[pd.Timestamp],
    ) -> List[dict]:
        """하루 거래 시뮬레이션.

        Args:
            trade_date: 매수일
            candidates: 당일 선정 종목 (ticker, close, proba_up 컬럼)
            daily_df: 전체 일봉 데이터
            all_dates: 전체 거래일 리스트

        Returns:
            당일 거래 결과 리스트
        """
        results = []

        # 다음 거래일 찾기
        date_idx = next((i for i, d in enumerate(all_dates) if d >= trade_date), None)
        if date_idx is None or date_idx + 1 >= len(all_dates):
            return results
        exit_date = all_dates[date_idx + 1]

        for _, row in candidates.iterrows():
            ticker = str(row["ticker"])
            entry_price = float(row.get("close", 0))
            proba = float(row.get("proba_up", 0))

            if entry_price <= 0 or proba < self.threshold:
                continue

            # 다음날 데이터
            next_data = daily_df[
                (daily_df["ticker"] == ticker) & (daily_df["date"] == exit_date)
            ]
            if next_data.empty:
                continue

            nr = next_data.iloc[0]
            next_open = float(nr["open"])
            next_high = float(nr["high"])
            next_low = float(nr["low"])

            # 보수적 매수가: 슬리피지 적용된 종가
            raw_entry = entry_price
            target_price = raw_entry * (1 + self.target_profit)
            stop_price = raw_entry * (1 + self.stop_loss)

            # 시가 갭 적용
            if next_open >= target_price:
                raw_exit = target_price
                reason = "take_profit_gap_up"
            elif next_open <= stop_price:
                raw_exit = next_open
                reason = "stop_loss_gap_down"
            elif next_high >= target_price:
                raw_exit = target_price
                reason = "take_profit"
            elif next_low <= stop_price:
                raw_exit = stop_price
                reason = "stop_loss"
            else:
                raw_exit = next_open
                reason = "forced_exit_0930"

            # 비용 적용
            eff_entry, eff_exit = self._apply_costs(raw_entry, raw_exit)
            pnl_rate = (eff_exit - eff_entry) / eff_entry

            results.append({
                "trade_date": trade_date.strftime("%Y-%m-%d"),
                "exit_date": exit_date.strftime("%Y-%m-%d"),
                "ticker": ticker,
                "name": row.get("name", ticker),
                "entry_price": round(eff_entry, 2),
                "exit_price": round(eff_exit, 2),
                "raw_entry": raw_entry,
                "raw_exit": raw_exit,
                "exit_reason": reason,
                "pnl_rate": round(pnl_rate, 6),
                "proba_up": round(proba, 4),
            })

        return results

    def run(
        self,
        labeled_df: pd.DataFrame,
        model,
    ) -> None:
        """전체 백테스트 실행.

        Args:
            labeled_df: 피처+라벨 포함 DataFrame
            model: 학습된 예측 모델
        """
        daily_df = labeled_df[["ticker", "name", "date", "open", "high", "low", "close", "volume", "trading_value"]].copy() if "name" in labeled_df.columns else labeled_df[["ticker", "date", "open", "high", "low", "close", "volume", "trading_value"]].copy()
        daily_df = daily_df.drop_duplicates(["ticker", "date"])

        all_dates = sorted(labeled_df["date"].unique())
        test_dates = [d for d in all_dates if self.start_date <= d <= self.end_date]

        logger.info(
            f"백테스트 기간: {self.start_date.date()} ~ {self.end_date.date()}, "
            f"거래일: {len(test_dates)}일"
        )

        available_features = [c for c in FEATURE_COLUMNS if c in labeled_df.columns]

        for trade_date in test_dates:
            day_df = labeled_df[labeled_df["date"] == trade_date].copy()
            if day_df.empty:
                continue

            # 예측
            X = day_df[available_features].fillna(0)
            try:
                proba = model.predict_proba(X)[:, 1]
                day_df = day_df.copy()
                day_df["proba_up"] = proba
            except Exception as e:
                logger.warning(f"예측 실패 [{trade_date}]: {e}")
                continue

            # Top N 선정
            candidates = day_df.nlargest(self.top_n, "proba_up")

            # 유동성 필터
            min_tv = (cfg.get("risk") or {}).get("min_daily_trading_value", 3_000_000_000)
            if "trading_value" in candidates.columns:
                candidates = candidates[candidates["trading_value"] >= min_tv]

            # 하루 시뮬레이션
            day_results = self.simulate_day(trade_date, candidates, daily_df, all_dates)
            self.trades.extend(day_results)

            if day_results:
                day_pnl = np.mean([r["pnl_rate"] for r in day_results])
                self.daily_returns.append({
                    "date": trade_date.strftime("%Y-%m-%d"),
                    "n_trades": len(day_results),
                    "avg_pnl_rate": round(day_pnl, 6),
                })

        logger.info(f"백테스트 완료: 총 {len(self.trades)}건 거래")

    def summary(self) -> Dict:
        """성과 지표 계산."""
        if not self.trades:
            return {"error": "거래 없음"}

        df = pd.DataFrame(self.trades)
        pnl_series = df["pnl_rate"]

        total_trades = len(df)
        wins = (pnl_series >= 0).sum()
        win_rate = wins / total_trades

        take_profit_count = (df["exit_reason"].str.contains("take_profit")).sum()
        stop_loss_count = (df["exit_reason"].str.contains("stop_loss")).sum()
        forced_exit_count = (df["exit_reason"].str.contains("forced_exit")).sum()

        # 누적수익률 계산 (복리)
        cumulative_return = (1 + pnl_series / self.top_n).prod() - 1

        avg_daily = pnl_series.mean()
        max_drawdown = self._calc_max_drawdown(df)

        return {
            "period": f"{self.start_date.date()} ~ {self.end_date.date()}",
            "total_trades": total_trades,
            "win_rate": round(win_rate, 4),
            "take_profit_rate": round(take_profit_count / total_trades, 4),
            "stop_loss_rate": round(stop_loss_count / total_trades, 4),
            "forced_exit_rate": round(forced_exit_count / total_trades, 4),
            "avg_pnl_rate": round(avg_daily, 6),
            "cumulative_return": round(cumulative_return, 4),
            "max_drawdown": round(max_drawdown, 4),
            "loss_trade_ratio": round((pnl_series < 0).mean(), 4),
            "avg_win_pnl": round(pnl_series[pnl_series >= 0].mean(), 6) if wins > 0 else 0,
            "avg_loss_pnl": round(pnl_series[pnl_series < 0].mean(), 6) if wins < total_trades else 0,
            "fee_tax_impact": round(
                (self.fee_rate * 2 + self.tax_rate + self.slippage * 2) * -1, 6
            ),
        }

    def _calc_max_drawdown(self, df: pd.DataFrame) -> float:
        """날짜별 누적 수익률 기준 MDD 계산."""
        if self.daily_returns:
            daily = pd.DataFrame(self.daily_returns)
            cumret = (1 + daily["avg_pnl_rate"]).cumprod()
            rolling_max = cumret.expanding().max()
            drawdown = (cumret - rolling_max) / rolling_max
            return float(drawdown.min())
        return 0.0

    def save_results(self, output_dir: str) -> None:
        """백테스트 결과 저장."""
        ensure_dir(output_dir)

        # 거래 내역
        if self.trades:
            trades_df = pd.DataFrame(self.trades)
            trades_path = os.path.join(output_dir, "backtest_trades.csv")
            save_csv(trades_df, trades_path)
            logger.info(f"거래 내역 저장: {trades_path}")

        # 요약
        summary = self.summary()
        summary_path = os.path.join(output_dir, "backtest_summary.txt")
        lines = [
            "=" * 60,
            "백테스트 결과 요약",
            "=" * 60,
            f"기간: {summary.get('period')}",
            f"총 거래 횟수: {summary.get('total_trades'):,}건",
            "",
            "--- 수익성 ---",
            f"평균 거래당 수익률: {summary.get('avg_pnl_rate', 0):.4%}",
            f"누적 수익률 (추정): {summary.get('cumulative_return', 0):.2%}",
            f"최대낙폭 (MDD): {summary.get('max_drawdown', 0):.2%}",
            "",
            "--- 전략 성과 ---",
            f"승률: {summary.get('win_rate', 0):.2%}",
            f"+{((cfg or {}).get('strategy') or {}).get('target_profit_rate', 0.02)*100:.0f}% 익절 비율: {summary.get('take_profit_rate', 0):.2%}",
            f"손절 비율: {summary.get('stop_loss_rate', 0):.2%}",
            f"강제청산 비율: {summary.get('forced_exit_rate', 0):.2%}",
            f"평균 이익 거래: {summary.get('avg_win_pnl', 0):.4%}",
            f"평균 손실 거래: {summary.get('avg_loss_pnl', 0):.4%}",
            "",
            "--- 비용 ---",
            f"거래당 비용 추정: {summary.get('fee_tax_impact', 0):.4%}",
            f"(수수료 {self.fee_rate:.4%} × 2 + 세금 {self.tax_rate:.4%} + 슬리피지 {self.slippage:.4%} × 2)",
            "=" * 60,
        ]
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logger.info(f"요약 저장: {summary_path}")
        logger.info("\n" + "\n".join(lines))


def run_backtest(
    start_date: str = "2024-01-01",
    end_date: str = "2024-12-31",
    top_n: int = 20,
    threshold: float = 0.5,
    strategy_id: str = "morning_0930",
) -> dict:
    """백테스트를 함수로 실행. 항상 dict를 반환하며 절대 None을 반환하지 않는다."""
    try:
        safe_cfg = cfg or {}
        data_cfg = safe_cfg.get("data") or {}
        paths_cfg = safe_cfg.get("paths") or {}
        bt_cfg = safe_cfg.get("backtest") or {}

        labels_path = data_cfg.get("processed_labels_path", "data/processed/labeled_dataset.csv")
        model_path = paths_cfg.get("model_path", "models/model.joblib")
        output_dir = paths_cfg.get("backtests_dir", "reports/backtests")

        missing = []
        if not os.path.exists(labels_path):
            missing.append(f"라벨 파일: {labels_path}")
        if not os.path.exists(model_path):
            missing.append(f"모델 파일: {model_path}")

        if missing:
            return {
                "success": False, "stage": "missing_data",
                "message": "필수 파일 없음 — 먼저 데이터 수집 → 피처 → 라벨 → 모델학습을 실행하세요.",
                "errors": missing,
                "trades_file": None, "summary_file": None,
                "total_trades": 0, "strategy_id": strategy_id,
            }

        import joblib
        model = joblib.load(model_path)
        labeled_df = pd.read_csv(labels_path, parse_dates=["date"])

        if labeled_df.empty:
            return {
                "success": False, "stage": "empty_data",
                "message": "라벨 데이터가 비어 있습니다.",
                "errors": ["labeled_dataset.csv 행 없음"],
                "trades_file": None, "summary_file": None,
                "total_trades": 0, "strategy_id": strategy_id,
            }

        engine = BacktestEngine(
            start_date=start_date, end_date=end_date,
            top_n=top_n, threshold=threshold,
            initial_capital=bt_cfg.get("initial_capital", 100_000_000),
        )
        engine.run(labeled_df, model)
        engine.save_results(output_dir)

        summary = engine.summary()
        trades_file = os.path.join(output_dir, "backtest_trades.csv")
        summary_file = os.path.join(output_dir, "backtest_summary.txt")

        import json as _json
        ensure_dir(output_dir)
        with open(os.path.join(output_dir, "backtest_result.json"), "w", encoding="utf-8") as _f:
            _json.dump({**summary, "strategy_id": strategy_id,
                        "start_date": start_date, "end_date": end_date, "top_n": top_n},
                       _f, ensure_ascii=False, indent=2)

        return {
            "success": True, "stage": "completed",
            "message": f"백테스트 완료: {summary.get('total_trades', 0)}건 거래",
            "errors": [],
            "trades_file": trades_file if os.path.exists(trades_file) else None,
            "summary_file": summary_file if os.path.exists(summary_file) else None,
            "total_trades": summary.get("total_trades", 0),
            "win_rate": summary.get("win_rate", 0),
            "avg_pnl_rate": summary.get("avg_pnl_rate", 0),
            "cumulative_return": summary.get("cumulative_return", 0),
            "strategy_id": strategy_id,
            "summary": summary,
        }

    except Exception as ex:
        import traceback
        tb = traceback.format_exc()
        logger.error("백테스트 예외: %s", tb)
        return {
            "success": False, "stage": "exception",
            "message": f"백테스트 예외: {ex}",
            "errors": [tb[-1000:]],
            "trades_file": None, "summary_file": None,
            "total_trades": 0, "strategy_id": strategy_id,
        }


def main() -> None:
    """메인 실행 함수."""
    parser = argparse.ArgumentParser(description="백테스트 실행")
    parser.add_argument("--start", default="2024-01-01", help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", default="2024-12-31", help="종료일 YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=20, help="매일 매수 종목 수")
    parser.add_argument("--threshold", type=float, default=0.5, help="상승확률 최소 기준")
    parser.add_argument("--strategy", default="morning_0930",
                        choices=["morning_0930", "afternoon_1500"], help="전략 ID")
    args = parser.parse_args()

    logger.info("=== 백테스트 시작 ===")
    result = run_backtest(
        start_date=args.start, end_date=args.end,
        top_n=args.top_n, threshold=args.threshold, strategy_id=args.strategy,
    )

    if result["success"]:
        logger.info("=== 백테스트 완료: %d건 ===", result.get("total_trades", 0))
    else:
        logger.error("백테스트 실패: %s", result.get("message", ""))
        for e in result.get("errors", []):
            print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
