"""거래일·시간 판단 모듈.

한국 주식시장 거래일과 시간대를 판단합니다.
Asia/Seoul 시간 기준으로 처리합니다.
"""

from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from utils import load_config, setup_logger

logger = setup_logger(__name__, "logs/trading_calendar.log")
KST = ZoneInfo("Asia/Seoul")

# 세션 상수 — kis_api.py resolve_order_division()에서 사용
SESSION_PRE_MARKET = "PRE_MARKET"              # 08:30~09:00 장전시간외
SESSION_REGULAR = "REGULAR"                    # 09:00~15:20 정규장
SESSION_CLOSING_AUCTION = "CLOSING_AUCTION"    # 15:20~15:30 동시호가
SESSION_AFTER_CLOSE = "AFTER_CLOSE"            # 15:30~16:00 장후시간외
SESSION_AFTER_HOURS_SINGLE = "AFTER_HOURS_SINGLE"  # 16:00~18:00 시간외단일가
SESSION_CLOSED = "CLOSED"                      # 그 외 (장 마감 또는 거래 불가 시간)

# 한국 공휴일 (매년 갱신 필요 — 또는 pykrx 사용)
# pykrx가 설치된 경우 자동으로 조회, 없으면 수동 리스트 사용
_MANUAL_HOLIDAYS_2024 = {
    date(2024, 1, 1),   # 신정
    date(2024, 2, 9),   # 설연휴
    date(2024, 2, 12),  # 설연휴
    date(2024, 3, 1),   # 삼일절
    date(2024, 4, 10),  # 총선
    date(2024, 5, 5),   # 어린이날
    date(2024, 5, 6),   # 대체공휴일
    date(2024, 5, 15),  # 부처님오신날
    date(2024, 6, 6),   # 현충일
    date(2024, 8, 15),  # 광복절
    date(2024, 9, 16),  # 추석
    date(2024, 9, 17),  # 추석
    date(2024, 9, 18),  # 추석
    date(2024, 10, 3),  # 개천절
    date(2024, 10, 9),  # 한글날
    date(2024, 12, 25), # 성탄절
}
_MANUAL_HOLIDAYS_2025 = {
    date(2025, 1, 1),
    date(2025, 1, 28),
    date(2025, 1, 29),
    date(2025, 1, 30),
    date(2025, 3, 1),
    date(2025, 5, 5),
    date(2025, 5, 6),
    date(2025, 6, 6),
    date(2025, 8, 15),
    date(2025, 10, 3),
    date(2025, 10, 6),
    date(2025, 10, 7),
    date(2025, 10, 8),
    date(2025, 10, 9),
    date(2025, 12, 25),
}
_MANUAL_HOLIDAYS_2026 = {
    date(2026, 1, 1),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 3, 2),   # 대체공휴일
    date(2026, 5, 5),
    date(2026, 6, 6),
    date(2026, 8, 17),  # 대체공휴일
    date(2026, 9, 24),
    date(2026, 9, 25),
    date(2026, 10, 3),
    date(2026, 10, 9),
    date(2026, 12, 25),
}

KNOWN_HOLIDAYS: set = _MANUAL_HOLIDAYS_2024 | _MANUAL_HOLIDAYS_2025 | _MANUAL_HOLIDAYS_2026


def _get_holidays_from_pykrx(year: int) -> set:
    """pykrx로 해당 연도 공휴일 조회. 실패 시 수동 리스트 반환."""
    try:
        from pykrx import stock
        start = f"{year}0101"
        end = f"{year}1231"
        all_dates = set()
        # 거래일 리스트를 가져와서 역으로 공휴일을 계산
        trading_dates_raw = stock.get_market_ohlcv_by_date(start, end, "005930")
        if trading_dates_raw is None or trading_dates_raw.empty:
            return set()
        trading_dates = set(pd.to_datetime(trading_dates_raw.index).date)
        # 주말 제외 평일 중 거래일이 아닌 날 = 공휴일
        cur = date(year, 1, 1)
        holidays = set()
        while cur.year == year:
            if cur.weekday() < 5 and cur not in trading_dates:
                holidays.add(cur)
            cur += timedelta(days=1)
        return holidays
    except Exception:
        return set()


def _now_kst() -> datetime:
    """현재 KST 시각 반환."""
    return datetime.now(tz=KST)


class TradingCalendar:
    """한국 주식시장 거래일·시간 판단 클래스."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        strategy = self.cfg.get("strategy", {})
        self._buy_start = self._t(strategy.get("buy_start_time", "14:40"))
        self._buy_end = self._t(strategy.get("buy_end_time", "15:00"))
        self._ah_start = self._t(strategy.get("after_hours_start_time", "15:40"))
        self._ah_end = self._t(strategy.get("after_hours_end_time", "18:00"))
        self._pm_start = self._t(strategy.get("pre_market_start_time", "08:30"))
        self._pm_end = self._t(strategy.get("pre_market_end_time", "08:40"))
        self._nm_start = self._t(strategy.get("regular_market_start_time", "09:00"))
        self._nm_end = self._t(strategy.get("forced_exit_time_next_day", "09:30"))
        self._force_exit = self._t(strategy.get("forced_exit_time_next_day", "09:30"))
        self._force_exit_end = self._t(strategy.get("forced_exit_end_time", "10:30"))
        self._holidays = KNOWN_HOLIDAYS

    @staticmethod
    def _t(s: str) -> time:
        h, m = map(int, s.split(":"))
        return time(h, m)

    def is_trading_day(self, d: Optional[date] = None) -> bool:
        """해당 날짜가 거래일인지 확인 (주말·공휴일 제외).

        Args:
            d: 확인할 날짜. None이면 오늘(KST).
        """
        d = d or _now_kst().date()
        if d.weekday() >= 5:
            return False
        return d not in self._holidays

    def is_buy_window(self, now: Optional[datetime] = None) -> bool:
        """14:40~15:00 매수 시간대 여부."""
        t = (now or _now_kst()).time().replace(tzinfo=None) if hasattr((now or _now_kst()).time(), 'tzinfo') else (now or _now_kst()).time()
        if hasattr(t, 'replace'):
            t = time(t.hour, t.minute, t.second)
        return self._buy_start <= t <= self._buy_end

    def is_after_hours_window(self, now: Optional[datetime] = None) -> bool:
        """15:40~18:00 시간외 단일가 구간 여부."""
        t = self._extract_time(now)
        return self._ah_start <= t <= self._ah_end

    def is_pre_market_window(self, now: Optional[datetime] = None) -> bool:
        """08:30~08:40 장전 시간외 구간 여부."""
        t = self._extract_time(now)
        return self._pm_start <= t <= self._pm_end

    def is_next_morning_window(self, now: Optional[datetime] = None) -> bool:
        """09:00~09:30 다음날 장초반 구간 여부."""
        t = self._extract_time(now)
        return self._nm_start <= t <= self._nm_end

    def is_force_exit_time(self, now: Optional[datetime] = None) -> bool:
        """09:30~10:30 강제청산 윈도우 여부."""
        t = self._extract_time(now)
        return self._force_exit <= t <= self._force_exit_end

    def get_next_trading_day(self, d: Optional[date] = None) -> date:
        """다음 거래일 반환."""
        cur = (d or _now_kst().date()) + timedelta(days=1)
        for _ in range(10):
            if self.is_trading_day(cur):
                return cur
            cur += timedelta(days=1)
        raise RuntimeError("다음 거래일을 찾을 수 없습니다.")

    @staticmethod
    def _extract_time(now: Optional[datetime]) -> time:
        """datetime 또는 None에서 time 객체 추출."""
        dt = now or _now_kst()
        return time(dt.hour, dt.minute, dt.second)

    def get_market_session(self, now: Optional[datetime] = None) -> str:
        """현재 시장 세션 반환.

        config.yaml trading_hours 섹션 기준으로 판단합니다.
        거래일(is_trading_day) 여부는 판단하지 않으므로, 주말/공휴일에도 시간대만 반환합니다.
        거래일 여부는 호출측에서 별도 확인하세요.

        Returns:
            SESSION_PRE_MARKET          — 장전시간외 (08:30~09:00)
            SESSION_REGULAR             — 정규장 (09:00~15:20)
            SESSION_CLOSING_AUCTION     — 동시호가 (15:20~15:30)
            SESSION_AFTER_CLOSE         — 장후시간외 (15:30~16:00)
            SESSION_AFTER_HOURS_SINGLE  — 시간외단일가 (16:00~18:00)
            SESSION_CLOSED              — 그 외
        """
        t = self._extract_time(now)
        th = self.cfg.get("trading_hours", {})

        def _tc(key: str, default: str) -> time:
            return self._t(th.get(key, default))

        pre_s = _tc("pre_market_start", "08:30")
        pre_e = _tc("pre_market_end", "09:00")
        reg_s = _tc("regular_start", "09:00")
        reg_e = _tc("regular_end", "15:20")
        ca_s = _tc("closing_auction_start", "15:20")
        ca_e = _tc("closing_auction_end", "15:30")
        ac_s = _tc("after_close_start", "15:30")
        ac_e = _tc("after_close_end", "16:00")
        ahs_s = _tc("after_hours_single_start", "16:00")
        ahs_e = _tc("after_hours_single_end", "18:00")

        if pre_s <= t < pre_e:
            return SESSION_PRE_MARKET
        if reg_s <= t < reg_e:
            return SESSION_REGULAR
        if ca_s <= t < ca_e:
            return SESSION_CLOSING_AUCTION
        if ac_s <= t < ac_e:
            return SESSION_AFTER_CLOSE
        if ahs_s <= t <= ahs_e:
            return SESSION_AFTER_HOURS_SINGLE
        return SESSION_CLOSED

    def is_session_allowed(self, session: str, after_hours_cfg: Optional[dict] = None) -> bool:
        """세션이 config 기준으로 허용되는지 확인.

        Args:
            session: get_market_session() 반환값
            after_hours_cfg: config.yaml after_hours 섹션 (None이면 self.cfg에서 읽음)
        """
        ah = after_hours_cfg or self.cfg.get("after_hours", {})
        if not ah.get("enabled", True):
            return session == SESSION_REGULAR
        mapping = {
            SESSION_PRE_MARKET: ah.get("allow_pre_market", True),
            SESSION_REGULAR: ah.get("allow_regular", True),
            SESSION_CLOSING_AUCTION: ah.get("allow_closing_auction", False),
            SESSION_AFTER_CLOSE: ah.get("allow_after_close", False),
            SESSION_AFTER_HOURS_SINGLE: ah.get("allow_after_hours_single", True),
            SESSION_CLOSED: False,
        }
        return bool(mapping.get(session, False))

    def current_window_name(self, now: Optional[datetime] = None) -> str:
        """현재 시간대 이름 반환."""
        if self.is_buy_window(now):
            return "BUY_WINDOW"
        if self.is_after_hours_window(now):
            return "AFTER_HOURS"
        if self.is_pre_market_window(now):
            return "PRE_MARKET"
        if self.is_force_exit_time(now):
            return "FORCE_EXIT"
        if self.is_next_morning_window(now):
            return "NEXT_MORNING"
        return "IDLE"
