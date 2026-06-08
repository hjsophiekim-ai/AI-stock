"""AI 후보 리스트 페이지 — top100 지원."""

import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "components"))

import streamlit as st
import pandas as pd
from config_service import load_config, get_trade_mode
from prediction_service import (
    load_top20, load_force_candidates, run_script,
    run_force_trade_selector, run_no_trade_analysis,
    get_today_str, get_top20_path, get_force_candidates_path,
)
from tables import render_candidates_table
from warning_box import force_trade_disclaimer, no_profit_guarantee_notice

st.set_page_config(page_title="AI 후보 리스트", page_icon="📋", layout="wide")
st.title("📋 AI 후보 리스트")
st.caption("+2% 익절 목표 전략 기반 AI 예측 후보 종목 (top100)")

today_str = get_today_str()
st.caption(f"오늘 날짜: {today_str}")

predictions_dir = PROJECT_ROOT / "reports" / "predictions"


def load_top_n(n: int, date_str: str) -> pd.DataFrame | None:
    path = predictions_dir / f"top{n}_{date_str}.csv"
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:
            return None
    return None


# ── 파일 존재 여부 ──────────────────────────────────────────────
top100_path = predictions_dir / f"top100_{today_str}.csv"
top20_path = predictions_dir / f"top20_{today_str}.csv"
force_path = PROJECT_ROOT / "reports" / f"force_trade_candidates_{today_str}.csv"
model_path = PROJECT_ROOT / "models" / "model.joblib"
daily_path = PROJECT_ROOT / "data" / "raw" / "daily_prices.csv"

col1, col2, col3, col4 = st.columns(4)
col1.metric("3년치 데이터", "있음 ✅" if daily_path.exists() else "없음 ❌")
col2.metric("학습 모델", "있음 ✅" if model_path.exists() else "없음 ❌")
col3.metric("Top100 파일", "있음 ✅" if top100_path.exists() else "없음 ❌")
col4.metric("force_trade 후보", "있음 ✅" if force_path.exists() else "없음 ❌")

st.divider()

# ── 데이터 파이프라인 실행 버튼 ─────────────────────────────────
st.subheader("데이터 파이프라인 실행")

st.caption("순서대로 실행하세요: 데이터 수집 → 피처 생성 → 라벨 생성 → 모델학습 → 예측 → Top100")

row1 = st.columns(4)
pipeline_steps = [
    ("1. 데이터 수집", "collect_daily_data.py", ["--years", "3", "--limit", "200"]),
    ("2. 피처 생성", "make_features.py", []),
    ("3. 라벨 생성", "make_labels.py", []),
    ("4. 모델 학습", "train_model.py", []),
]

for i, (label, script, extra_args) in enumerate(pipeline_steps):
    with row1[i]:
        if st.button(label, use_container_width=True):
            with st.spinner(f"{label} 실행 중..."):
                r = run_script(script, timeout=1800)
            if r["success"]:
                st.success(f"{label} 완료")
            else:
                st.error(f"실패: {r.get('message','')}")
                if "stderr" in r:
                    st.text_area("오류", r["stderr"], height=80)

row2 = st.columns(4)
row2_steps = [
    ("5. 예측 생성", "predict_candidates.py"),
    ("6. Top100 생성", "select_top_candidates.py"),
    ("7. force_trade 후보", None),
    ("8. 전체 파이프라인", None),
]

with row2[0]:
    if st.button("5. 예측 생성", use_container_width=True):
        with st.spinner("예측 생성 중..."):
            r = run_script("predict_candidates.py", timeout=300)
        if r["success"]:
            st.success("예측 완료")
        else:
            st.error(f"실패: {r.get('message','')}")

with row2[1]:
    if st.button("6. Top100 생성", use_container_width=True):
        with st.spinner("Top100 생성 중..."):
            r = run_script("select_top_candidates.py", timeout=120)
        if r["success"]:
            st.success("Top100 생성 완료")
            st.rerun()
        else:
            st.error(f"실패: {r.get('message','')}")

with row2[2]:
    if st.button("7. force_trade 후보", use_container_width=True):
        with st.spinner("force_trade 후보 생성 중..."):
            r = run_force_trade_selector(today_str, min_candidates=1)
        if r["success"]:
            st.success(f"후보 {r.get('count',0)}개 생성")
            st.rerun()
        else:
            st.error(r.get("message", "오류"))

with row2[3]:
    if st.button("전체 파이프라인 실행", use_container_width=True, type="primary"):
        with st.spinner("전체 파이프라인 실행 중 (시간이 걸립니다)..."):
            r = run_script("run_ai_prediction_pipeline.py", timeout=3600)
        if r["success"]:
            st.success("파이프라인 완료!")
            st.rerun()
        else:
            st.error(f"실패: {r.get('message','')}")

st.divider()

# ── Top100 리스트 ────────────────────────────────────────────────
st.subheader(f"오늘의 AI Top100 후보 ({today_str})")
df_top100 = load_top_n(100, today_str)

if df_top100 is not None and not df_top100.empty:
    code_col = "stock_code" if "stock_code" in df_top100.columns else "ticker"
    name_col = "stock_name" if "stock_name" in df_top100.columns else "name"
    prob_col = next((c for c in ("probability_2pct", "proba_up") if c in df_top100.columns), None)

    col_a, col_b = st.columns([2, 1])
    with col_a:
        sort_options = [c for c in ["probability_2pct", "trading_value", "volume_ratio_20"] if c in df_top100.columns]
        if sort_options:
            sort_col = st.selectbox("정렬 기준", sort_options)
            df_top100 = df_top100.sort_values(sort_col, ascending=False)
    with col_b:
        show_n = st.selectbox("표시 개수", [20, 50, 100], index=2)
        df_top100 = df_top100.head(show_n)

    st.caption("⚠ 이 목록은 모델 기반 후보군입니다. 투자 추천이 아니며 수익을 보장하지 않습니다.")
    render_candidates_table(df_top100)
    csv = df_top100.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(f"Top{show_n} CSV 다운로드", csv, f"top{show_n}_{today_str}.csv", "text/csv")

else:
    st.info("오늘의 Top100 파일이 없습니다. 위의 '6. Top100 생성' 또는 '전체 파이프라인 실행'을 클릭하세요.")

    # Top20 fallback
    df_top20 = load_top20(today_str)
    if df_top20 is not None and not df_top20.empty:
        st.subheader("Top20 후보 (Top100 미생성 시 대체)")
        render_candidates_table(df_top20)

    if st.button("거래 0건 원인 분석"):
        with st.spinner("분석 중..."):
            r = run_no_trade_analysis()
        if r["success"]:
            st.success("분석 완료")
            st.json(r.get("data", {}))
        else:
            st.error(r.get("message", "분석 오류"))

st.divider()

# ── force_trade 후보 ────────────────────────────────────────────
st.subheader("force_trade 후보 (필터 완화 적용)")
force_trade_disclaimer()

df_force = load_force_candidates(today_str)
if df_force is not None and not df_force.empty:
    render_candidates_table(df_force)
    csv2 = df_force.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(
        "force_trade 후보 CSV",
        csv2,
        f"force_trade_candidates_{today_str}.csv",
        "text/csv",
    )
else:
    st.info("force_trade 후보 파일이 없습니다. '7. force_trade 후보' 버튼을 클릭하세요.")

no_profit_guarantee_notice()
