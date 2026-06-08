"""API 설정 페이지."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))

import streamlit as st
from env_service import (load_env, save_mock_keys, save_real_keys,
                          get_masked_env, is_env_file_exists,
                          check_mock_keys, check_real_keys)
from config_service import (load_config, save_config, get_trade_mode,
                              get_safety_status, enable_paper_mode,
                              enable_mock_mode, enable_real_mode)

st.set_page_config(page_title="API 설정", page_icon="🔑", layout="wide")
st.title("🔑 API 설정")
st.caption("한국투자증권 Open API 키와 거래 모드를 설정합니다.")

# ── .env 파일 상태 ──────────────────────────────────────────────
if is_env_file_exists():
    st.success(".env 파일이 존재합니다.")
    masked = get_masked_env()
    with st.expander("현재 저장된 값 (마스킹)"):
        for k, v in masked.items():
            st.text(f"{k} = {v}")
else:
    st.warning(".env 파일이 없습니다. 아래에서 API 키를 입력하고 저장하세요.")

st.divider()

# ── 모의투자 API ────────────────────────────────────────────────
st.subheader("모의투자 API 정보")
st.caption("KIS Developers에서 모의투자용 앱 등록 후 발급받은 키를 입력하세요.")

mock_check = check_mock_keys()
for k, v in mock_check.items():
    st.caption(f"{'✅' if v else '❌'} {k}")

with st.form("mock_form"):
    mock_app_key = st.text_input("KIS_APP_KEY (모의투자 앱키)", placeholder="PSxxx...")
    mock_app_secret = st.text_input("KIS_APP_SECRET (모의투자 앱시크릿)",
                                     type="password", placeholder="앱시크릿 입력")
    mock_account_no = st.text_input("KIS_MOCK_ACCOUNT_NO (모의투자 계좌번호)",
                                     placeholder="50XXXXXXXX")
    mock_product_code = st.text_input("KIS_MOCK_ACCOUNT_PRODUCT_CODE", value="01")
    if st.form_submit_button("모의투자 키 저장", type="primary"):
        if not mock_app_key or not mock_app_secret or not mock_account_no:
            st.error("모든 필드를 입력하세요.")
        else:
            save_mock_keys(mock_app_key, mock_app_secret, mock_account_no, mock_product_code)
            st.success("모의투자 API 키가 저장되었습니다.")
            st.rerun()

st.divider()

# ── 실전투자 API ────────────────────────────────────────────────
st.subheader("실전투자 API 정보")
st.warning(
    "⚠️ 실전투자 API 정보 저장은 실제 주문 가능성을 열어두는 것입니다. "
    "반드시 소액 테스트 후 사용하세요."
)

real_check = check_real_keys()
for k, v in real_check.items():
    st.caption(f"{'✅' if v else '❌'} {k}")

with st.form("real_form"):
    real_app_key = st.text_input("KIS_REAL_APP_KEY (실전투자 앱키)")
    real_app_secret = st.text_input("KIS_REAL_APP_SECRET (실전투자 앱시크릿)",
                                     type="password")
    real_account_no = st.text_input("KIS_ACCOUNT_NO (실전투자 계좌번호)")
    real_product_code = st.text_input("KIS_ACCOUNT_PRODUCT_CODE", value="01")
    if st.form_submit_button("실전투자 키 저장"):
        if not real_app_key or not real_app_secret or not real_account_no:
            st.error("모든 필드를 입력하세요.")
        else:
            save_real_keys(real_app_key, real_app_secret, real_account_no, real_product_code)
            st.success("실전투자 API 키가 저장되었습니다.")
            st.rerun()

st.divider()

# ── 거래 모드 설정 ──────────────────────────────────────────────
st.subheader("거래 모드 설정")

cfg = load_config()
current_mode = get_trade_mode(cfg)
status = get_safety_status(cfg)

mode_colors = {"PAPER": "blue", "MOCK": "orange", "REAL": "red"}
st.markdown(f"**현재 모드:** `{current_mode}`")

col_paper, col_mock, col_real = st.columns(3)
with col_paper:
    if st.button("PAPER 모드로 전환", use_container_width=True,
                 type="primary" if current_mode == "PAPER" else "secondary"):
        enable_paper_mode()
        st.success("PAPER 모드로 전환되었습니다.")
        st.rerun()
with col_mock:
    if st.button("MOCK 모드로 전환", use_container_width=True,
                 type="primary" if current_mode == "MOCK" else "secondary"):
        enable_mock_mode()
        st.success("MOCK 모드로 전환되었습니다.")
        st.rerun()

with col_real:
    with st.expander("REAL 모드 전환 (3중 안전장치 필요)"):
        st.error("⛔ REAL 모드는 실제 자금으로 주문이 실행됩니다!")
        chk1 = st.checkbox("실전투자 위험을 이해했습니다.")
        chk2 = st.checkbox("수익이 보장되지 않음을 이해했습니다.")
        chk3 = st.checkbox("소액 테스트 목적으로만 사용합니다.")
        if all([chk1, chk2, chk3]):
            if st.button("REAL 모드로 전환", type="primary", use_container_width=True):
                enable_real_mode()
                st.success("REAL 모드로 전환되었습니다. 반드시 소액으로만 테스트하세요.")
                st.rerun()
        else:
            st.warning("3가지 항목을 모두 체크해야 REAL 모드로 전환할 수 있습니다.")

st.divider()
st.subheader("현재 안전장치 상태")
for key, label in [
    ("live_trade", "live_trade"), ("use_mock", "kis.use_mock"),
    ("confirm_live_trade", "safety.confirm_live_trade"),
    ("force_trade_enabled", "force_trade.enabled"),
]:
    val = status.get(key, False)
    st.caption(f"{'✅' if val else '⬜'} {label}: `{val}`")

st.divider()
st.caption("⚠️ .env 파일은 로컬에만 저장됩니다. Git에는 절대 올라가지 않습니다.")
