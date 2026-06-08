"""스케줄러 모듈.

장중 실행 순서를 정의합니다.
실제 자동 스케줄 실행은 아직 구현되지 않았습니다.
각 단계를 함수로 정의하여 수동 또는 외부 스케줄러(cron, Task Scheduler)와 연동 가능합니다.

실행 순서:
  14:30 → 데이터 업데이트
  14:40 → 후보 선정
  14:40~15:00 → 가상/실제 매수
  시간외~다음날 09:30 → 매도 감시
  09:30 → 강제청산
"""

import os
import subprocess
import sys
from datetime import datetime
from typing import Optional

from utils import get_today_str, load_config, setup_logger

logger = setup_logger(__name__, "logs/scheduler.log")
cfg = load_config("config.yaml")


def step_update_data() -> None:
    """14:30 — 일봉 및 분봉 데이터 업데이트."""
    logger.info("[14:30] 데이터 업데이트 시작")
    _run_script("src/collect_daily_data.py")
    _run_script("src/collect_intraday_data.py")
    logger.info("[14:30] 데이터 업데이트 완료")


def step_make_features() -> None:
    """피처 및 라벨 재생성."""
    logger.info("피처/라벨 생성 시작")
    _run_script("src/make_features.py")
    _run_script("src/make_labels.py")
    logger.info("피처/라벨 생성 완료")


def step_predict(now: Optional[datetime] = None) -> None:
    """14:40 — 전 종목 상승확률 예측 및 Top 20 선정."""
    logger.info("[14:40] 예측 및 Top 20 선정 시작")
    _run_script("src/predict_candidates.py")
    _run_script("src/select_top20.py")
    logger.info("[14:40] Top 20 선정 완료")


def step_buy(now: Optional[datetime] = None) -> None:
    """14:40~15:00 — 가상 또는 실제 매수."""
    live_trade = cfg.get("live_trade", False)
    mode = "실제" if live_trade else "가상"
    logger.info(f"[14:40~15:00] {mode} 매수 시작")

    if live_trade:
        logger.warning("⚠️  실제 주문 실행 중입니다!")
        # TODO: 실전 주문 로직 연동
    else:
        _run_script("src/paper_trade.py")

    logger.info(f"[14:40~15:00] {mode} 매수 완료")


def step_monitor_exit() -> None:
    """시간외~다음날 09:30 — 매도 감시 (보유 포지션 +2% 도달 체크)."""
    logger.info("매도 감시 시작")
    # TODO: 실시간 가격 모니터링 및 익절/손절 실행
    # 현재는 구조만 정의
    logger.info("매도 감시 실행 (현재 미구현: 별도 모니터링 프로세스 필요)")


def step_force_exit() -> None:
    """다음날 09:30 — 전량 강제청산."""
    logger.info("[09:30] 강제청산 시작")
    # TODO: 모든 보유 포지션 시장가 청산
    logger.info("[09:30] 강제청산 완료 (현재 미구현)")


def run_daily_pipeline() -> None:
    """전체 일일 파이프라인 순차 실행.

    수동 실행 또는 외부 스케줄러(cron, Task Scheduler)에서 호출합니다.
    """
    today = get_today_str()
    logger.info(f"=== 일일 파이프라인 시작: {today} ===")

    try:
        step_update_data()
        step_predict()
        step_buy()
    except Exception as e:
        logger.error(f"파이프라인 오류: {e}")
        raise

    logger.info(f"=== 일일 파이프라인 완료: {today} ===")


def _run_script(script_path: str) -> None:
    """Python 스크립트 실행.

    Args:
        script_path: 실행할 스크립트 경로
    """
    result = subprocess.run(
        [sys.executable, script_path],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        logger.error(f"스크립트 실패 [{script_path}]:\n{result.stderr}")
        raise RuntimeError(f"스크립트 실패: {script_path}")
    if result.stdout:
        logger.debug(f"[{script_path}] stdout: {result.stdout[-500:]}")


if __name__ == "__main__":
    run_daily_pipeline()
