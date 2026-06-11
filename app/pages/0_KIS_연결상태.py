"""KIS 토큰 및 연결 상태 전용 페이지 — MOCK/REAL 분리, 항상 표시."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))

import streamlit as st

from trading_service import (
    check_kis_account,
    check_kis_connection,
    check_orderable_cash,
    get_kis_token_status,
    refresh_kis_token,
    run_kis_readiness,
)

st.set_page_config(page_title="KIS 연결상태", page_icon="🔌", layout="wide")
st.title("KIS 연결상태")
st.caption("MOCK/REAL 토큰 발급, 연결 확인, 계좌조회, 주문가능금액, 전체 준비상태 점검.")
st.caption("API 키 · 시크릿 · 토큰 원문은 절대 표시되지 않습니다. 마스킹된 정보만 표시됩니다.")

_tab_mock, _tab_real = st.tabs(["MOCK 모의투자", "REAL 실전투자"])

# ── MOCK 탭 ──────────────────────────────────────────────────────────────────
with _tab_mock:
    st.subheader("MOCK 모의투자 토큰 / 연결 상태")

    _mts = get_kis_token_status("mock")
    _mc0, _mc1, _mc2, _mc3 = st.columns(4)
    with _mc0:
        _key_ok = bool(_mts.get("app_key_masked") and _mts.get("app_key_masked") != "MISSING")
        st.metric("App Key", "✅ 설정됨" if _key_ok else "❌ 없음")
        st.caption(_mts.get("app_key_masked", ""))
    with _mc1:
        st.metric("계좌번호", _mts.get("account_masked", "") or "❌ 없음")
    with _mc2:
        _c = _mts.get("cache_exists", False)
        _e = _mts.get("is_expired", True)
        if _c and not _e:
            _rem = _mts.get("remaining_seconds", 0)
            st.metric("토큰 상태", "✅ 유효")
            st.caption(f"남은 시간: {_rem // 3600}h {(_rem % 3600) // 60}m")
        elif _c and _e:
            st.metric("토큰 상태", "⚠️ 만료")
            st.caption(_mts.get("expires_at_str", ""))
        else:
            st.metric("토큰 상태", "❌ 없음")
    with _mc3:
        st.metric("캐시 파일", _mts.get("token_cache_file", ""))

    st.divider()
    _mb0, _mb1, _mb2, _mb3, _mb4 = st.columns(5)
    with _mb0:
        if st.button("MOCK 토큰 새로 발급", use_container_width=True, key="kis_conn_mock_refresh_token"):
            with st.spinner("MOCK 토큰 발급 중..."):
                _r = refresh_kis_token("mock")
            if _r.get("success"):
                st.info(f"발급 완료 | 만료: {_r.get('expires_at_str', '')}")
            else:
                st.error(f"발급 실패: {_r.get('error', '')}")
            st.rerun()
    with _mb1:
        if st.button("MOCK 연결 확인", use_container_width=True, key="kis_conn_mock_connection"):
            with st.spinner("MOCK 연결 확인 중..."):
                _r = check_kis_connection("mock")
            if _r.get("connection_ok"):
                st.success("MOCK 연결 OK")
            else:
                st.error(f"MOCK 연결 실패: {_r.get('error', '')}")
    with _mb2:
        if st.button("MOCK 계좌조회 확인", use_container_width=True, key="kis_conn_mock_account"):
            with st.spinner("MOCK 계좌조회 중..."):
                _r = check_kis_account("mock")
            if _r.get("account_ok"):
                st.success(f"MOCK 계좌조회 OK — {_r.get('broker_count', 0)}개 종목")
            else:
                st.error(f"실패: {_r.get('error', _r.get('response_text', ''))[:200]}")
    with _mb3:
        if st.button("MOCK 주문가능금액 확인", use_container_width=True, key="kis_conn_mock_cash"):
            with st.spinner("MOCK 주문가능금액 조회 중..."):
                _r = check_orderable_cash("mock")
            if _r.get("orderable_cash_ok"):
                st.success(f"MOCK 주문가능금액: {_r.get('orderable_cash_amount', 0):,}원")
            else:
                st.error(f"실패: {_r.get('error', '')}")
    with _mb4:
        if st.button("MOCK 전체 준비상태 점검", use_container_width=True, key="kis_conn_mock_readiness", type="primary"):
            with st.spinner("MOCK 전체 준비상태 점검 중..."):
                _r = run_kis_readiness("mock")
            if _r.get("success"):
                st.success(f"MOCK 준비 완료 — {_r.get('verdict', '')}")
            else:
                st.warning(f"MOCK 준비 미완료 — {_r.get('verdict', '')} | {_r.get('error_message', '')}")
            with st.expander("상세 결과"):
                st.json({k: v for k, v in _r.items() if k not in ("token_status", "response_json")})

# ── REAL 탭 ──────────────────────────────────────────────────────────────────
with _tab_real:
    st.error("REAL은 실제 계좌입니다. 조회는 안전하지만 주문은 실제 자금에 반영됩니다.")
    st.subheader("REAL 실전투자 토큰 / 연결 상태")

    _rts = get_kis_token_status("real")
    _rc0, _rc1, _rc2, _rc3 = st.columns(4)
    with _rc0:
        _rkey_ok = bool(_rts.get("app_key_masked") and _rts.get("app_key_masked") != "MISSING")
        st.metric("App Key", "✅ 설정됨" if _rkey_ok else "❌ 없음")
        st.caption(_rts.get("app_key_masked", ""))
    with _rc1:
        st.metric("계좌번호", _rts.get("account_masked", "") or "❌ 없음")
    with _rc2:
        _rc_exists = _rts.get("cache_exists", False)
        _re = _rts.get("is_expired", True)
        if _rc_exists and not _re:
            _rrem = _rts.get("remaining_seconds", 0)
            st.metric("토큰 상태", "✅ 유효")
            st.caption(f"남은 시간: {_rrem // 3600}h {(_rrem % 3600) // 60}m")
        elif _rc_exists and _re:
            st.metric("토큰 상태", "⚠️ 만료")
            st.caption(_rts.get("expires_at_str", ""))
        else:
            st.metric("토큰 상태", "❌ 없음")
    with _rc3:
        st.metric("캐시 파일", _rts.get("token_cache_file", ""))

    st.divider()
    _rb0, _rb1, _rb2, _rb3, _rb4 = st.columns(5)
    with _rb0:
        if st.button("REAL 토큰 새로 발급", use_container_width=True, key="kis_conn_real_refresh_token"):
            with st.spinner("REAL 토큰 발급 중..."):
                _r = refresh_kis_token("real")
            if _r.get("success"):
                st.info(f"발급 완료 | 만료: {_r.get('expires_at_str', '')}")
            else:
                st.error(f"발급 실패: {_r.get('error', '')}")
            st.rerun()
    with _rb1:
        if st.button("REAL 연결 확인", use_container_width=True, key="kis_conn_real_connection"):
            with st.spinner("REAL 연결 확인 중..."):
                _r = check_kis_connection("real")
            if _r.get("connection_ok"):
                st.success("REAL 연결 OK")
            else:
                st.error(f"REAL 연결 실패: {_r.get('error', '')}")
    with _rb2:
        if st.button("REAL 계좌조회 확인", use_container_width=True, key="kis_conn_real_account"):
            with st.spinner("REAL 계좌조회 중..."):
                _r = check_kis_account("real")
            if _r.get("account_ok"):
                st.success(f"REAL 계좌조회 OK — {_r.get('broker_count', 0)}개 종목")
            else:
                err = _r.get("response_text", _r.get("error", ""))[:300]
                st.error(f"실패: {err}")
    with _rb3:
        if st.button("REAL 주문가능금액 확인", use_container_width=True, key="kis_conn_real_cash"):
            with st.spinner("REAL 주문가능금액 조회 중..."):
                _r = check_orderable_cash("real")
            if _r.get("orderable_cash_ok"):
                st.success(f"REAL 주문가능금액: {_r.get('orderable_cash_amount', 0):,}원")
            else:
                st.error(f"실패: {_r.get('error', '')}")
    with _rb4:
        if st.button("REAL 준비상태 전체 점검", use_container_width=True, key="kis_conn_real_readiness", type="primary"):
            with st.spinner("REAL 준비상태 전체 점검 중..."):
                _r = run_kis_readiness("real")
            if _r.get("success"):
                st.success(f"REAL 준비 완료 — {_r.get('verdict', '')}")
            else:
                st.warning(f"REAL 준비 미완료 — {_r.get('error_message', _r.get('verdict', ''))}")
            with st.expander("상세 결과"):
                _diag = {k: v for k, v in _r.items() if k not in ("token_status", "response_json")}
                st.json(_diag)
