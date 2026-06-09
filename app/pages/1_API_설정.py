"""API and trading mode settings page."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))

import streamlit as st

from config_service import (
    disable_real_ordering,
    enable_mock_mode,
    enable_paper_mode,
    enable_real_single_test_mode,
    get_real_order_conditions,
    get_safety_status,
    get_trade_mode,
    load_config,
)
from env_service import (
    check_mock_keys,
    check_real_keys,
    get_masked_env,
    is_env_file_exists,
    save_mock_keys,
    save_real_keys,
)
from trading_service import run_real_order_readiness_check

st.set_page_config(page_title="API 설정", page_icon="API", layout="wide")
st.title("API 설정")
st.caption("한국투자증권 API 키와 거래 모드를 설정합니다.")

if is_env_file_exists():
    st.success(".env 파일이 있습니다.")
    with st.expander("저장된 환경변수 확인"):
        for key, value in get_masked_env().items():
            st.text(f"{key} = {value}")
else:
    st.warning(".env 파일이 없습니다.")

st.divider()
st.subheader("모의투자 API")
for key, value in check_mock_keys().items():
    st.caption(f"{'OK' if value else 'FAIL'} {key}")

with st.form("mock_form"):
    mock_app_key = st.text_input("KIS_APP_KEY 또는 KIS_MOCK_APP_KEY")
    mock_app_secret = st.text_input("KIS_APP_SECRET 또는 KIS_MOCK_APP_SECRET", type="password")
    mock_account_no = st.text_input("KIS_MOCK_ACCOUNT_NO")
    mock_product_code = st.text_input("KIS_MOCK_ACCOUNT_PRODUCT_CODE", value="01")
    if st.form_submit_button("모의투자 API 저장"):
        if not mock_app_key or not mock_app_secret or not mock_account_no:
            st.error("필수 값을 입력하세요.")
        else:
            save_mock_keys(mock_app_key, mock_app_secret, mock_account_no, mock_product_code)
            st.success("모의투자 API 정보를 저장했습니다.")
            st.rerun()

st.divider()
st.subheader("실전투자 API")
st.warning("실전 API 정보 저장만으로 주문이 켜지지는 않습니다. 아래 실전 주문 설정을 별도로 켜야 합니다.")
for key, value in check_real_keys().items():
    st.caption(f"{'OK' if value else 'FAIL'} {key}")

with st.form("real_form"):
    real_app_key = st.text_input("KIS_REAL_APP_KEY")
    real_app_secret = st.text_input("KIS_REAL_APP_SECRET", type="password")
    real_account_no = st.text_input("KIS_ACCOUNT_NO")
    real_product_code = st.text_input("KIS_ACCOUNT_PRODUCT_CODE", value="01")
    if st.form_submit_button("실전투자 API 저장"):
        if not real_app_key or not real_app_secret or not real_account_no:
            st.error("필수 값을 입력하세요.")
        else:
            save_real_keys(real_app_key, real_app_secret, real_account_no, real_product_code)
            st.success("실전투자 API 정보를 저장했습니다.")
            st.rerun()

st.divider()
st.subheader("거래 모드")
cfg = load_config()
current_mode = get_trade_mode(cfg)
status = get_safety_status(cfg)
st.markdown(f"현재 모드: `{current_mode}`")

col_paper, col_mock = st.columns(2)
with col_paper:
    if st.button("PAPER 모드로 전환", use_container_width=True):
        enable_paper_mode()
        st.success("PAPER 모드로 전환했습니다.")
        st.rerun()
with col_mock:
    if st.button("MOCK 모드로 전환", use_container_width=True):
        enable_mock_mode()
        st.success("MOCK 모드로 전환했습니다.")
        st.rerun()

for key, label in [
    ("live_trade", "live_trade"),
    ("paper_trade", "paper_trade"),
    ("use_mock", "kis.use_mock"),
    ("confirm_live_trade", "safety.confirm_live_trade"),
    ("allow_real_test_order", "safety.allow_real_test_order"),
    ("force_trade_allow_real", "force_trade.allow_real_test_order"),
]:
    value = status.get(key, False)
    st.caption(f"{'OK' if value else 'FAIL'} {label}: {value}")

st.divider()
st.subheader("실전 주문 설정")
st.error("실전 주문은 실제 자금으로 실행됩니다. 이 프로그램은 수익을 보장하지 않으며 +2% 익절 목표 전략만 제공합니다.")
st.info("기본값은 실전 주문 차단입니다. 아래 5개 확인을 모두 체크해야 개별 종목 1주 테스트 모드만 켤 수 있습니다.")

real_conditions = get_real_order_conditions()
for key, value in real_conditions.items():
    st.caption(f"{'OK' if value else 'FAIL'} {key}")

ack1 = st.checkbox("실전 주문 위험을 이해했습니다.", key="real_ack_1")
ack2 = st.checkbox("이 프로그램은 수익을 보장하지 않음을 이해했습니다.", key="real_ack_2")
ack3 = st.checkbox("실전 주문은 실제 자금으로 실행됨을 이해했습니다.", key="real_ack_3")
ack4 = st.checkbox("우선 1주 또는 소액 테스트만 진행합니다.", key="real_ack_4")
ack5 = st.checkbox("실전 주문 결과에 대한 책임은 본인에게 있음을 이해했습니다.", key="real_ack_5")
all_real_ack = all([ack1, ack2, ack3, ack4, ack5])

col_a, col_b, col_c = st.columns(3)
with col_a:
    if st.button("실전 개별 주문 테스트 모드 켜기", disabled=not all_real_ack, use_container_width=True):
        enable_real_single_test_mode()
        st.success("실전 개별 주문 테스트 모드를 켰습니다. 전체 리스트 REAL 매수는 계속 비활성화됩니다.")
        st.rerun()
with col_b:
    if st.button("실전 주문 전체 끄기", use_container_width=True):
        disable_real_ordering()
        st.success("실전 주문 설정을 모두 껐습니다.")
        st.rerun()
with col_c:
    if st.button("실전 준비상태 점검 실행", use_container_width=True):
        with st.spinner("실전 준비상태 점검 중..."):
            readiness = run_real_order_readiness_check()
        st.json(readiness)

with st.expander("고급 옵션: 실전 전체 매수 허용 상태"):
    cfg_now = load_config()
    st.caption(f"real_trade.allow_bulk_buy = {cfg_now.get('real_trade', {}).get('allow_bulk_buy', False)}")
    st.caption(f"force_trade.allow_real_bulk_order = {cfg_now.get('force_trade', {}).get('allow_real_bulk_order', False)}")
    st.warning("실전 전체 리스트 매수는 기본 비활성화입니다. 먼저 개별 종목 1주 테스트를 완료하세요.")
