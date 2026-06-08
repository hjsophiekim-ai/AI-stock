"""보유 포지션 관리 모듈.

매수가, 목표가, 손절가, 보유 시각을 추적하고
data/positions.json에 저장·복원합니다.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from utils import ensure_dir, load_config, setup_logger

logger = setup_logger(__name__, "logs/position_manager.log")


@dataclass
class PositionRecord:
    """단일 포지션 정보."""
    stock_code: str
    stock_name: str
    quantity: int
    entry_price: float
    entry_time: str          # ISO 형식 문자열
    target_price: float = 0.0
    stop_price: float = 0.0
    is_closed: bool = False
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None
    order_no: str = ""                # 주문번호
    filled_quantity: int = 0          # 체결수량
    filled_price: float = 0.0         # 체결가격
    force_trade_mode: bool = False     # force_trade로 매수한 종목 여부
    forced_exit_time_str: str = "09:30"  # 강제청산 시각 (HH:MM)

    def pnl_rate(self, current_price: Optional[float] = None) -> float:
        """현재 또는 최종 손익률 계산."""
        p = current_price or self.exit_price
        if p is None:
            return 0.0
        return (p - self.entry_price) / (self.entry_price + 1e-9)


class PositionManager:
    """보유 포지션 관리 클래스.

    매수 후 포지션을 기록하고 익절/손절/강제청산 시점을 판단합니다.
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        strategy = self.cfg.get("strategy", {})
        self._target_rate: float = strategy.get("target_profit_rate", 0.02)
        self._stop_rate: float = strategy.get("stop_loss_rate", -0.03)
        self._forced_exit_time: str = strategy.get("forced_exit_time_next_day", "09:30")
        self._positions_file = Path(
            self.cfg.get("data", {}).get("positions_file", "data/positions.json")
        )
        self._positions: Dict[str, PositionRecord] = {}
        self._load_local_positions()

    # ------------------------------------------------------------------
    # 저장·복원
    # ------------------------------------------------------------------

    def _load_local_positions(self) -> None:
        """파일에서 포지션 복원 (프로그램 재시작 시).

        positions.json이 [] 리스트이면 {} 빈 딕셔너리로 자동 보정합니다.
        JSON 파싱 실패 시 백업을 만들고 빈 포지션으로 초기화합니다.
        """
        if not self._positions_file.exists():
            return
        try:
            with open(self._positions_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # [] 리스트 또는 None → {} 딕셔너리로 보정
            if data is None or isinstance(data, list):
                logger.warning(
                    "positions.json이 %s 형식 — 빈 딕셔너리로 초기화합니다.",
                    type(data).__name__,
                )
                data = {}
                # 보정된 내용을 즉시 저장
                with open(self._positions_file, "w", encoding="utf-8") as fw:
                    json.dump(data, fw, ensure_ascii=False, indent=2)

            self._positions = {
                code: PositionRecord(**rec)
                for code, rec in data.items()
            }
            logger.info("포지션 복원 완료: %d개 종목", len(self._positions))

        except json.JSONDecodeError as e:
            # JSON 파싱 실패 → 백업 후 초기화
            backup = self._positions_file.with_suffix(".json.bak")
            try:
                self._positions_file.rename(backup)
                logger.error("positions.json 파싱 실패 → 백업: %s | 오류: %s", backup, str(e))
            except Exception:
                pass
            self._positions = {}
            with open(self._positions_file, "w", encoding="utf-8") as fw:
                json.dump({}, fw, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error("포지션 파일 로드 실패: %s", str(e))
            self._positions = {}

    def save_local_positions(self) -> None:
        """현재 포지션을 파일에 저장."""
        ensure_dir(str(self._positions_file.parent))
        try:
            data = {code: asdict(pos) for code, pos in self._positions.items()}
            with open(self._positions_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("포지션 저장 실패: %s", str(e))

    # ------------------------------------------------------------------
    # 브로커 동기화
    # ------------------------------------------------------------------

    def sync_positions_from_broker(self, broker_positions: "pd.DataFrame") -> None:  # noqa: F821
        """브로커 잔고와 로컬 포지션 동기화.

        브로커에 있는데 로컬에 없는 종목을 추가합니다.

        Args:
            broker_positions: kis_api.get_positions() 반환값
        """
        if broker_positions is None or len(broker_positions) == 0:
            return
        for _, row in broker_positions.iterrows():
            code = str(row["stock_code"])
            if code not in self._positions:
                logger.info(
                    "브로커 포지션 추가 (로컬 없음): %s (%s) %d주 @ %.0f원",
                    code, row.get("stock_name", ""), row["quantity"], row["avg_price"]
                )
                self.update_position_after_buy(
                    stock_code=code,
                    stock_name=str(row.get("stock_name", "")),
                    quantity=int(row["quantity"]),
                    entry_price=float(row["avg_price"]),
                    entry_time=datetime.now(),
                )
        self.save_local_positions()

    # ------------------------------------------------------------------
    # 포지션 업데이트
    # ------------------------------------------------------------------

    def update_position_after_buy(
        self,
        stock_code: str,
        stock_name: str,
        quantity: int,
        entry_price: float,
        entry_time: Optional[datetime] = None,
        order_no: str = "",
        filled_quantity: int = 0,
        filled_price: float = 0.0,
        force_trade_mode: bool = False,
    ) -> PositionRecord:
        """매수 후 포지션 추가.

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            quantity: 매수 수량
            entry_price: 매수 가격
            entry_time: 매수 시각

        Returns:
            생성된 PositionRecord
        """
        t = entry_time or datetime.now()
        pos = PositionRecord(
            stock_code=stock_code,
            stock_name=stock_name,
            quantity=quantity,
            entry_price=entry_price,
            entry_time=t.isoformat(),
            target_price=self.get_target_price(entry_price),
            stop_price=self.get_stop_loss_price(entry_price),
            order_no=order_no,
            filled_quantity=filled_quantity if filled_quantity > 0 else quantity,
            filled_price=filled_price if filled_price > 0 else entry_price,
            force_trade_mode=force_trade_mode,
            forced_exit_time_str=self._forced_exit_time,
        )
        self._positions[stock_code] = pos
        logger.info(
            "포지션 추가: %s(%s) %d주 @ %.0f원 | 목표가=%.0f | 손절가=%.0f",
            stock_code, stock_name, quantity, entry_price,
            pos.target_price, pos.stop_price,
        )
        self.save_local_positions()
        return pos

    def update_position_after_sell(
        self,
        stock_code: str,
        exit_price: float,
        exit_reason: str,
        exit_time: Optional[datetime] = None,
    ) -> Optional[PositionRecord]:
        """매도 후 포지션 청산 처리.

        Args:
            stock_code: 종목코드
            exit_price: 매도 가격
            exit_reason: 청산 사유
            exit_time: 청산 시각

        Returns:
            청산된 PositionRecord 또는 None
        """
        pos = self._positions.get(stock_code)
        if pos is None:
            logger.warning("포지션 없음: %s", stock_code)
            return None
        t = exit_time or datetime.now()
        pos.exit_price = exit_price
        pos.exit_time = t.isoformat()
        pos.exit_reason = exit_reason
        pos.is_closed = True
        logger.info(
            "포지션 청산: %s(%s) %d주 @ %.0f원 (사유=%s, 손익=%.2f%%)",
            pos.stock_code, pos.stock_name, pos.quantity, exit_price,
            exit_reason, pos.pnl_rate(exit_price) * 100,
        )
        del self._positions[stock_code]
        self.save_local_positions()
        return pos

    # ------------------------------------------------------------------
    # 목표가·손절가 계산
    # ------------------------------------------------------------------

    def get_target_price(self, entry_price: float) -> float:
        """익절 목표가 계산 (+2%).

        Args:
            entry_price: 매수가

        Returns:
            목표가
        """
        return round(entry_price * (1 + self._target_rate))

    def get_stop_loss_price(self, entry_price: float) -> float:
        """손절가 계산 (-3%).

        Args:
            entry_price: 매수가

        Returns:
            손절가
        """
        return round(entry_price * (1 + self._stop_rate))

    # ------------------------------------------------------------------
    # 청산 조건 판단
    # ------------------------------------------------------------------

    def should_take_profit(self, stock_code: str, current_price: float) -> bool:
        """+2% 익절 조건 충족 여부."""
        pos = self._positions.get(stock_code)
        if pos is None:
            return False
        return current_price >= pos.target_price

    def should_stop_loss(self, stock_code: str, current_price: float) -> bool:
        """-3% 손절 조건 충족 여부."""
        pos = self._positions.get(stock_code)
        if pos is None:
            return False
        return current_price <= pos.stop_price

    def should_force_exit(self, stock_code: str, now: Optional[datetime] = None) -> bool:
        """다음 거래일 09:30 강제청산 여부."""
        pos = self._positions.get(stock_code)
        if pos is None:
            return False
        current = now or datetime.now()
        entry_dt = datetime.fromisoformat(pos.entry_time)
        if current.date() <= entry_dt.date():
            return False
        force_h, force_m = map(int, self._forced_exit_time.split(":"))
        return current.hour > force_h or (
            current.hour == force_h and current.minute >= force_m
        )

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    def get_all_positions(self) -> Dict[str, PositionRecord]:
        """모든 보유 포지션 반환."""
        return dict(self._positions)

    def get_position(self, stock_code: str) -> Optional[PositionRecord]:
        """특정 종목 포지션 반환."""
        return self._positions.get(stock_code)

    def has_position(self, stock_code: str) -> bool:
        """특정 종목 보유 여부."""
        return stock_code in self._positions

    def position_count(self) -> int:
        """현재 보유 종목 수."""
        return len(self._positions)

    def get_force_exit_targets(self, now: Optional[datetime] = None) -> List[str]:
        """강제청산 대상 종목코드 목록."""
        return [
            code for code in self._positions
            if self.should_force_exit(code, now)
        ]
