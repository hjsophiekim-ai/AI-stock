"""자동매매 메인 실행 진입점.

+2% 익절 목표 자동매매 전략의 진입점입니다.
이 프로그램은 수익을 보장하지 않습니다.
실전 사용 전 반드시 모의투자를 완료하고 리스크를 숙지하세요.

사용법:
    python src/run_live_trader.py --mode once
    python src/run_live_trader.py --mode buy
    python src/run_live_trader.py --mode monitor
    python src/run_live_trader.py --mode force-exit
    python src/run_live_trader.py --mode loop --loop-interval 5

모드:
    once        현재 시간에 맞는 단계 자동 선택 (권장)
    buy         14:40~15:00 매수만 실행 (시간 무시)
    monitor     현재 보유 종목 매도 감시만 실행
    force-exit  전량 강제청산 실행
    loop        once를 --loop-interval(초) 간격으로 반복 실행

주의:
    실전 매매(REAL 모드)를 활성화하려면 config.yaml에서
    live_trade: true, kis.use_mock: false, safety.confirm_live_trade: true
    세 가지를 모두 설정해야 합니다.
"""

import argparse
import os
import sys
import time
from datetime import datetime

# src 디렉터리를 path에 추가
sys.path.insert(0, os.path.dirname(__file__))

from auto_trader import AutoTrader
from safety_gate import TRADE_MODE_PAPER, TRADE_MODE_MOCK, TRADE_MODE_REAL
from utils import load_config, setup_logger

logger = setup_logger(__name__, "logs/run_live_trader.log")

_BANNER_PAPER = """
╔══════════════════════════════════════════════════════════════════╗
║          [PAPER 모드] 가상 매매 — 실제 자금 이동 없음             ║
╚══════════════════════════════════════════════════════════════════╝
"""

_BANNER_MOCK = """
╔══════════════════════════════════════════════════════════════════╗
║        [MOCK 모드] 한국투자증권 모의투자 — 가상 계좌             ║
╚══════════════════════════════════════════════════════════════════╝
"""

_BANNER_REAL = """
╔══════════════════════════════════════════════════════════════════╗
║  ⚠  [REAL 모드] 한국투자증권 실전투자 — 실제 자금이 사용됩니다   ║
║                                                                  ║
║  이 프로그램은 수익을 보장하지 않습니다.                         ║
║  손실이 발생할 수 있으며, 투자 결과는 본인 책임입니다.           ║
║  반드시 모의투자 검증 후 소액으로 먼저 테스트하세요.             ║
║                                                                  ║
║  5초 후 자동 시작됩니다. 중단하려면 Ctrl+C를 누르세요.          ║
╚══════════════════════════════════════════════════════════════════╝
"""


def _print_mode_banner(mode: str) -> None:
    """모드에 맞는 경고 배너 출력."""
    if mode == TRADE_MODE_PAPER:
        print(_BANNER_PAPER)
    elif mode == TRADE_MODE_MOCK:
        print(_BANNER_MOCK)
    elif mode == TRADE_MODE_REAL:
        print(_BANNER_REAL)
        for remaining in range(5, 0, -1):
            print(f"\r  시작까지 {remaining}초 남음...", end="", flush=True)
            time.sleep(1)
        print("\n")
    else:
        print(f"[알 수 없는 모드] {mode}")


def _run_once(trader: AutoTrader) -> None:
    print(f"[run_once] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 현재 시간대 자동 감지")
    trader.run_once()


def _run_buy(trader: AutoTrader) -> None:
    print("[buy] 매수 강제 실행")
    results = trader.run_buy_only()
    success = sum(1 for r in results if r.get("success"))
    print(f"[buy 완료] {success}/{len(results)}개 종목 매수")


def _run_monitor(trader: AutoTrader) -> None:
    print("[monitor] 매도 감시 실행")
    cleared = trader.run_sell_monitor_only()
    print(f"[monitor 완료] {len(cleared)}개 종목 청산")


def _run_force_exit(trader: AutoTrader) -> None:
    mode = trader.gate.mode
    if mode == TRADE_MODE_REAL:
        confirm = input(
            "\n⚠ REAL 모드에서 전량 강제청산을 실행합니다.\n"
            "계속하려면 'CONFIRM'을 입력하세요: "
        ).strip()
        if confirm != "CONFIRM":
            print("취소되었습니다.")
            return
    print("[force-exit] 전량 강제청산 실행")
    cleared = trader.run_force_exit_only()
    print(f"[force-exit 완료] {len(cleared)}개 종목 청산")


def _run_loop(trader: AutoTrader, interval: int) -> None:
    """once를 interval 초 간격으로 반복 실행."""
    print(f"[loop] {interval}초 간격으로 반복 실행 (Ctrl+C로 중단)")
    iteration = 0
    while True:
        iteration += 1
        print(f"\n[loop:{iteration}] {datetime.now().strftime('%H:%M:%S')}")
        try:
            trader.run_once()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.exception("[loop] 실행 오류: %s", str(e))
            print(f"[loop] 오류 발생: {e}")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="+2% 익절 목표 자동매매 전략 실행기 (수익 보장 없음)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["once", "buy", "monitor", "force-exit", "loop"],
        default="once",
        help="실행 모드 (기본값: once)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="설정 파일 경로 (기본값: config.yaml)",
    )
    parser.add_argument(
        "--loop-interval",
        type=int,
        default=30,
        help="loop 모드 반복 간격(초) (기본값: 30)",
    )
    args = parser.parse_args()

    # AutoTrader 초기화
    try:
        trader = AutoTrader(args.config)
    except Exception as e:
        print(f"[오류] AutoTrader 초기화 실패: {e}")
        logger.exception("AutoTrader 초기화 실패")
        sys.exit(1)

    # 모드 배너 출력
    _print_mode_banner(trader.gate.mode)
    print(f"설정 파일: {args.config}")
    print(f"실행 모드: {args.mode}")
    print(f"매매 모드: {trader.gate.mode}\n")

    # 실행
    try:
        if args.mode == "once":
            _run_once(trader)
        elif args.mode == "buy":
            _run_buy(trader)
        elif args.mode == "monitor":
            _run_monitor(trader)
        elif args.mode == "force-exit":
            _run_force_exit(trader)
        elif args.mode == "loop":
            _run_loop(trader, args.loop_interval)
    except KeyboardInterrupt:
        print("\n\n[중단] 사용자에 의해 중단되었습니다.")
        logger.info("사용자 중단 (KeyboardInterrupt)")
    except Exception as e:
        print(f"\n[오류] 실행 중 오류 발생: {e}")
        logger.exception("실행 오류")
        sys.exit(1)


if __name__ == "__main__":
    main()
