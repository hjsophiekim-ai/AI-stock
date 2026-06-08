"""보유종목 및 매도감시 페이지."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "components"))

import streamlit as st
from config_service import load_config, get_trade_mode
from trading_service import (get_positions_with_current_price, run_sell_all,
                               run_force_sell_once, run_force_exit)
from log_service import is_emergency_stop_active
from tables import render_positions_table
from mode_badge import render_mode_badge, render_mode_warning
from warning_box import real_order_warning, no_profit_guarantee_notice

st.set_page_config(page_title="보유종목 및 매도감시", page_icon="👀", layout="wide")
st.title("👀 보유종목 및 매도감시")

cfg = load_config()
mode = get_trade_mode(cfg)

render_mode_badge(mode)
render_mode_warning(mode)
st.write("")

if is_emergency_stop_active():
    st.error("🚨 긴급중단 활성 — 신규 매수가 차단됩니다. (매도는 가능)")

# ── 보유종목 ─────────────────────────────────────────────────────
st.subheader("보유종목")
col_refresh, col_sync = st.columns([1, 3])
with col_refresh:
    if st.button("현재가 갱신", use_container_width=True):
        st.rerun()

positions = get_positions_with_current_price()
render_positions_table(positions)

if not positions:
    st.info("보유 중인 종목이 없습니다.")
    no_profit_guarantee_notice()
    st.stop()

st.divider()

# ── 매도 감시 ─────────────────────────────────────────────────────
st.subheader("매도 감시")
mon_cols = st.columns(3)
with mon_cols[0]:
    if st.button("+2% 익절 감시 1회 실행", use_container_width=True):
        with st.spinner("익절 감시 중..."):
            r = run_force_sell_once()
        if r.get("success"):
            st.success("감시 완료")
            st.json(r.get("data", {}))
        else:
            st.error(r.get("message", "오류"))
        st.rerun()

with mon_cols[1]:
    if st.button("-3% 손절 감시 1회 실행", use_container_width=True):
        with st.spinner("손절 감시 중..."):
            r = run_force_sell_once()
        if r.get("success"):
            st.success("감시 완료")
        else:
            st.error(r.get("message", "오류"))
        st.rerun()

with mon_cols[2]:
    if mode == "REAL":
        real_order_warning()
    if st.button("전체 강제청산 실행", type="primary", use_container_width=True):
        if mode == "REAL":
            real_order_warning()
            confirm = st.checkbox("강제청산을 실행하겠습니다.")
            if not confirm:
                st.stop()
        with st.spinner("강제청산 실행 중..."):
            r = run_force_exit()
        if r.get("success"):
            st.success("강제청산 완료")
            st.rerun()
        else:
            st.error(r.get("message", "오류"))

st.divider()

# ── 개별 종목 매도 ────────────────────────────────────────────────
st.subheader("개별 종목 매도")
codes = [p["stock_code"] for p in positions]
names = {p["stock_code"]: p.get("stock_name", p["stock_code"]) for p in positions}

if codes:
    sel_code = st.selectbox("종목 선택", codes,
                             format_func=lambda c: f"{c} ({names.get(c,'')})")
    reason_sel = st.selectbox("매도 사유", ["take_profit", "stop_loss", "force_exit", "manual"])
    if st.button(f"{sel_code} 매도 실행", use_container_width=True):
        if mode == "REAL":
            real_order_warning()
        with st.spinner("매도 실행 중..."):
            r = run_sell_all(sel_code, reason=reason_sel)
        if r.get("success"):
            st.success(f"{sel_code} 매도 성공")
            st.rerun()
        else:
            st.error(f"매도 실패: {r.get('reason','')}")
        st.json(r)

no_profit_guarantee_notice()
