"""Streamlit warning and notice components."""

import streamlit as st


def real_order_warning() -> None:
    """Show a high-visibility warning before REAL orders."""
    st.error(
        """
        **실전 주문 경고**

        - 실전 주문은 한국투자증권 실전 계좌에서 실제 자금으로 실행됩니다.
        - 이 프로그램은 +2% 익절 목표 전략을 사용할 수 있지만 수익을 보장하지 않습니다.
        - 주문 전 종목, 수량, 가격, 주문금액을 직접 확인해야 합니다.
        - 먼저 1주 또는 소액 테스트만 진행하세요.
        - 주문 결과와 투자 손익에 대한 책임은 사용자 본인에게 있습니다.
        """
    )


def force_trade_disclaimer() -> None:
    """Explain force_trade mode."""
    st.info(
        """
        **거래 보장 모드 안내**

        - force_trade는 최소 주문 발생을 목표로 일부 필터를 단계적으로 완화합니다.
        - 수익을 보장하는 기능이 아닙니다.
        - 거래정지, 관리종목, 투자주의환기종목 등 hard exclusion은 우회하지 않습니다.
        """
    )


def emergency_stop_warning() -> None:
    """Show emergency stop warning."""
    st.error("**긴급중단(EMERGENCY_STOP) 활성화 상태**: 모든 신규 매수가 차단됩니다.")


def no_profit_guarantee_notice() -> None:
    """Show no-profit-guarantee notice."""
    st.caption(
        "이 시스템은 +2% 익절 목표 전략을 사용할 수 있지만 수익을 보장하지 않습니다. "
        "투자 결과에 대한 책임은 사용자 본인에게 있습니다."
    )
