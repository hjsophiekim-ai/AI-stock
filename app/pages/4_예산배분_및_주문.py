"""예산배분 및 주문 페이지 — top100 기반."""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "components"))

import streamlit as st
import pandas as pd
from config_service import load_config, get_trade_mode, get_safety_status
from trading_service import (run_budget_allocation, run_paper_order,
                               run_buy_candidates, check_real_order_conditions)
from prediction_service import load_top20, get_today_str
from tables import render_order_results_table
from warning_box import real_order_warning, no_profit_guarantee_notice
from mode_badge import render_mode_badge, render_mode_warning


def render_budget_result_metrics(result: dict) -> None:
    if not isinstance(result, dict):
        return
    labels = [
        ("입력 예산", "input_budget"),
        ("실제 주문가능금액", "orderable_cash"),
        ("실제 사용 가능 예산", "effective_budget"),
        ("예상 사용금액", "expected_order_amount"),
        ("남은 금액", "remaining_budget"),
    ]
    if not any(key in result for _, key in labels):
        return
    cols = st.columns(len(labels))
    for col, (label, key) in zip(cols, labels):
        value = int(float(result.get(key, 0) or 0))
        col.metric(label, f"{value:,}원")

st.set_page_config(page_title="예산배분 및 주문", page_icon="💰", layout="wide")
st.title("💰 예산배분 및 주문")
st.caption("+2% 익절 목표 전략 — 수익을 보장하지 않습니다.")

cfg = load_config()
mode = get_trade_mode(cfg)
status = get_safety_status(cfg)

render_mode_badge(mode)
render_mode_warning(mode)
st.write("")

today_str = get_today_str()
predictions_dir = PROJECT_ROOT / "reports" / "predictions"


def load_top_n_candidates(date_str: str) -> pd.DataFrame | None:
    """top100 → top50 → top20 순서로 후보 파일 로드."""
    for n in [100, 50, 20]:
        path = predictions_dir / f"top{n}_{date_str}.csv"
        if path.exists():
            try:
                df = pd.read_csv(path)
                if not df.empty:
                    return df, n
            except Exception:
                pass
    return None, 0


df_candidates, loaded_n = load_top_n_candidates(today_str)
_enriched_candidates_path = PROJECT_ROOT / "reports" / f"enriched_candidates_{today_str}.csv"
if _enriched_candidates_path.exists():
    try:
        _enriched_df = pd.read_csv(_enriched_candidates_path)
        if not _enriched_df.empty:
            df_candidates, loaded_n = _enriched_df, 100
    except Exception:
        pass

if df_candidates is None or df_candidates.empty:
    st.warning(
        "오늘의 후보 파일(top100/top50/top20)이 없습니다. "
        "3번 'AI 후보 리스트' 화면에서 먼저 생성하세요."
    )
    st.stop()

st.success(f"Top{loaded_n} 후보 파일 로드됨 ({len(df_candidates)}개 종목)")

# ── 거래전략 선택 ────────────────────────────────────────────────
st.subheader("거래전략 선택")
try:
    from strategy_config import get_strategy_display_labels, label_to_strategy_id
    _strategy_labels = get_strategy_display_labels()
except Exception:
    _strategy_labels = [
        "장초반 매매 — 9시30분경 매수, +2% 익절 목표",
        "종가 매매 — 오후 3시경 매수, 시간외·프리마켓·다음날 장중 +2% 익절 목표",
    ]

_strategy_label = st.radio("전략", _strategy_labels, horizontal=True, index=0)
try:
    _strategy_id = label_to_strategy_id(_strategy_label)
except Exception:
    _strategy_id = "morning_0930" if "장초반" in _strategy_label else "afternoon_1500"
st.caption(f"선택된 전략 ID: `{_strategy_id}`")

# ── 예산 입력 ────────────────────────────────────────────────────
st.subheader("예산 설정")
col1, col2, col3 = st.columns(3)
with col1:
    budget = st.number_input(
        "총 예산 (원)", min_value=10_000, max_value=100_000_000,
        value=300_000, step=10_000,
    )
with col2:
    min_orders = st.number_input("최소 주문 건수", min_value=1, max_value=100, value=1)
with col3:
    max_orders = st.number_input("최대 주문 건수", min_value=1, max_value=100, value=min(loaded_n, 100))

st.caption(f"현재 후보: {len(df_candidates)}개 | 예산 {budget:,}원으로 최대 {max_orders}종목 주문")

# ── 후보 목록 미리보기 ────────────────────────────────────────────
with st.expander(f"후보 목록 미리보기 (상위 20개)", expanded=False):
    code_col = "stock_code" if "stock_code" in df_candidates.columns else "ticker"
    name_col = "stock_name" if "stock_name" in df_candidates.columns else "name"
    prob_col = next((c for c in ("probability_2pct", "proba_up") if c in df_candidates.columns), None)
    show_cols = [c for c in [code_col, name_col, "close", prob_col, "trading_value"] if c]
    st.dataframe(df_candidates[show_cols].head(20), use_container_width=True)

st.divider()

# ── 예산 배분 실행 ───────────────────────────────────────────────
if st.button("예산배분 계산", type="primary"):
    with st.spinner("예산배분 계산 중..."):
        result = run_budget_allocation(int(budget), today_str)
    if result["success"]:
        st.success("예산배분 완료")
        data = result["data"]
        summary = result.get("summary", {})

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("총 예산", f"{budget:,}원")
        col_m2.metric("예상 주문 건수", f"{summary.get('allocated_count', 0)}건")
        col_m3.metric("총 주문금액", f"{summary.get('total_order_amount', 0):,.0f}원")

        if hasattr(data, "to_dataframe"):
            alloc_df = data.to_dataframe()
        else:
            alloc_df = pd.DataFrame()

        if not alloc_df.empty:
            st.dataframe(alloc_df, use_container_width=True)
            st.session_state["allocation"] = alloc_df
        if summary:
            st.json(summary)
    else:
        st.error(f"예산배분 실패: {result.get('message','')}")

st.divider()

# ── 전략 매수 실행 (후보 리스트 전부/선택) ─────────────────────────
st.subheader("전략 매수 실행")
st.caption(f"선택 전략: **{_strategy_label}** | +2% 익절 목표 전략 — 수익을 보장하지 않습니다.")

_cand_file = next(
    (str(PROJECT_ROOT / "reports" / "predictions" / f"top{n}_{today_str}.csv")
     for n in [100, 50, 20]
     if (PROJECT_ROOT / "reports" / "predictions" / f"top{n}_{today_str}.csv").exists()),
    None,
)
if _enriched_candidates_path.exists():
    _cand_file = str(_enriched_candidates_path)

_order_mode = st.radio("주문 모드", ["MOCK", "PAPER", "REAL"], index=0, horizontal=True, key="strategy_order_mode")
if _order_mode == "REAL":
    real_order_warning()

_b_col1, _b_col2, _b_col3 = st.columns(3)
with _b_col1:
    if st.button("현재 리스트 전부 매수", type="primary", use_container_width=True):
        if not _cand_file:
            st.error("후보 파일이 없습니다.")
        else:
            with st.spinner(f"{_order_mode} 전략 매수 실행 중..."):
                r = run_buy_candidates(
                    candidate_file=_cand_file, budget=int(budget),
                    mode=_order_mode.lower(), strategy_id=_strategy_id,
                    max_orders=int(max_orders),
                )
            if not isinstance(r, dict):
                st.error("매수 반환값 오류")
            elif r.get("success"):
                st.success(r.get("message", "매수 완료"))
            else:
                st.error(f"매수 실패: {r.get('message','')}")
            if isinstance(r, dict) and r.get("allocation_preview"):
                st.dataframe(pd.DataFrame(r["allocation_preview"]), use_container_width=True)

with _b_col2:
    if st.button("주문 미리보기", use_container_width=True):
        if not _cand_file:
            st.error("후보 파일이 없습니다.")
        else:
            with st.spinner("미리보기 생성 중..."):
                r = run_buy_candidates(
                    candidate_file=_cand_file, budget=int(budget),
                    mode=_order_mode.lower(), strategy_id=_strategy_id,
                    max_orders=int(max_orders), preview_only=True,
                )
            if isinstance(r, dict) and r.get("allocation_preview"):
                st.success(f"미리보기 {len(r['allocation_preview'])}건 (실제 주문 없음)")
                st.dataframe(pd.DataFrame(r["allocation_preview"]), use_container_width=True)
            else:
                st.warning((r or {}).get("message", "미리보기 없음"))

st.divider()

# ── 주문 실행 ────────────────────────────────────────────────────
st.subheader("개별 종목 주문 테스트")
order_mode = st.radio("주문 모드", ["PAPER", "MOCK", "REAL"], index=0, horizontal=True, key="single_order_mode")

if order_mode == "REAL":
    real_order_warning()
    cond_result = check_real_order_conditions()
    cond = cond_result.get("conditions", {})
    all_ok = all(cond.values())
    for k, v in cond.items():
        st.caption(f"{'✅' if v else '❌'} {k}")
    if not all_ok:
        st.error("실전 주문 조건이 충족되지 않았습니다.")

# ── 개별 종목 주문 테스트 ─────────────────────────────────────────
st.subheader("개별 종목 주문 테스트")
with st.form("order_form"):
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        order_code = st.text_input("종목코드", value="005930", max_chars=6)
    with col_b:
        order_name = st.text_input("종목명", value="삼성전자")
    with col_c:
        order_price = st.number_input("현재가 (원)", min_value=1, value=70_000)
    order_amount = st.number_input("주문금액 (원)", min_value=10_000, value=min(int(budget), 100_000))
    submitted = st.form_submit_button(
        f"{order_mode} 주문 실행",
        type="primary" if order_mode == "PAPER" else "secondary",
    )

if submitted:
    if order_mode == "REAL" and not check_real_order_conditions().get("success"):
        st.error("실전 주문 조건 미충족. 주문이 실행되지 않았습니다.")
    else:
        with st.spinner("주문 실행 중..."):
            if order_mode == "PAPER":
                r = run_paper_order(order_code, order_name, order_amount, order_price)
            else:
                st.warning("MOCK/REAL 주문은 실제 API 연결 후 가능합니다. 현재는 PAPER 모드로 실행합니다.")
                r = run_paper_order(order_code, order_name, order_amount, order_price)
        if r.get("success"):
            st.success(f"주문 성공: {r.get('order_no','')}")
        else:
            st.error(f"주문 실패: {r.get('reason','')}")
        st.json(r)

st.divider()
no_profit_guarantee_notice()
