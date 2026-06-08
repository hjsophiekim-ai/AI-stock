"""거래 보장 자동매매 실행 파일.

top100 후보를 기준으로 예산 범위 내에서 PAPER/MOCK/REAL 매매를 실행합니다.
+2% 익절 목표 후보 전략 — 수익을 보장하지 않으며, 주문 발생 가능성을 높이는 기능입니다.

모드 동작:
  --mode paper : PAPER — 실제 API 주문 없음, 가상 기록만
  --mode mock  : MOCK  — 한국투자증권 모의투자 API 호출 (live_trade=false여도 실행)
  --mode real  : REAL  — 3중 안전장치(live_trade/use_mock=false/confirm) 모두 충족 시만 실행

실전 주문 조건 (모두 충족 필요):
  - live_trade: true
  - kis.use_mock: false
  - safety.confirm_live_trade: true
  - force_trade.enabled: true

모든 주문은 기본 지정가. 시장가 주문은 기본 금지.

사용법:
    python src/force_auto_trade.py --budget 300000 --mode paper --min-orders 1 --max-orders 100
    python src/force_auto_trade.py --budget 300000 --mode mock  --min-orders 1 --max-orders 100
    python src/force_auto_trade.py --budget 300000 --mode real  --min-orders 1 --max-orders 100
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from budget_allocator import BudgetAllocator, BudgetAllocationResult
from emergency_stop import EmergencyStop
from force_trade_selector import ForceTradeSelector, ForceTradeSelectorError
from order_manager import OrderManager
from position_manager import PositionManager
from safety_gate import SafetyGate, TRADE_MODE_PAPER, TRADE_MODE_MOCK, TRADE_MODE_REAL
from trading_calendar import TradingCalendar, SESSION_CLOSED, SESSION_CLOSING_AUCTION
from utils import ensure_dir, get_today_str, load_config, save_csv, setup_logger

logger = setup_logger(__name__, "logs/trade.log")
NO_TRADE_REASON_DIR = "reports"


class NoTradeReason:
    API_AUTH_FAIL = "API 인증 실패"
    INSUFFICIENT_CASH = "주문가능금액 부족"
    NO_CANDIDATES = "후보 종목 없음"
    ALL_HARD_EXCLUDED = "모든 후보가 hard exclusion에 해당"
    WRONG_TRADING_HOUR = "현재 시간이 주문 가능 시간이 아님"
    ZERO_QUANTITY = "주문 수량 계산 결과 0"
    SAFETY_GATE_REJECTED = "SafetyGate 거부"
    RISK_MANAGER_REJECTED = "RiskManager 거부"
    API_ORDER_REJECTED = "한국투자증권 API 주문 거부"
    CANCELLED_UNFILLED = "미체결 후 취소"
    FORCE_TRADE_DISABLED = "force_trade.enabled=false"
    EMERGENCY_STOP = "긴급 중단 활성화"
    REAL_CONDITIONS_NOT_MET = "REAL 모드 조건 미충족"


class ForceAutoTrader:
    """거래 보장 자동매매 실행 클래스."""

    def __init__(
        self,
        config_path: str = "config.yaml",
        runtime_mode: Optional[str] = None,
    ) -> None:
        self.cfg = load_config(config_path)
        self.ft = self.cfg.get("force_trade", {})
        self._requested_mode: Optional[str] = runtime_mode  # 'paper'|'mock'|'real'|None

        # SafetyGate: runtime_mode 전달 → config 보다 우선 적용
        self.gate = SafetyGate(config_path, runtime_mode=runtime_mode)

        self.es = EmergencyStop(config_path)
        # OrderManager에 gate 주입 → 동일한 mode 공유
        self.order_mgr = OrderManager(config_path, gate=self.gate)
        self.pos_mgr = PositionManager(config_path)
        self.selector = ForceTradeSelector(config_path)
        self.allocator = BudgetAllocator(config_path)
        self.calendar = TradingCalendar(config_path)
        self._no_trade_reasons: List[str] = []
        ensure_dir("reports")
        ensure_dir("logs")

    def run(
        self,
        budget: float,
        min_orders: int = 1,
        max_orders: int = 100,
        date_str: Optional[str] = None,
        candidate_file: Optional[str] = None,
    ) -> List[Dict]:
        """거래 보장 자동매매 실행."""
        ds = date_str or get_today_str("%Y%m%d")
        trade_mode = self.gate.mode
        now = datetime.now()
        order_session = self.calendar.get_market_session(now)
        after_hours_cfg = self.cfg.get("after_hours", {})

        self._print_banner(trade_mode, budget, min_orders, max_orders, ds, order_session)

        # ── REAL 요청인데 조건 미충족 → 즉시 거부 ────────────────────
        if self._requested_mode == "real" and trade_mode != TRADE_MODE_REAL:
            reason = (
                "REAL 모드 요청이 거부되었습니다. config.yaml에서 "
                "live_trade=true, kis.use_mock=false, safety.confirm_live_trade=true "
                "를 모두 설정해야 합니다."
            )
            print(f"\n[REAL 거부] {reason}")
            self._add_no_trade(NoTradeReason.REAL_CONDITIONS_NOT_MET, reason)
            self._save_no_trade_report(ds)
            return []

        # ── force_trade.enabled 확인 ──────────────────────────────────
        if not self.ft.get("enabled", False):
            self._add_no_trade(
                NoTradeReason.FORCE_TRADE_DISABLED,
                "config.yaml: force_trade.enabled=false → true로 변경 필요",
            )
            self._save_no_trade_report(ds)
            return []

        # ── 긴급 중단 ─────────────────────────────────────────────────
        if self.es.is_active():
            self._add_no_trade(NoTradeReason.EMERGENCY_STOP, "data/EMERGENCY_STOP 파일 존재")
            self._save_no_trade_report(ds)
            return []

        # ── 세션 허용 여부 확인 (MOCK/REAL에서만 검사) ────────────────
        if trade_mode != TRADE_MODE_PAPER:
            if not self.calendar.is_session_allowed(order_session, after_hours_cfg):
                if order_session == SESSION_CLOSED:
                    reason = f"장 마감({order_session}) — 시간외 주문 비활성화 또는 허용 시간 외"
                elif order_session == SESSION_CLOSING_AUCTION:
                    reason = f"동시호가({order_session}) — 주문 미지원"
                else:
                    reason = (
                        f"세션({order_session}) 주문 비활성화 — "
                        f"config.yaml: after_hours.allow_{order_session.lower()}: true 로 변경 가능"
                    )
                self._add_no_trade(NoTradeReason.WRONG_TRADING_HOUR, reason)
                self._save_no_trade_report(ds)
                return []

        # 1. 주문가능금액
        orderable_cash = self._get_orderable_cash(trade_mode)
        if orderable_cash is None:
            self._save_no_trade_report(ds)
            return []

        # 2. 후보 종목 선정
        candidates = self._get_candidates(ds, min_orders, max_orders, candidate_file)
        if candidates is None or candidates.empty:
            self._save_no_trade_report(ds)
            return []

        # 3. 주문 미리보기 저장
        preview_path = os.path.join("reports", f"orders_preview_{ds}.csv")
        save_csv(candidates.head(max_orders), preview_path)
        print(f"주문 대상 미리보기: {preview_path} ({len(candidates.head(max_orders))}개 종목)")

        # 4. 예산 배분
        alloc_result = self.allocator.allocate(
            candidates=candidates,
            budget=budget,
            orderable_cash=orderable_cash if trade_mode != TRADE_MODE_PAPER else 0,
            min_orders=min_orders,
            max_orders=max_orders,
        )
        self.allocator.save_result(alloc_result, ds)
        print(f"예산배분 후 주문대상: {len(alloc_result.allocations)}개 종목")

        if not alloc_result.allocations:
            self._add_no_trade(
                NoTradeReason.ZERO_QUANTITY,
                f"예산 {budget:,.0f}원으로 1주 이상 살 수 있는 종목 없음 "
                f"(최저가 종목 가격이 예산 초과 가능성 확인 필요)",
            )
            self._save_no_trade_report(ds)
            return []

        # 5. 주문 실행
        results = self._execute_orders(alloc_result, trade_mode)

        # 6. 주문 결과 저장
        if results:
            results_df = pd.DataFrame(results)
            orders_path = os.path.join("reports", f"orders_{ds}.csv")
            save_csv(results_df, orders_path)
            print(f"주문 결과: {orders_path}")

        success_count = sum(1 for r in results if r.get("success"))
        print(f"\n[완료] 주문 성공: {success_count}/{len(results)}건 | 모드: {trade_mode}")

        if success_count == 0:
            if not self._no_trade_reasons:
                self._add_no_trade(NoTradeReason.API_ORDER_REJECTED, "모든 주문이 실패함")
            self._save_no_trade_report(ds)
        else:
            logger.info("[ForceAutoTrade] 완료: 성공=%d/%d", success_count, len(results))

        return results

    def _get_orderable_cash(self, trade_mode: str) -> Optional[float]:
        """PAPER: 가상자본, MOCK/REAL: API에서 주문가능금액 조회."""
        if trade_mode == TRADE_MODE_PAPER:
            capital = float(self.cfg.get("backtest", {}).get("initial_capital", 100_000_000))
            logger.info("PAPER 모드: 가상 자본 %.0f원", capital)
            return capital
        try:
            cash = self.order_mgr._api.get_orderable_cash()
            logger.info("주문가능금액: %.0f원", cash)
            print(f"주문가능금액: {cash:,.0f}원")
            return cash
        except Exception as e:
            self._add_no_trade(NoTradeReason.API_AUTH_FAIL, str(e))
            return None

    def _get_candidates(
        self,
        date_str: str,
        min_orders: int,
        max_orders: int,
        candidate_file: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        """후보 종목 선정."""
        if candidate_file and os.path.exists(candidate_file):
            df = pd.read_csv(candidate_file)
            code_col = "stock_code" if "stock_code" in df.columns else "ticker"
            df[code_col] = df[code_col].astype(str).str.zfill(6)
            logger.info("지정 파일 로드: %s (%d행)", candidate_file, len(df))
            print(f"후보 파일: {candidate_file} ({len(df)}개 종목)")
            return df

        try:
            df = self.selector.select(
                date_str=date_str,
                min_candidates=min_orders,
                max_candidates=max_orders,
            )
            if df.empty:
                self._add_no_trade(NoTradeReason.NO_CANDIDATES, "후보 선정 결과 0개")
            else:
                print(f"후보 파일: force_trade_selector → {len(df)}개 종목")
            return df
        except ForceTradeSelectorError as e:
            self._add_no_trade(NoTradeReason.ALL_HARD_EXCLUDED, str(e))
            return None

    def _execute_orders(self, alloc_result: BudgetAllocationResult, trade_mode: str) -> List[Dict]:
        results = []
        self.order_mgr.reset_failed_tickers()

        for alloc in alloc_result.allocations:
            stock_code = str(alloc.get("stock_code", alloc.get("ticker", ""))).zfill(6)
            stock_name = alloc.get("stock_name", alloc.get("name", stock_code))
            quantity = alloc["quantity"]
            order_price = alloc["order_price"]
            current_price = int(alloc["current_price"])

            logger.info(
                "[주문] %s(%s) %d주 × %d원 = %d원 [모드=%s]",
                stock_code, stock_name, quantity, order_price,
                quantity * order_price, trade_mode,
            )

            result = self.order_mgr.place_order_with_verification(
                stock_code=stock_code,
                stock_name=stock_name,
                quantity=quantity,
                order_price=order_price,
                current_price=current_price,
                side="buy",
            )
            result["stock_code"] = stock_code
            result["stock_name"] = stock_name
            result["order_price"] = order_price
            result["quantity"] = quantity
            result["trade_mode"] = trade_mode
            results.append(result)

            if not result.get("success"):
                reason = result.get("rejected_reason", result.get("reason", ""))
                if "RiskManager" in reason:
                    self._add_no_trade(NoTradeReason.RISK_MANAGER_REJECTED, f"{stock_code}: {reason}")
                elif "SafetyGate" in reason:
                    self._add_no_trade(NoTradeReason.SAFETY_GATE_REJECTED, f"{stock_code}: {reason}")
                else:
                    self._add_no_trade(NoTradeReason.API_ORDER_REJECTED, f"{stock_code}: {reason}")

        return results

    def _add_no_trade(self, reason: str, detail: str = "") -> None:
        entry = reason if not detail else f"{reason}: {detail}"
        self._no_trade_reasons.append(entry)
        logger.warning("[거래없음] %s", entry)

    def _save_no_trade_report(self, date_str: str) -> None:
        if not self._no_trade_reasons:
            return
        path = os.path.join(NO_TRADE_REASON_DIR, f"no_trade_reason_{date_str}.txt")
        lines = [
            "거래 0건 원인 보고서",
            f"생성시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 50,
        ] + [f"  - {r}" for r in self._no_trade_reasons] + ["=" * 50]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n거래없음 보고서: {path}")
        for r in self._no_trade_reasons:
            print(f"  [원인] {r}")

    def _print_banner(
        self,
        trade_mode: str,
        budget: float,
        min_orders: int,
        max_orders: int,
        date_str: str,
        order_session: str = "",
    ) -> None:
        req = (self._requested_mode or "auto").upper()
        api_called = trade_mode != TRADE_MODE_PAPER
        mock_api = trade_mode == TRADE_MODE_MOCK
        real_api = trade_mode == TRADE_MODE_REAL
        after_hours_cfg = self.cfg.get("after_hours", {})
        real_confirmed = after_hours_cfg.get("real_order_confirmed", False)
        session_allowed = self.calendar.is_session_allowed(order_session, after_hours_cfg) if order_session else True
        print("\n" + "=" * 60)
        print("  거래 보장 자동매매 (+2% 익절 목표 후보)")
        print("  ※ 수익을 보장하지 않습니다.")
        print("-" * 60)
        print(f"  요청 모드    : {req}")
        print(f"  실행 모드    : {trade_mode}")
        print(f"  현재 세션    : {order_session or '(판단 중)'}")
        print(f"  세션 허용    : {'YES' if session_allowed else 'NO — 세션 주문 비활성화'}")
        print(f"  예산         : {budget:,.0f}원")
        print(f"  최소/최대주문: {min_orders} / {max_orders}개")
        print(f"  날짜         : {date_str}")
        print(f"  API 호출     : {'YES' if api_called else 'NO'}")
        print(f"  MOCK API     : {'YES' if mock_api else 'NO'}")
        print(f"  REAL API     : {'YES' if real_api else 'NO'}")
        if real_api:
            print(f"  시간외확인   : {'완료 (real_order_confirmed=true)' if real_confirmed else '미완료 — 시간외 REAL 주문 차단됨'}")
        print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="거래 보장 자동매매 — +2% 익절 목표 후보, 수익 보장 없음"
    )
    parser.add_argument("--budget", type=int, required=True, help="사용 예산 (원)")
    parser.add_argument("--min-orders", type=int, default=1, help="최소 주문 종목 수")
    parser.add_argument("--max-orders", type=int, default=100, help="최대 주문 종목 수 (기본값: 100)")
    parser.add_argument(
        "--mode", choices=["paper", "mock", "real"], default=None,
        help=(
            "실행 모드: paper=가상기록, mock=모의투자API, real=실전API(3중 안전장치 충족 필요). "
            "기본값: config.yaml 설정 따름"
        ),
    )
    parser.add_argument("--date", default=None, help="날짜 YYYYMMDD")
    parser.add_argument(
        "--candidate-file", default=None,
        help="후보 파일 직접 지정 (기본: reports/predictions/top100_YYYYMMDD.csv)",
    )
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    trader = ForceAutoTrader(args.config, runtime_mode=args.mode)
    results = trader.run(
        budget=args.budget,
        min_orders=args.min_orders,
        max_orders=args.max_orders,
        date_str=args.date,
        candidate_file=args.candidate_file,
    )

    success = sum(1 for r in results if r.get("success"))
    print(f"\n[최종] 주문 성공: {success}/{len(results)}건")


if __name__ == "__main__":
    main()
