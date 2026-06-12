"""AI Stock 자동매매 대시보드 — 메인 화면.

실행:
    streamlit run app/streamlit_app.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# 프로젝트 경로 설정
PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _p in (
    str(PROJECT_ROOT),
    str(PROJECT_ROOT / "src"),
    str(PROJECT_ROOT / "app" / "services"),
    str(PROJECT_ROOT / "app" / "components"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st

# 앱 시작 시 필수 디렉토리 자동 생성
try:
    _startup_path = PROJECT_ROOT / "app" / "services" / "startup_service.py"
    if _startup_path.exists():
        sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
        from startup_service import ensure_dirs, get_commit_hash
        ensure_dirs()
        _COMMIT_HASH = get_commit_hash()
    else:
        _COMMIT_HASH = os.environ.get("RENDER_GIT_COMMIT", "UNKNOWN")[:8] or "UNKNOWN"
except Exception:
    _COMMIT_HASH = "UNKNOWN"

st.set_page_config(
    page_title="AI Stock 자동매매",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 서비스 임포트
from config_service import load_config, get_trade_mode, get_safety_status
from log_service import is_emergency_stop_active, activate_emergency_stop, deactivate_emergency_stop
from performance_service import get_summary, load_positions_json
from env_service import is_env_file_exists
from prediction_service import get_top20_path, get_today_str


def main():
    # ── 사이드바 ────────────────────────────────────────────────
    with st.sidebar:
        st.title("📈 AI Stock")
        st.caption("한국투자증권 Open API")
        st.caption("+2% 익절 목표 전략")
        st.divider()
        st.page_link("pages/1_API_설정.py", label="1. API 설정", icon="🔑")
        st.page_link("pages/2_API_연결_테스트.py", label="2. API 연결 테스트", icon="🔌")
        st.page_link("pages/3_AI_후보_리스트.py", label="3. AI 후보 리스트", icon="📋")
        st.page_link("pages/4_예산배분_및_주문.py", label="4. 예산배분 및 주문", icon="💰")
        st.page_link("pages/5_보유종목_및_매도감시.py", label="5. 보유종목 및 매도감시", icon="👀")
        st.page_link("pages/6_수익률_분석.py", label="6. 수익률 분석", icon="📊")
        st.page_link("pages/7_백테스트_결과.py", label="7. 백테스트 결과", icon="📉")
        st.page_link("pages/8_로그_및_긴급중단.py", label="8. 로그 및 긴급중단", icon="🚨")
        st.divider()
        st.caption(f"날짜: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        st.caption(f"커밋: `{_COMMIT_HASH}`")
        if os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_URL"):
            st.caption("🌐 Render 배포 환경")

    # ── 헤더 ────────────────────────────────────────────────────
    st.title("📈 AI Stock 자동매매 대시보드")
    st.caption("한국투자증권 Open API 기반 **+2% 익절 목표 전략** | 수익을 보장하지 않습니다.")

    # 상태 로딩
    try:
        cfg = load_config()
        mode = get_trade_mode(cfg)
        status = get_safety_status(cfg)
    except Exception as e:
        st.error(f"config.yaml 로드 오류: {e}")
        return

    es_active = is_emergency_stop_active()
    env_exists = is_env_file_exists()
    today_str = get_today_str()
    top20_exists = get_top20_path(today_str) is not None

    # ── 모드 배지 ──────────────────────────────────────────────
    mode_colors = {"PAPER": "blue", "MOCK": "orange", "REAL": "red"}
    mode_descs = {
        "PAPER": "PAPER — 가상 거래 (실제 주문 없음)",
        "MOCK": "MOCK — 모의투자 API",
        "REAL": "REAL — 실전투자 (실제 자금!)",
    }
    badge_color = mode_colors.get(mode, "gray")
    st.markdown(
        f'<span style="background:{"#dbeafe" if mode=="PAPER" else "#fef3c7" if mode=="MOCK" else "#fee2e2"};'
        f'color:{"#1d4ed8" if mode=="PAPER" else "#92400e" if mode=="MOCK" else "#991b1b"};'
        f'padding:4px 14px;border-radius:12px;font-weight:700;font-size:15px;">'
        f'{mode_descs.get(mode, mode)}</span>',
        unsafe_allow_html=True,
    )
    st.write("")

    if mode == "REAL":
        st.error("⛔ 실전투자 모드 활성 — 모든 주문이 실제 자금으로 체결됩니다!")
    if es_active:
        st.error("🚨 긴급중단(EMERGENCY_STOP) 활성 — 신규 매수가 차단됩니다.")

    # ── 안전장치 상태 ──────────────────────────────────────────
    with st.expander("안전장치 상태", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("live_trade", "ON" if status["live_trade"] else "OFF",
                  delta_color="inverse" if status["live_trade"] else "normal")
        c2.metric("use_mock", "ON" if status["use_mock"] else "OFF")
        c3.metric("confirm_live_trade", "ON" if status["confirm_live_trade"] else "OFF")
        c4.metric(".env 파일", "존재" if env_exists else "없음")

    # ── 오늘 요약 카드 ─────────────────────────────────────────
    try:
        summary = get_summary()
        positions = load_positions_json()
    except Exception:
        summary = {}
        positions = []

    st.subheader("오늘 요약")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("현재 모드", mode)
    col2.metric("보유종목 수", f"{len(positions)}종목")
    col3.metric("오늘 후보 파일", "있음" if top20_exists else "없음")
    col4.metric("긴급중단", "활성" if es_active else "비활성",
                delta_color="inverse" if es_active else "normal")

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("실현손익", f"{summary.get('realized_pnl', 0):+,.0f}원")
    col6.metric("평가손익", f"{summary.get('unrealized_pnl', 0):+,.0f}원")
    col7.metric("누적수익률", f"{summary.get('cumulative_return_pct', 0):+.2f}%")
    col8.metric("승률", f"{summary.get('win_rate', 0):.1f}%")

    st.divider()

    # ── 빠른 실행 버튼 ─────────────────────────────────────────
    st.subheader("빠른 실행")
    b1, b2, b3, b4 = st.columns(4)

    with b1:
        if st.button("🔌 API 연결 테스트", use_container_width=True):
            st.switch_page("pages/2_API_연결_테스트.py")
    with b2:
        if st.button("📋 오늘 후보 불러오기", use_container_width=True):
            st.switch_page("pages/3_AI_후보_리스트.py")
    with b3:
        if st.button("💰 예산배분 및 주문", use_container_width=True):
            st.switch_page("pages/4_예산배분_및_주문.py")
    with b4:
        if st.button("👀 매도감시", use_container_width=True):
            st.switch_page("pages/5_보유종목_및_매도감시.py")

    b5, b6, b7, b8 = st.columns(4)
    with b5:
        if st.button("📊 수익률 분석", use_container_width=True):
            st.switch_page("pages/6_수익률_분석.py")
    with b6:
        if st.button("📉 백테스트 결과", use_container_width=True):
            st.switch_page("pages/7_백테스트_결과.py")
    with b7:
        if not es_active:
            if st.button("🚨 긴급중단 ON", type="primary", use_container_width=True):
                r = activate_emergency_stop()
                if r["success"]:
                    st.success(r["message"])
                    st.rerun()
                else:
                    st.error(r["message"])
        else:
            if st.button("✅ 긴급중단 해제", use_container_width=True):
                r = deactivate_emergency_stop()
                if r["success"]:
                    st.success(r["message"])
                    st.rerun()
                else:
                    st.error(r["message"])
    with b8:
        if st.button("📝 로그 보기", use_container_width=True):
            st.switch_page("pages/8_로그_및_긴급중단.py")

    st.divider()
    st.caption(
        "⚠️ 이 시스템은 +2% 익절 목표 전략을 사용하며, 수익을 보장하지 않습니다. "
        "투자 결과에 대한 책임은 사용자 본인에게 있습니다."
    )


if __name__ == "__main__":
    main()
