"""공통 Config 상태 패널 컴포넌트."""

import streamlit as st
from typing import Dict


def render_config_status(status: Dict) -> None:
    """config 안전장치 상태를 사이드바 또는 메인에 표시."""
    mode = status.get("mode", "PAPER")
    checks = {
        "live_trade": status.get("live_trade", False),
        "paper_trade": status.get("paper_trade", True),
        "use_mock": status.get("use_mock", True),
        "confirm_live_trade": status.get("confirm_live_trade", False),
        "force_trade_enabled": status.get("force_trade_enabled", False),
        "allow_real_test_order": status.get("allow_real_test_order", False),
    }
    label_map = {
        "live_trade": "live_trade",
        "paper_trade": "paper_trade",
        "use_mock": "kis.use_mock",
        "confirm_live_trade": "safety.confirm_live_trade",
        "force_trade_enabled": "force_trade.enabled",
        "allow_real_test_order": "allow_real_test_order",
    }
    for key, val in checks.items():
        icon = "✅" if val else "⬜"
        st.caption(f"{icon} {label_map[key]}: `{val}`")
