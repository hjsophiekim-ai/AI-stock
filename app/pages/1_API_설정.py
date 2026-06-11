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
from trading_service import (
    run_real_order_readiness_check,
    refresh_kis_token,
    check_kis_connection,
    check_kis_account,
    check_orderable_cash,
    run_kis_readiness,
    get_kis_token_status,
)

st.set_page_config(page_title="API 설정", page_icon="API", layout="wide")
st.title("API 설정")
st.caption("한국투자증권 API 키와 거래 모드를 설정합니다.")

# ══════════════════════════════════════════════════════════════════════════════
# 매일 아침 KIS 연결 점검 (항상 표시 — expander/조건문 없음)
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("매일 아침 KIS 연결 점검")
st.caption("토큰/키/시크릿 원문은 절대 표시되지 않습니다.")
_top_col_m, _top_col_r = st.columns(2)

with _top_col_m:
    st.markdown("**MOCK 모의투자**")
    _tm0, _tm1 = st.columns(2)
    with _tm0:
        if st.button("MOCK 토큰 발급", use_container_width=True, key="api_top_mock_refresh_token"):
            with st.spinner("MOCK 토큰 발급 중..."):
                _r = refresh_kis_token("mock")
            if _r.get("success"):
                st.info(f"발급 완료 | 만료: {_r.get('expires_at_str', '')}")
            else:
                st.error(f"발급 실패: {_r.get('error', '')}")
            st.rerun()
    with _tm1:
        if st.button("MOCK 연결 확인", use_container_width=True, key="api_top_mock_connection"):
            with st.spinner("MOCK 연결 확인 중..."):
                _r = check_kis_connection("mock")
            if _r.get("connection_ok"):
                st.success("MOCK 연결 OK")
            else:
                st.error(f"실패: {_r.get('error', '')}")
    _tm2, _tm3 = st.columns(2)
    with _tm2:
        if st.button("MOCK 계좌조회", use_container_width=True, key="api_top_mock_account"):
            with st.spinner("MOCK 계좌조회 중..."):
                _r = check_kis_account("mock")
            if _r.get("account_ok"):
                st.success(f"OK — {_r.get('broker_count', 0)}개 종목")
            else:
                st.error(f"실패: {_r.get('error', _r.get('response_text', ''))[:100]}")
    with _tm3:
        if st.button("MOCK 주문가능금액", use_container_width=True, key="api_top_mock_cash"):
            with st.spinner("MOCK 주문가능금액 조회 중..."):
                _r = check_orderable_cash("mock")
            if _r.get("orderable_cash_ok"):
                st.success(f"{_r.get('orderable_cash_amount', 0):,}원")
            else:
                st.error(f"실패: {_r.get('error', '')}")
    if st.button("MOCK 전체 준비상태 점검", use_container_width=True, key="api_top_mock_readiness", type="primary"):
        with st.spinner("MOCK 전체 준비상태 점검 중..."):
            _r = run_kis_readiness("mock")
        if _r.get("success"):
            st.success(f"MOCK 준비 완료 — {_r.get('verdict', '')}")
        else:
            st.warning(f"MOCK 준비 미완료 — {_r.get('verdict', '')} | {_r.get('error_message', '')}")

with _top_col_r:
    st.markdown("**REAL 실전투자**")
    _tr0, _tr1 = st.columns(2)
    with _tr0:
        if st.button("REAL 토큰 발급", use_container_width=True, key="api_top_real_refresh_token"):
            with st.spinner("REAL 토큰 발급 중..."):
                _r = refresh_kis_token("real")
            if _r.get("success"):
                st.info(f"발급 완료 | 만료: {_r.get('expires_at_str', '')}")
            else:
                st.error(f"발급 실패: {_r.get('error', '')}")
            st.rerun()
    with _tr1:
        if st.button("REAL 연결 확인", use_container_width=True, key="api_top_real_connection"):
            with st.spinner("REAL 연결 확인 중..."):
                _r = check_kis_connection("real")
            if _r.get("connection_ok"):
                st.success("REAL 연결 OK")
            else:
                st.error(f"실패: {_r.get('error', '')}")
    _tr2, _tr3 = st.columns(2)
    with _tr2:
        if st.button("REAL 계좌조회", use_container_width=True, key="api_top_real_account"):
            with st.spinner("REAL 계좌조회 중..."):
                _r = check_kis_account("real")
            if _r.get("account_ok"):
                st.success(f"OK — {_r.get('broker_count', 0)}개 종목")
            else:
                err = _r.get("response_text", _r.get("error", ""))[:200]
                st.error(f"실패: {err}")
    with _tr3:
        if st.button("REAL 주문가능금액", use_container_width=True, key="api_top_real_cash"):
            with st.spinner("REAL 주문가능금액 조회 중..."):
                _r = check_orderable_cash("real")
            if _r.get("orderable_cash_ok"):
                st.success(f"{_r.get('orderable_cash_amount', 0):,}원")
            else:
                st.error(f"실패: {_r.get('error', '')}")
    if st.button("REAL 전체 준비상태 점검", use_container_width=True, key="api_top_real_readiness", type="primary"):
        with st.spinner("REAL 준비상태 점검 중..."):
            _r = run_kis_readiness("real")
        if _r.get("success"):
            st.success(f"REAL 준비 완료 — {_r.get('verdict', '')}")
        else:
            st.warning(f"REAL 준비 미완료 — {_r.get('error_message', _r.get('verdict', ''))}")

st.divider()

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

real_conditions = get_real_order_conditions()
for key, value in real_conditions.items():
    st.caption(f"{'OK' if value else 'FAIL'} {key}")

# Daily REAL trade confirmation
st.subheader("실전 주문 오늘 확인 (매일 1회 필요)")
st.info("REAL 전체 리스트 매수를 위해 아래 6개를 매일 확인해야 합니다. 앱 재시작 후에도 오늘 날짜로 확인한 경우 유지됩니다.")

# Load current confirmation state
try:
    _CONFIRM_PATH = PROJECT_ROOT / "data" / "real_trade_confirmation.json"
    import json as _json
    _confirm_data = _json.loads(_CONFIRM_PATH.read_text(encoding="utf-8")) if _CONFIRM_PATH.exists() else {}
except Exception:
    _confirm_data = {}

_today_str = __import__("datetime").date.today().strftime("%Y%m%d")
_confirmed_today = _confirm_data.get("confirmation_date") == _today_str and _confirm_data.get("real_trade_confirmed", False)
if _confirmed_today:
    st.success(f"오늘 실전 주문 확인 완료 — {_confirm_data.get('confirmed_at', '')[:19]}")
else:
    st.warning("오늘 아직 실전 주문 확인을 완료하지 않았습니다.")

ack1 = st.checkbox("실전 주문 위험을 이해했습니다.", key="real_ack_1", value=bool(_confirm_data.get("confirm_live_trade")))
ack2 = st.checkbox("이 프로그램은 수익을 보장하지 않음을 이해했습니다.", key="real_ack_2", value=bool(_confirm_data.get("understand_no_profit_guarantee")))
ack3 = st.checkbox("실전 주문은 실제 자금으로 실행됨을 이해했습니다.", key="real_ack_3", value=bool(_confirm_data.get("understand_real_money")))
ack4 = st.checkbox("주문이 미체결될 수 있음을 이해했습니다.", key="real_ack_4", value=bool(_confirm_data.get("understand_order_may_be_unfilled")))
ack5 = st.checkbox("실전 주문 결과에 대한 책임은 본인에게 있음을 이해했습니다.", key="real_ack_5", value=bool(_confirm_data.get("understand_user_responsibility")))
ack6 = st.checkbox("REAL 전체 리스트 매수를 허용합니다.", key="real_ack_6", value=bool(_confirm_data.get("allow_real_bulk_order")))
all_real_ack = all([ack1, ack2, ack3, ack4, ack5, ack6])

col_a, col_b, col_c = st.columns(3)
with col_a:
    if st.button("실전 개별 주문 테스트 모드 켜기", disabled=not all([ack1, ack2, ack3, ack4, ack5]), use_container_width=True):
        enable_real_single_test_mode()
        st.success("실전 개별 주문 테스트 모드를 켰습니다.")
        st.rerun()
with col_b:
    if st.button("오늘 실전 주문 확인 저장", disabled=not all_real_ack, use_container_width=True, type="primary"):
        try:
            from real_trade_confirmation import save_confirmation
            result_save = save_confirmation({
                "confirm_live_trade": ack1,
                "understand_no_profit_guarantee": ack2,
                "understand_real_money": ack3,
                "understand_order_may_be_unfilled": ack4,
                "understand_user_responsibility": ack5,
                "allow_real_bulk_order": ack6,
            })
            st.success(f"실전 주문 확인이 저장되었습니다. 오늘({_today_str}) REAL 전체 리스트 매수가 가능합니다.")
            st.rerun()
        except Exception as _e:
            st.error(f"확인 저장 실패: {_e}")
with col_c:
    if st.button("실전 주문 전체 끄기", use_container_width=True):
        disable_real_ordering()
        try:
            from real_trade_confirmation import clear_confirmation
            clear_confirmation()
        except Exception:
            pass
        st.success("실전 주문 설정을 모두 껐습니다.")
        st.rerun()

_rc, _rd = st.columns(2)
with _rc:
    if st.button("실전 준비상태 점검 실행", use_container_width=True):
        with st.spinner("실전 준비상태 점검 중..."):
            readiness = run_real_order_readiness_check()
        st.json(readiness)
with _rd:
    if st.button("오늘 확인 초기화", use_container_width=True):
        try:
            from real_trade_confirmation import clear_confirmation
            clear_confirmation()
            st.success("오늘 확인을 초기화했습니다. 다시 확인 후 저장하세요.")
            st.rerun()
        except Exception as _e:
            st.error(f"초기화 실패: {_e}")

with st.expander("고급 옵션: 실전 전체 매수 허용 상태"):
    cfg_now = load_config()
    st.caption(f"real_trade.allow_bulk_buy_after_api_confirmation = {cfg_now.get('real_trade', {}).get('allow_bulk_buy_after_api_confirmation', False)}")
    st.caption(f"real_trade.max_real_bulk_order_amount = {cfg_now.get('real_trade', {}).get('max_real_bulk_order_amount', 300000):,}원")
    st.caption(f"오늘 확인 완료: {_confirmed_today}")
    if _confirmed_today:
        st.success("REAL 전체 리스트 매수 조건 충족 가능 상태입니다. 예산배분 및 주문 화면에서 나머지 조건을 확인하세요.")
    else:
        st.warning("오늘 실전 주문 확인을 먼저 완료하세요.")

# (KIS 토큰/연결 버튼은 페이지 최상단 및 'KIS 연결상태' 전용 페이지를 사용하세요)
