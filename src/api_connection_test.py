"""한국투자증권 Open API 연결 단계별 검증 도구.

.env에 입력된 API 키로 실제 통신이 가능한지 순서대로 확인합니다.
민감정보(계좌번호, 앱시크릿)는 로그에 마스킹 처리됩니다.

사용법:
    python src/api_connection_test.py
    python src/api_connection_test.py --stock-code 005930
    python src/api_connection_test.py --stock-code 005930 --config config.yaml
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(__file__))

from utils import ensure_dir, get_today_str, load_config, setup_logger

logger = setup_logger(__name__, "logs/api_connection_test.log")

REPORT_DIR = "reports"
LOG_DIR = "logs"


class StepResult:
    """단계별 검증 결과."""

    def __init__(self, step: int, name: str) -> None:
        self.step = step
        self.name = name
        self.success: Optional[bool] = None
        self.message: str = ""
        self.detail: str = ""
        self.skipped: bool = False

    def ok(self, message: str, detail: str = "") -> "StepResult":
        self.success = True
        self.message = message
        self.detail = detail
        return self

    def fail(self, message: str, detail: str = "") -> "StepResult":
        self.success = False
        self.message = message
        self.detail = detail
        return self

    def skip(self, reason: str) -> "StepResult":
        self.skipped = True
        self.success = None
        self.message = reason
        return self

    def __str__(self) -> str:
        if self.skipped:
            icon = "⏭"
        elif self.success:
            icon = "✅"
        else:
            icon = "❌"
        detail_str = f"\n       {self.detail}" if self.detail else ""
        return f"{icon} Step {self.step}: [{self.name}] {self.message}{detail_str}"


class APIConnectionTester:
    """한국투자증권 Open API 연결 단계별 검증 클래스."""

    def __init__(self, config_path: str = "config.yaml", stock_code: str = "005930") -> None:
        self.cfg = load_config(config_path)
        self.stock_code = stock_code
        self.results: List[StepResult] = []
        self._api = None
        self._mode = "UNKNOWN"
        ensure_dir(REPORT_DIR)
        ensure_dir(LOG_DIR)

    def run_all(self) -> bool:
        """전체 검증 단계 순서대로 실행."""
        print("\n" + "=" * 60)
        print("  한국투자증권 Open API 연결 검증")
        print(f"  시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        steps = [
            self._step1_check_env_file,
            self._step2_check_env_vars,
            self._step3_show_mode,
            self._step4_token,
            self._step5_balance,
            self._step6_orderable_cash,
            self._step7_current_price,
        ]

        for step_fn in steps:
            result = step_fn()
            self.results.append(result)
            print(result)
            # 토큰 발급 실패 시 이후 API 단계 스킵
            if result.step == 4 and not result.success and not result.skipped:
                for i, fn in enumerate(steps[4:], start=5):
                    r = StepResult(i, fn.__name__)
                    r.skip("이전 단계(토큰 발급) 실패로 스킵")
                    self.results.append(r)
                    print(r)
                break

        success_count = sum(1 for r in self.results if r.success is True)
        fail_count = sum(1 for r in self.results if r.success is False)
        skip_count = sum(1 for r in self.results if r.skipped)

        print("\n" + "=" * 60)
        print(f"  결과: 성공 {success_count} / 실패 {fail_count} / 스킵 {skip_count}")
        print("=" * 60 + "\n")

        self._save_report()
        return fail_count == 0

    def _step1_check_env_file(self) -> StepResult:
        r = StepResult(1, ".env 파일 확인")
        if os.path.exists(".env"):
            return r.ok(".env 파일 존재")
        return r.fail(".env 파일 없음 — .env.example을 복사하고 API 키를 입력하세요.")

    def _step2_check_env_vars(self) -> StepResult:
        r = StepResult(2, "환경변수 확인")
        from dotenv import load_dotenv
        load_dotenv()

        missing = []
        app_key = os.environ.get("KIS_APP_KEY", "")
        app_secret = os.environ.get("KIS_APP_SECRET", "")
        use_mock = os.environ.get("KIS_USE_MOCK", "true").lower() == "true"

        if not app_key:
            missing.append("KIS_APP_KEY")
        if not app_secret:
            missing.append("KIS_APP_SECRET")

        # 계좌번호 확인
        if use_mock:
            acct = os.environ.get("KIS_MOCK_ACCOUNT_NO", "") or os.environ.get("KIS_ACCOUNT_NO", "")
        else:
            acct = os.environ.get("KIS_ACCOUNT_NO", "")
        if not acct:
            missing.append("KIS_ACCOUNT_NO (또는 KIS_MOCK_ACCOUNT_NO)")

        if missing:
            return r.fail(f"누락된 환경변수: {', '.join(missing)}")

        from safety_gate import SafetyGate
        gate = SafetyGate()
        masked = gate.mask_account_no(acct)
        key_preview = app_key[:4] + "****" if len(app_key) >= 4 else "****"
        return r.ok(
            f"환경변수 확인 완료",
            f"APP_KEY={key_preview} | 계좌={masked} | USE_MOCK={use_mock}",
        )

    def _step3_show_mode(self) -> StepResult:
        r = StepResult(3, "거래 모드 확인")
        from safety_gate import SafetyGate, TRADE_MODE_PAPER, TRADE_MODE_MOCK, TRADE_MODE_REAL
        gate = SafetyGate()
        self._mode = gate.mode
        mode_desc = {
            TRADE_MODE_PAPER: "PAPER 모드 — 가상 기록만 (실제 API 주문 없음)",
            TRADE_MODE_MOCK: "MOCK 모드 — 한국투자증권 모의투자 API",
            TRADE_MODE_REAL: "REAL 모드 — 실전투자 API (실제 자금 사용!)",
        }
        return r.ok(
            mode_desc.get(self._mode, self._mode),
            f"base_url={gate.get_base_url()}",
        )

    def _step4_token(self) -> StepResult:
        r = StepResult(4, "접근토큰 발급")
        if self._mode == "PAPER":
            return r.skip("PAPER 모드: API 통신 없음")
        try:
            from kis_auth import get_access_token
            token = get_access_token()
            if token:
                preview = token[:8] + "..." if len(token) > 8 else "..."
                return r.ok("접근토큰 발급 성공", f"token_preview={preview}")
            return r.fail("토큰 발급 실패: 빈 토큰 반환")
        except Exception as e:
            return r.fail(f"토큰 발급 오류: {type(e).__name__}", str(e))

    def _step5_balance(self) -> StepResult:
        r = StepResult(5, "계좌 잔고 조회")
        if self._mode == "PAPER":
            return r.skip("PAPER 모드: API 통신 없음")
        try:
            from kis_api import KISApiClient
            self._api = KISApiClient()
            positions = self._api.get_positions()
            count = len(positions) if positions is not None else 0
            return r.ok(f"잔고 조회 성공 — 보유종목 {count}개")
        except Exception as e:
            return r.fail(f"잔고 조회 오류: {type(e).__name__}", str(e))

    def _step6_orderable_cash(self) -> StepResult:
        r = StepResult(6, "주문가능금액 조회")
        if self._mode == "PAPER":
            return r.skip("PAPER 모드: API 통신 없음")
        if self._api is None:
            return r.skip("API 클라이언트 미초기화")
        try:
            cash = self._api.get_orderable_cash()
            return r.ok(f"주문가능금액 조회 성공", f"{cash:,.0f}원")
        except Exception as e:
            return r.fail(f"주문가능금액 조회 오류: {type(e).__name__}", str(e))

    def _step7_current_price(self) -> StepResult:
        r = StepResult(7, f"현재가 조회 [{self.stock_code}]")
        if self._mode == "PAPER":
            return r.skip("PAPER 모드: API 통신 없음")
        if self._api is None:
            return r.skip("API 클라이언트 미초기화")
        try:
            info = self._api.get_current_price(self.stock_code)
            price = info.get("current_price", 0)
            name = info.get("stock_name", self.stock_code)
            change = info.get("change_rate", 0)
            return r.ok(
                f"현재가 조회 성공",
                f"{name} ({self.stock_code}): {price:,}원 ({change:+.2f}%)",
            )
        except Exception as e:
            return r.fail(f"현재가 조회 오류: {type(e).__name__}", str(e))

    def _save_report(self) -> None:
        """검증 결과를 파일로 저장."""
        today = get_today_str("%Y%m%d")
        report_path = os.path.join(REPORT_DIR, f"api_connection_test_{today}.txt")
        lines = [
            f"한국투자증권 Open API 연결 검증 보고서",
            f"생성시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"모드: {self._mode}",
            f"종목코드: {self.stock_code}",
            "=" * 60,
        ]
        for r in self.results:
            lines.append(str(r))
        lines.append("=" * 60)
        report_text = "\n".join(lines)

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)
        logger.info("검증 보고서 저장: %s", report_path)
        print(f"보고서 저장: {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="한국투자증권 Open API 연결 검증")
    parser.add_argument("--stock-code", default="005930", help="현재가 조회 종목코드 (기본값: 005930 삼성전자)")
    parser.add_argument("--config", default="config.yaml", help="설정 파일 경로")
    args = parser.parse_args()

    tester = APIConnectionTester(config_path=args.config, stock_code=args.stock_code)
    success = tester.run_all()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
