"""거래전략 설정 모듈.

2가지 전략을 정의합니다:
  - morning_0930: 장초반 매매 (9:30경 매수, +2% 익절)
  - afternoon_1500: 종가 매매 (15:00경 매수, 시간외·프리마켓·다음날 장중 +2% 익절)
"""

from typing import Dict, List

STRATEGIES: Dict[str, dict] = {
    "morning_0930": {
        "id": "morning_0930",
        "name": "장초반 매매",
        "display_name": "장초반 매매 — 9시30분경 매수",
        "description": "오전 9시30분경 AI 후보 리스트를 매수하고 +2% 도달 시 매도하는 전략",
        "buy_window_start": "09:25",
        "buy_window_end": "09:40",
        "allowed_buy_sessions": ["REGULAR"],
        "take_profit_rate": 0.02,
        "stop_loss_rate": -0.03,
        "allowed_sell_sessions": ["REGULAR"],
        "after_hours_sell_allowed": False,
        "pre_market_sell_allowed": False,
        "force_exit_rule": "same_day_or_manual",
        "default_sell_policy_id": "fixed_2pct",
        "available_sell_policy_ids": ["fixed_2pct", "market_strength_trailing", "manual_hold"],
    },
    "afternoon_1500": {
        "id": "afternoon_1500",
        "name": "종가 매매",
        "display_name": "종가 매매 — 오후 3시경 매수",
        "description": (
            "오후 3시경 AI 후보 리스트를 매수하고 "
            "시간외, 프리마켓, 다음날 장중에서 +2% 도달 시 매도하는 전략"
        ),
        "buy_window_start": "14:50",
        "buy_window_end": "15:20",
        "allowed_buy_sessions": ["REGULAR", "CLOSING_AUCTION"],
        "take_profit_rate": 0.02,
        "stop_loss_rate": -0.03,
        "allowed_sell_sessions": ["REGULAR", "AFTER_CLOSE", "AFTER_HOURS_SINGLE", "PRE_MARKET"],
        "after_hours_sell_allowed": True,
        "pre_market_sell_allowed": True,
        "force_exit_rule": "next_day_0930_or_after",
        "default_sell_policy_id": "fixed_2pct",
        "available_sell_policy_ids": ["fixed_2pct", "market_strength_trailing", "manual_hold"],
    },
}

_DISPLAY_LABELS = {
    "morning_0930": "장초반 매매 — 9시30분경 매수, +2% 익절 목표",
    "afternoon_1500": "종가 매매 — 오후 3시경 매수, 시간외·프리마켓·다음날 장중 +2% 익절 목표",
}


def get_strategy(strategy_id: str) -> dict:
    """전략 정보 반환. 없으면 morning_0930 기본값."""
    return STRATEGIES.get(strategy_id, STRATEGIES["morning_0930"])


def list_strategies() -> List[dict]:
    """전략 목록 반환."""
    return list(STRATEGIES.values())


def validate_strategy_id(strategy_id: str) -> bool:
    """유효한 전략 ID인지 확인."""
    return strategy_id in STRATEGIES


def get_strategy_options_for_ui() -> Dict[str, str]:
    """Streamlit 라디오 버튼용 {display_label: strategy_id} 딕셔너리."""
    return {v: k for k, v in _DISPLAY_LABELS.items()}


def get_strategy_display_labels() -> List[str]:
    """UI 표시용 레이블 목록 (순서 고정)."""
    return [_DISPLAY_LABELS["morning_0930"], _DISPLAY_LABELS["afternoon_1500"]]


def label_to_strategy_id(label: str) -> str:
    """UI 레이블 → strategy_id 변환. 매핑 없으면 morning_0930."""
    mapping = get_strategy_options_for_ui()
    return mapping.get(label, "morning_0930")
