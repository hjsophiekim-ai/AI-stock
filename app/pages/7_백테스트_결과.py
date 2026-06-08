"""백테스트 결과 페이지."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))

import streamlit as st
import pandas as pd
from prediction_service import run_script

st.set_page_config(page_title="백테스트 결과", page_icon="📉", layout="wide")
st.title("📉 백테스트 결과")
st.caption("+2% 익절 목표 전략 백테스트 — 과거 성과가 미래를 보장하지 않습니다.")

BACKTESTS_DIR = PROJECT_ROOT / "reports" / "backtests"

# ── 기존 결과 표시 ──────────────────────────────────────────────
summary_file = BACKTESTS_DIR / "backtest_summary.txt"
trades_file = BACKTESTS_DIR / "backtest_trades.csv"

col1, col2 = st.columns(2)
col1.metric("백테스트 요약", "있음 ✅" if summary_file.exists() else "없음 ❌")
col2.metric("거래 내역", "있음 ✅" if trades_file.exists() else "없음 ❌")

if summary_file.exists():
    st.subheader("백테스트 요약")
    st.text(summary_file.read_text(encoding="utf-8", errors="replace"))

st.divider()

# ── 백테스트 실행 ────────────────────────────────────────────────
st.subheader("백테스트 실행")
with st.form("backtest_form"):
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        start_date = st.text_input("시작일", value="2024-01-01")
    with col_b:
        end_date = st.text_input("종료일", value="2024-12-31")
    with col_c:
        top_n = st.number_input("Top N", min_value=1, max_value=50, value=20)
    if st.form_submit_button("백테스트 실행", type="primary"):
        st.info("백테스트는 데이터와 모델이 준비된 경우에만 실행 가능합니다.")
        with st.spinner("백테스트 실행 중 (시간이 걸릴 수 있음)..."):
            r = run_script("backtest.py", timeout=300)
        if r["success"]:
            st.success("백테스트 완료")
            st.rerun()
        else:
            st.error(f"백테스트 실패: {r.get('message','')}")
            if "stderr" in r:
                st.text_area("오류", r["stderr"], height=150)

st.divider()

# ── 거래 내역 ────────────────────────────────────────────────────
st.subheader("백테스트 거래 내역")
if trades_file.exists():
    try:
        df = pd.read_csv(trades_file, encoding="utf-8-sig")
        st.dataframe(df, use_container_width=True)

        # 성과 지표
        if not df.empty:
            col1, col2, col3, col4 = st.columns(4)
            total = len(df)
            col1.metric("총 거래", f"{total}건")
            if "exit_reason" in df.columns:
                tp = (df["exit_reason"].str.contains("take_profit", na=False)).sum()
                col2.metric("+2% 익절", f"{tp}건 ({tp/total*100:.1f}%)" if total else "0건")
            if "pnl_rate" in df.columns:
                wins = (df["pnl_rate"] > 0).sum()
                col3.metric("승률", f"{wins/total*100:.1f}%" if total else "0%")
                col4.metric("평균수익률", f"{df['pnl_rate'].mean()*100:.2f}%" if total else "0%")

            try:
                import plotly.express as px
                if "trade_date" in df.columns and "pnl_rate" in df.columns:
                    fig = px.scatter(df, x="trade_date", y="pnl_rate",
                                     color="exit_reason",
                                     title="백테스트 거래별 수익률",
                                     labels={"trade_date": "날짜", "pnl_rate": "수익률"})
                    st.plotly_chart(fig, use_container_width=True)
            except ImportError:
                pass

        csv = df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button("백테스트 거래 내역 다운로드", csv,
                           "backtest_trades.csv", "text/csv")
    except Exception as e:
        st.error(f"파일 로드 오류: {e}")
else:
    st.info("백테스트 거래 내역 파일이 없습니다.")

st.caption("⚠️ 과거 백테스트 성과는 미래 수익을 보장하지 않습니다.")
