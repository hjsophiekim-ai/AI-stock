"""로그 및 긴급중단 페이지."""

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))

import streamlit as st
from log_service import (read_log, read_no_trade_analysis, read_report,
                          is_emergency_stop_active, activate_emergency_stop,
                          deactivate_emergency_stop, list_available_logs,
                          get_log_text_for_download)

st.set_page_config(page_title="로그 및 긴급중단", page_icon="🚨", layout="wide")
st.title("🚨 로그 및 긴급중단")

# ── 긴급중단 ─────────────────────────────────────────────────────
es_active = is_emergency_stop_active()
st.subheader("긴급중단 제어")

if es_active:
    st.error("🚨 긴급중단 활성 상태 — 모든 신규 매수가 차단됩니다.")
    if st.button("✅ 긴급중단 해제", type="primary", use_container_width=True):
        r = deactivate_emergency_stop()
        st.success(r["message"]) if r["success"] else st.error(r["message"])
        st.rerun()
else:
    st.success("긴급중단 비활성 상태")
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("🚨 긴급중단 ON", type="primary", use_container_width=True):
            r = activate_emergency_stop()
            st.success(r["message"]) if r["success"] else st.error(r["message"])
            st.rerun()

st.divider()

# ── 로그 탭 ──────────────────────────────────────────────────────
available_logs = list_available_logs()
log_tabs_labels = ["trade.log", "api.log", "error.log", "API 연결 보고서", "no_trade 분석"]
tab1, tab2, tab3, tab4, tab5 = st.tabs(log_tabs_labels)

today_str = datetime.now().strftime("%Y%m%d")

with tab1:
    st.subheader("trade.log (최근 200줄)")
    text = read_log("trade.log", last_n=200)
    st.text_area("trade.log", value=text, height=400)
    if st.button("trade.log 새로고침"):
        st.rerun()
    st.download_button("trade.log 다운로드",
                        get_log_text_for_download("trade.log"),
                        "trade.log", "text/plain")

with tab2:
    st.subheader("api.log (최근 200줄)")
    text = read_log("api.log", last_n=200)
    st.text_area("api.log", value=text, height=400)
    if st.button("api.log 새로고침"):
        st.rerun()
    st.download_button("api.log 다운로드",
                        get_log_text_for_download("api.log"),
                        "api.log", "text/plain")

with tab3:
    st.subheader("error.log (최근 200줄)")
    text = read_log("error.log", last_n=200)
    st.text_area("error.log", value=text, height=400)
    if st.button("error.log 새로고침"):
        st.rerun()
    st.download_button("error.log 다운로드",
                        get_log_text_for_download("error.log"),
                        "error.log", "text/plain")

with tab4:
    st.subheader(f"API 연결 보고서 ({today_str})")
    rpt_path = PROJECT_ROOT / "reports" / f"api_connection_test_{today_str}.txt"
    text = read_report(rpt_path)
    st.text_area("api_connection_test", value=text, height=400)
    if rpt_path.exists():
        st.download_button("보고서 다운로드",
                            rpt_path.read_text(encoding="utf-8", errors="replace"),
                            rpt_path.name, "text/plain")

with tab5:
    st.subheader(f"no_trade 원인 분석 ({today_str})")
    text = read_no_trade_analysis(today_str)
    st.text_area("no_trade_analysis", value=text, height=400)
    no_trade_path = PROJECT_ROOT / "reports" / f"no_trade_analysis_{today_str}.txt"
    if no_trade_path.exists():
        st.download_button("분석 보고서 다운로드",
                            no_trade_path.read_text(encoding="utf-8", errors="replace"),
                            no_trade_path.name, "text/plain")

st.divider()

# ── 로그 목록 ────────────────────────────────────────────────────
st.subheader("사용 가능한 로그 파일")
if available_logs:
    st.write(", ".join(available_logs))
else:
    st.info("logs/ 디렉토리에 로그 파일이 없습니다.")
