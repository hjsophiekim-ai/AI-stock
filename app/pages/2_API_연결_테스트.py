"""API 연결 테스트 페이지."""

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))

import streamlit as st
from config_service import load_config, get_trade_mode, get_safety_status
from env_service import inject_to_os_env, check_mock_keys, check_real_keys
from api_service import test_env_vars, test_token, test_balance, test_orderable_cash, test_current_price
from log_service import read_log

st.set_page_config(page_title="API 연결 테스트", page_icon="🔌", layout="wide")
st.title("🔌 API 연결 테스트")

cfg = load_config()
mode = get_trade_mode(cfg)
status = get_safety_status(cfg)

st.markdown(f"**현재 모드:** `{mode}`")

# ── 환경변수 상태 ───────────────────────────────────────────────
st.subheader("환경변수 확인")
mock_check = check_mock_keys()
real_check = check_real_keys()

col1, col2 = st.columns(2)
with col1:
    st.caption("**모의투자 키**")
    for k, v in mock_check.items():
        st.caption(f"{'✅' if v else '❌'} {k}")
with col2:
    st.caption("**실전투자 키**")
    for k, v in real_check.items():
        st.caption(f"{'✅' if v else '❌'} {k}")

st.divider()

# ── 종목코드 입력 ───────────────────────────────────────────────
stock_code = st.text_input("현재가 조회 종목코드", value="005930", max_chars=6)

# ── 단계별 테스트 ───────────────────────────────────────────────
st.subheader("단계별 테스트")

inject_to_os_env()

tests = [
    ("1. 환경변수 확인", test_env_vars),
    ("2. 접근토큰 발급", test_token),
    ("3. 계좌 잔고 조회", test_balance),
    ("4. 주문가능금액 조회", test_orderable_cash),
    ("5. 현재가 조회", lambda: test_current_price(stock_code)),
]

if st.button("전체 테스트 실행", type="primary"):
    results = []
    for label, fn in tests:
        with st.spinner(f"{label} 진행 중..."):
            try:
                r = fn()
                results.append((label, r))
            except Exception as e:
                results.append((label, {"success": False, "message": str(e)}))
    st.session_state["test_results"] = results

if st.button("초기화"):
    st.session_state.pop("test_results", None)
    st.rerun()

# 개별 테스트 버튼
with st.expander("개별 테스트"):
    cols = st.columns(5)
    individual_results = {}
    for i, (label, fn) in enumerate(tests):
        with cols[i]:
            if st.button(label.split(". ")[1][:6], use_container_width=True):
                try:
                    individual_results[label] = fn()
                except Exception as e:
                    individual_results[label] = {"success": False, "message": str(e)}
    for label, r in individual_results.items():
        icon = "✅" if r.get("success") else "❌"
        st.write(f"{icon} {label}: {r.get('message', '')}")
        if "cash" in r:
            st.write(f"   주문가능금액: {r['cash']:,}원")
        if "price" in r and r["price"]:
            st.write(f"   현재가: {r['price']:,}원")

# 테스트 결과 표시
if "test_results" in st.session_state:
    st.subheader("테스트 결과")
    all_pass = True
    for label, r in st.session_state["test_results"]:
        success = r.get("success", False)
        icon = "✅" if success else "❌"
        if not success:
            all_pass = False
        with st.container():
            st.markdown(f"**{icon} {label}**")
            st.caption(r.get("message", ""))
            if "checks" in r:
                for k, v in r["checks"].items():
                    st.caption(f"  {'✅' if v else '❌'} {k}")
            if "cash" in r and r["cash"]:
                st.caption(f"  주문가능금액: {r['cash']:,}원")
            if "price" in r and r["price"]:
                st.caption(f"  현재가: {r['price']:,}원")
    if all_pass:
        st.success("모든 테스트 통과!")
    else:
        st.error("일부 테스트 실패. API 키와 모드 설정을 확인하세요.")

st.divider()

# ── 로그 ────────────────────────────────────────────────────────
st.subheader("API 로그 (최근 100줄)")
log_text = read_log("api.log", last_n=100)
st.text_area("api.log", value=log_text, height=200)

# ── 보고서 다운로드 ─────────────────────────────────────────────
today_str = datetime.now().strftime("%Y%m%d")
report_path = PROJECT_ROOT / "reports" / f"api_connection_test_{today_str}.txt"
if report_path.exists():
    st.download_button(
        "보고서 다운로드",
        data=report_path.read_text(encoding="utf-8", errors="replace"),
        file_name=report_path.name,
        mime="text/plain",
    )
