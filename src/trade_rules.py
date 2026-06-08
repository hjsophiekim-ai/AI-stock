"""매매 규칙 모듈.

매수 시간, 익절/손절 조건, 강제청산 규칙을 정의합니다.
실제 주문 실행은 order_manager.py에서 담당합니다.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional

from utils import load_config, setup_logger

logger = setup_logger(__name__, "logs/trade_rules.log")
cfg = load_config("config.yaml")


@dataclass
class Position:
    """보유 포지션 정보."""
    ticker: str
    name: str
    entry_price: float
    qty: int
    entry_time: datetime
    target_price: float = field(init=False)
    stop_price: float = field(init=False)
    forced_exit_time: Optional[datetime] = None
    is_closed: bool = False
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None

    def __post_init__(self) -> None:
        strategy = cfg.get("strategy", {})
        self.target_price = self.entry_price * (1 + strategy.get("target_profit_rate", 0.02))
        self.stop_price = self.entry_price * (1 + strategy.get("stop_loss_rate", -0.03))

    @property
    def pnl_rate(self) -> float:
        """현재 또는 최종 손익률."""
        if self.exit_price is not None:
            return (self.exit_price - self.entry_price) / self.entry_price
        return 0.0

    @property
    def pnl_amount(self) -> float:
        """현재 또는 최종 손익 금액."""
        if self.exit_price is not None:
            return (self.exit_price - self.entry_price) * self.qty
        return 0.0


class TradeRules:
    """매매 규칙 판단 클래스."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        strategy = self.cfg.get("strategy", {})
        self.buy_start = self._parse_time(strategy.get("buy_start_time", "14:40"))
        self.buy_end = self._parse_time(strategy.get("buy_end_time", "15:00"))
        self.forced_exit_time = self._parse_time(
            strategy.get("forced_exit_time_next_day", "09:30")
        )
        self.target_profit = strategy.get("target_profit_rate", 0.02)
        self.stop_loss = strategy.get("stop_loss_rate", -0.03)

    def _parse_time(self, t_str: str) -> time:
        """'HH:MM' 형식 문자열을 time 객체로 변환."""
        h, m = map(int, t_str.split(":"))
        return time(h, m)

    def is_buy_window(self, now: Optional[datetime] = None) -> bool:
        """현재 시간이 매수 가능 시간대인지 확인 (14:40~15:00).

        Args:
            now: 현재 시각 (None이면 실제 시간 사용)
        """
        current = (now or datetime.now()).time()
        return self.buy_start <= current <= self.buy_end

    def should_take_profit(self, position: Position, current_price: float) -> bool:
        """+2% 익절 조건 충족 여부.

        Args:
            position: 보유 포지션
            current_price: 현재가
        """
        return current_price >= position.target_price

    def should_stop_loss(self, position: Position, current_price: float) -> bool:
        """손절 조건 충족 여부.

        Args:
            position: 보유 포지션
            current_price: 현재가
        """
        return current_price <= position.stop_price

    def should_force_exit(self, position: Position, now: Optional[datetime] = None) -> bool:
        """강제청산 시간 도달 여부 (다음날 09:30).

        Args:
            position: 보유 포지션
            now: 현재 시각 (None이면 실제 시간 사용)
        """
        current = (now or datetime.now()).time()
        # 포지션 진입 다음날이어야 함
        current_dt = now or datetime.now()
        entry_date = position.entry_time.date()
        current_date = current_dt.date()
        if current_date <= entry_date:
            return False
        return current >= self.forced_exit_time

    def check_exit_condition(
        self,
        position: Position,
        current_price: float,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        """매도 조건 확인.

        Args:
            position: 보유 포지션
            current_price: 현재가
            now: 현재 시각

        Returns:
            매도 사유 문자열 또는 None (매도 불필요)
            'take_profit' | 'stop_loss' | 'forced_exit' | None
        """
        if position.is_closed:
            return None
        if self.should_take_profit(position, current_price):
            return "take_profit"
        if self.should_stop_loss(position, current_price):
            return "stop_loss"
        if self.should_force_exit(position, now):
            return "forced_exit"
        return None

    def calc_position_size(
        self,
        capital: float,
        current_price: float,
        n_positions: int = 20,
    ) -> int:
        """매수 수량 계산.

        Args:
            capital: 투자 가능 금액
            current_price: 현재가
            n_positions: 총 포지션 수

        Returns:
            매수 수량 (주)
        """
        max_weight = self.cfg["risk"].get("max_position_weight", 0.05)
        per_position = capital * max_weight
        qty = int(per_position // current_price)
        return max(qty, 0)
