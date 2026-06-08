"""주문 가능 여부 단계별 검증 도구.

실제 주문이 가능한지 안전하게 확인합니다.
기본값은 PAPER/MOCK 주문 테스트이며, 실전 주문 테스트는
아래 5가지 조건이 모두 충족될 때만 실행됩니다:
  - live_trade: true
  - kis.use_mock: false
  - safety.confirm_live_trade: true
  - safety.allow_real_test_order: true
  - force_trade.allow_real_test_order: true

사용법:
    python src/order_verification_test.py --stock-code 005930 --amount 10000
    python src/order_verification_test.py --stock-code 005930 --amount 10000 --cancel-after-order
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(__file__))

from utils import ensure_dir, get_today_str, load_config, setup_logger

logger = setup_logger(__name__, "logs/trade.log")


class OrderVerificationTester:
    """주문 검증 단계별 실행 클래스."""

    def __init__(
        self,
        config_path: str = "config.yaml",
        stock_code: str = "005930",
        amount: int = 10_000,
        cancel_after: bool = False,
    ) -> None:
        self.cfg = load_config(config_path)
        self.stock_code = stock_code
        self.amount = amount
        self.cancel_after = cancel_after
        self._results: Dict = {
            "datetime": datetime.now().isoformat(),
            "stock_code": stock_code,
            "amount": amount,
            "trade_mode": "UNKNOWN",
            "api_called": False,
            "real_order_called": False,
            "current_price": 0,
            "orderable_cash": 0,
            "quantity": 0,
            "order_price": 0,
            "order_result": "",
            "order_no": "",
            "filled_quantity": 0,
            "cancelled": False,
            "rejected_reason": "",
        }
        ensure_dir("reports")
        ensure_dir("logs")

    def run(self) -> bool:
        """전체 주문 검증 실행."""
        from safety_gate import SafetyGate, TRADE_MODE_PAPER, TRADE_MODE_MOCK, TRADE_MODE_REAL
        gate = SafetyGate()
        self._results["trade_mode"] = gate.mode

        print("\n" + "=" * 60)
        print(f"  주문 검증 테스트 [{gate.mode} 모드]")
        print(f"  종목: {self.stock_code} | 금액: {self.amount:,}원")
        print("=" * 60)

        # 실전 주문 가능 여부 먼저 확인
        if gate.mode == TRADE_MODE_REAL:
            if not self._check_real_order_conditions():
                return False

        # PAPER 모드
        if gate.mode == TRADE_MODE_PAPER:
            return self._run_paper_test()

        # MOCK / REAL 모드
        return self._run_api_test(gate.mode == TRADE_MODE_REAL)

    def _check_real_order_conditions(self) -> bool:
        """실전 주문 허용 조건 5가지 확인."""
        safety = self.cfg.get("safety", {})
        ft = self.cfg.get("force_trade", {})

        conditions = {
            "live_trade=true": self.cfg.get("live_trade", False),
            "kis.use_mock=false": not self.cfg.get("kis", {}).get("use_mock", True),
            "safety.confirm_live_trade=true": safety.get("confirm_live_trade", False),
            "safety.allow_real_test_order=true": safety.get("allow_real_test_order", False),
            "force_trade.allow_real_test_order=true": ft.get("allow_real_test_order", False),
        }

        failed = [k for k, v in conditions.items() if not v]
        if failed:
            reason = f"실전 주문 조건 미충족: {', '.join(failed)}"
            print(f"❌ {reason}")
            self._results["rejected_reason"] = reason
            self._save_report()
            return False

        max_amount = int(ft.get("test_order_amount", 10_000))
        if self.amount > max_amount:
            reason = f"주문금액({self.amount:,}원)이 테스트 한도({max_amount:,}원) 초과"
            print(f"❌ {reason}")
            self._results["rejected_reason"] = reason
            self._save_report()
            return False

        print(f"⚠️  실전 주문 조건 충족 — 최대 {max_amount:,}원 한도로 실행")
        return True

    def _run_paper_test(self) -> bool:
        """PAPER 모드 가상 주문 테스트."""
        print("📋 PAPER 모드: 실제 API 호출 없이 가상 주문 기록")
        self._results.update({
            "api_called": False,
            "real_order_called": False,
            "quantity": max(1, self.amount // 70_000),
            "order_price": 70_000,
            "order_result": "PAPER_SUCCESS",
            "order_no": f"PAPER_{datetime.now().strftime('%H%M%S')}",
        })
        print(f"✅ PAPER 가상 주문 완료: {self._results['order_no']}")
        self._save_report()
        return True

    def _run_api_test(self, is_real: bool) -> bool:
        """MOCK 또는 REAL 모드 API 주문 테스트."""
        try:
            from kis_api import KISApiClient
            api = KISApiClient()
        except Exception as e:
            reason = f"API 클라이언트 초기화 실패: {e}"
            print(f"❌ {reason}")
            self._results["rejected_reason"] = reason
            self._save_report()
            return False

        # Step 1: 현재가 조회
        print(f"\n[1] 현재가 조회 [{self.stock_code}]...")
        try:
            price_info = api.get_current_price(self.stock_code)
            current_price = int(price_info.get("current_price", 0))
            stock_name = price_info.get("stock_name", self.stock_code)
            self._results["current_price"] = current_price
            print(f"    {stock_name} ({self.stock_code}): {current_price:,}원")
            self._results["api_called"] = True
        except Exception as e:
            reason = f"현재가 조회 실패: {e}"
            print(f"❌ {reason}")
            self._results["rejected_reason"] = reason
            self._save_report()
            return False

        if current_price <= 0:
            reason = "현재가 조회 결과 0"
            print(f"❌ {reason}")
            self._results["rejected_reason"] = reason
            self._save_report()
            return False

        # Step 2: 주문가능금액 조회
        print("[2] 주문가능금액 조회...")
        try:
            orderable_cash = api.get_orderable_cash()
            self._results["orderable_cash"] = orderable_cash
            print(f"    주문가능금액: {orderable_cash:,.0f}원")
        except Exception as e:
            reason = f"주문가능금액 조회 실패: {e}"
            print(f"❌ {reason}")
            self._results["rejected_reason"] = reason
            self._save_report()
            return False

        # Step 3: 주문수량 계산
        print("[3] 주문수량 계산...")
        actual_amount = min(self.amount, orderable_cash)
        buy_adj = self.cfg.get("order", {}).get("buy_price_adjustment_rate", 0.001)
        order_price = int(current_price * (1 + buy_adj))
        quantity = int(actual_amount // order_price)
        self._results.update({"quantity": quantity, "order_price": order_price})

        if quantity <= 0:
            reason = f"주문수량 0 (예산={actual_amount:,}원, 주문가={order_price:,}원)"
            print(f"❌ {reason}")
            self._results["rejected_reason"] = reason
            self._save_report()
            return False

        print(f"    주문가격: {order_price:,}원 | 수량: {quantity}주 | 예상금액: {order_price * quantity:,}원")

        # Step 4: 주문 실행
        print(f"[4] 지정가 매수 주문 실행 ({'실전' if is_real else '모의투자'})...")
        try:
            resp = api.place_cash_buy_order(self.stock_code, quantity, order_price, "limit")
            self._results["api_called"] = True
            if is_real:
                self._results["real_order_called"] = True

            rt_cd = resp.get("rt_cd", "")
            order_no = resp.get("output", {}).get("ODNO", "")
            msg = resp.get("msg1", "")

            if rt_cd == "0":
                self._results.update({"order_result": "SUCCESS", "order_no": order_no})
                print(f"✅ 주문 성공: 주문번호={order_no}")
            else:
                self._results.update({"order_result": f"FAIL:{rt_cd}", "rejected_reason": msg})
                print(f"❌ 주문 실패: rt_cd={rt_cd} msg={msg}")
                self._save_report()
                return False

        except Exception as e:
            reason = f"주문 API 오류: {e}"
            print(f"❌ {reason}")
            self._results["rejected_reason"] = reason
            self._save_report()
            return False

        # Step 5: 체결 여부 조회 (3초 대기)
        if self._results["order_no"]:
            print("[5] 체결 여부 조회 (3초 후)...")
            time.sleep(3)
            try:
                filled_df = api.get_filled_orders()
                order_no = self._results["order_no"]
                matched = filled_df[filled_df.get("order_no", "") == order_no] if filled_df is not None else None
                if matched is not None and len(matched) > 0:
                    filled_qty = int(matched.iloc[0].get("filled_qty", 0))
                    self._results["filled_quantity"] = filled_qty
                    print(f"    체결수량: {filled_qty}/{quantity}주")
                else:
                    print("    체결 정보 없음 (미체결 또는 조회 지연)")
            except Exception as e:
                print(f"    체결 조회 실패 (무시): {e}")

            # Step 6: 미체결 취소
            if self.cancel_after:
                print("[6] 주문 취소 요청...")
                try:
                    api.cancel_order(self._results["order_no"], self.stock_code, quantity)
                    self._results["cancelled"] = True
                    print("    ✅ 취소 요청 완료")
                except Exception as e:
                    print(f"    취소 실패 (수동 확인 필요): {e}")

        self._save_report()
        return True

    def _save_report(self) -> None:
        """주문 검증 결과를 CSV로 저장."""
        today = get_today_str("%Y%m%d")
        path = os.path.join("reports", f"order_verification_{today}.csv")
        fieldnames = list(self._results.keys())
        write_header = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerow(self._results)
        print(f"\n보고서 저장: {path}")
        logger.info(
            "주문검증: mode=%s api_called=%s real_order_called=%s code=%s qty=%d price=%d result=%s order_no=%s",
            self._results["trade_mode"],
            self._results["api_called"],
            self._results["real_order_called"],
            self._results["stock_code"],
            self._results["quantity"],
            self._results["order_price"],
            self._results["order_result"],
            self._results["order_no"],
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="주문 검증 테스트")
    parser.add_argument("--stock-code", default="005930", help="종목코드 (기본값: 005930)")
    parser.add_argument("--amount", type=int, default=10_000, help="주문금액 (기본값: 10000원)")
    parser.add_argument("--cancel-after-order", action="store_true", help="주문 후 즉시 취소 요청")
    parser.add_argument("--config", default="config.yaml", help="설정 파일 경로")
    args = parser.parse_args()

    tester = OrderVerificationTester(
        config_path=args.config,
        stock_code=args.stock_code,
        amount=args.amount,
        cancel_after=args.cancel_after_order,
    )
    success = tester.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
