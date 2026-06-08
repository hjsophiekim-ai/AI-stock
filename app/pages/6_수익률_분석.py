"""수익률 분석 페이지."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "components"))

import streamlit as st
import pandas as pd
from performance_service import (get_summary, get_daily_pnl, load_order_logs,
                                   calc_realized_pnl, calc_win_rate,
                                   calc_cumulative_return)
from metric_cards import render_summary_cards
from charts import pnl_bar_chart, cumulative_return_chart, ticker_pnl_chart

st.set_page_config(page_title="수익률 분석", page_icon="📊", layout="wide")
st.title("📊 수익률 분석")
st.caption("+2% 익절 목표 전략 — 수익을 보장하지 않습니다.")

# ── 성과 요약 카드 ──────────────────────────────────────────────
summary = get_summary()
render_summary_cards(summary)

st.divider()

# ── 거래 통계 ───────────────────────────────────────────────────
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("총 거래 횟수", f"{summary['total_trades']}건")
    st.metric("+2% 익절", f"{summary['take_profit_count']}건")
with col2:
    st.metric("-3% 손절", f"{summary['stop_loss_count']}건")
    st.metric("강제청산", f"{summary['forced_exit_count']}건")
with col3:
    st.metric("현재 보유 종목", f"{summary['position_count']}종목")
    st.metric("누적수익률", f"{summary['cumulative_return_pct']:+.2f}%")

st.divider()

# ── 차트 ────────────────────────────────────────────────────────
try:
    import plotly.express as px
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False
    st.warning("plotly가 설치되지 않아 차트를 표시할 수 없습니다. pip install plotly")

order_df = load_order_logs()
daily_pnl = get_daily_pnl(order_df)

if HAS_PLOTLY:
    tab1, tab2, tab3 = st.tabs(["일별 손익", "누적수익률", "종목별 손익"])
    with tab1:
        fig = pnl_bar_chart(daily_pnl)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("거래 데이터가 없습니다.")
    with tab2:
        fig2 = cumulative_return_chart(daily_pnl)
        if fig2:
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("거래 데이터가 없습니다.")
    with tab3:
        if not order_df.empty and "stock_code" in order_df.columns:
            fig3 = ticker_pnl_chart(order_df)
            if fig3:
                st.plotly_chart(fig3, use_container_width=True)
            else:
                st.info("종목별 손익 데이터가 없습니다.")
        else:
            st.info("거래 데이터가 없습니다.")

st.divider()

# ── 거래 내역 테이블 ────────────────────────────────────────────
st.subheader("전체 거래 내역")
if not order_df.empty:
    st.dataframe(order_df, use_container_width=True)
    csv = order_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("거래 내역 다운로드", csv, "order_log.csv", "text/csv")
else:
    st.info("거래 내역이 없습니다.")

# ── PAPER/MOCK/REAL 구분 ─────────────────────────────────────────
if not order_df.empty and "mode" in order_df.columns:
    st.subheader("모드별 손익")
    if HAS_PLOTLY:
        mode_pnl = order_df.groupby("mode").apply(
            lambda df: ((df.get("sell_price", 0) - df.get("entry_price", 0)) *
                        df.get("quantity", 0)).sum()
        ).reset_index(name="pnl")
        fig_mode = px.bar(mode_pnl, x="mode", y="pnl", title="모드별 손익",
                          labels={"mode": "거래 모드", "pnl": "손익(원)"})
        st.plotly_chart(fig_mode, use_container_width=True)

st.caption("⚠️ 이 시스템은 +2% 익절 목표 전략을 사용하며, 수익을 보장하지 않습니다.")
