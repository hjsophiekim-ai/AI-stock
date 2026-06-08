"""거래 모드 배지 컴포넌트."""

import streamlit as st


MODE_COLORS = {
    "PAPER": ("#2563eb", "#dbeafe", "PAPER - 가상 거래 (실제 주문 없음)"),
    "MOCK": ("#d97706", "#fef3c7", "MOCK - 모의투자 API"),
    "REAL": ("#dc2626", "#fee2e2", "REAL - 실전투자 (실제 자금 사용!)"),
}


def render_mode_badge(mode: str) -> None:
    """모드 배지 렌더링."""
    color, bg, label = MODE_COLORS.get(mode, ("#6b7280", "#f3f4f6", mode))
    st.markdown(
        f"""<div style="display:inline-block;padding:6px 16px;border-radius:20px;
        background:{bg};color:{color};font-weight:700;font-size:14px;
        border:2px solid {color};">
        {label}
        </div>""",
        unsafe_allow_html=True,
    )


def render_mode_warning(mode: str) -> None:
    """모드별 경고 메시지."""
    if mode == "REAL":
        st.error(
            "⛔ **실전투자 모드** — 모든 주문이 실제 자금으로 체결됩니다. "
            "이 화면에서 실행하는 매수/매도는 실제 계좌에 반영됩니다."
        )
    elif mode == "MOCK":
        st.warning(
            "⚠️ **모의투자 모드** — 한국투자증권 모의투자 서버에 실제 API 호출이 발생합니다. "
            "실제 자금은 사용하지 않습니다."
        )
    else:
        st.info(
            "ℹ️ **가상 거래 모드(PAPER)** — 실제 API 호출 없음. "
            "주문은 로컬에만 기록됩니다."
        )
