"""긴급 중단 모듈.

data/EMERGENCY_STOP 파일이 존재하면 신규주문을 즉시 차단합니다.
강제청산은 별도 설정에 따라 허용됩니다.

사용법:
    # 긴급 중단 활성화
    type nul > data/EMERGENCY_STOP    (Windows)
    touch data/EMERGENCY_STOP         (Linux/Mac)

    # 긴급 중단 해제
    del data/EMERGENCY_STOP           (Windows)
    rm data/EMERGENCY_STOP            (Linux/Mac)
"""

import os
from datetime import datetime
from pathlib import Path

from utils import load_config, setup_logger

logger = setup_logger(__name__, "logs/emergency_stop.log")
STOP_FILE = Path("data/EMERGENCY_STOP")


class EmergencyStop:
    """긴급 중단 상태 관리 클래스."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        self._stop_file = STOP_FILE
        self._last_check: float = 0.0
        self._cached_state: bool = False

    def is_active(self) -> bool:
        """긴급 중단 상태 여부 확인.

        EMERGENCY_STOP 파일이 존재하면 True를 반환합니다.
        매 호출마다 파일 시스템을 확인합니다.
        """
        active = self._stop_file.exists()
        if active and not self._cached_state:
            logger.critical(
                f"⛔ 긴급 중단 활성화 감지: {self._stop_file.absolute()} 파일 존재. "
                "신규 매수를 즉시 중단합니다."
            )
        self._cached_state = active
        return active

    def assert_not_stopped(self) -> None:
        """긴급 중단 상태이면 RuntimeError 발생.

        Raises:
            RuntimeError: EMERGENCY_STOP 파일이 존재할 때
        """
        if self.is_active():
            raise RuntimeError(
                "긴급 중단(EMERGENCY_STOP) 상태입니다. "
                f"data/EMERGENCY_STOP 파일을 삭제하면 재개됩니다."
            )

    def activate(self, reason: str = "수동 활성화") -> None:
        """긴급 중단 파일 생성."""
        os.makedirs("data", exist_ok=True)
        with open(self._stop_file, "w", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} | {reason}\n")
        logger.critical(f"⛔ 긴급 중단 파일 생성: {reason}")

    def deactivate(self) -> None:
        """긴급 중단 파일 삭제."""
        if self._stop_file.exists():
            self._stop_file.unlink()
            logger.info("✅ 긴급 중단 해제됨")
        self._cached_state = False

    def allow_force_exit(self) -> bool:
        """긴급 중단 중에도 강제청산 허용 여부.

        config.yaml emergency_liquidation_enabled 기준.
        """
        return self.cfg.get("risk", {}).get("emergency_liquidation_enabled", True)
