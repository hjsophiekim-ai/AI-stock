"""KPI 카드 컴포넌트."""

import streamlit as st
from typing import Optional


def metric_card(label: str, value: str, delta: Optional[str] = None,
                delta_color: str = "normal") -> None:
    """단일 지표 카드."""
    st.metric(label=label, value=value, delta=delta, delta_color=delta_color)


def render_summary_cards(summary: dict) -> None:
    """성과 요약 카드 그리드 렌더링."""
    cols = st.columns(4)
    with cols[0]:
        st.metric("총 거래 횟수", f"{summary.get('total_trades', 0)}건")
    with cols[1]:
        wr = summary.get("win_rate", 0)
        st.metric("승률", f"{wr:.1f}%")
    with cols[2]:
        rpnl = summary.get("realized_pnl", 0)
        color = "normal" if rpnl >= 0 else "inverse"
        st.metric("실현손익", f"{rpnl:+,.0f}원")
    with cols[3]:
        cr = summary.get("cumulative_return_pct", 0)
        st.metric("누적수익률", f"{cr:+.2f}%")

    cols2 = st.columns(4)
    with cols2[0]:
        st.metric("+2% 익절", f"{summary.get('take_profit_count', 0)}건")
    with cols2[1]:
        st.metric("-3% 손절", f"{summary.get('stop_loss_count', 0)}건")
    with cols2[2]:
        st.metric("강제청산", f"{summary.get('forced_exit_count', 0)}건")
    with cols2[3]:
        up = summary.get("unrealized_pnl", 0)
        st.metric("평가손익", f"{up:+,.0f}원")
