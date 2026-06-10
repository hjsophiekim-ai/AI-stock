"""예산배분 및 주문 화면."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "components"))

from config_service import load_config, get_trade_mode, get_safety_status
from trading_service import (
    check_real_order_conditions,
    run_budget_allocation,
    run_buy_candidates,
    list_sell_policies,
    run_paper_order,
    run_real_order_readiness_check,
    run_real_order_diagnosis,
    run_real_order_verify,
    run_real_single_order_test,
)
from prediction_service import get_today_str
from warning_box import no_profit_guarantee_notice, real_order_warning
from mode_badge import render_mode_badge, render_mode_warning


def _load_candidates(date_str: str) -> tuple[pd.DataFrame | None, int, str | None]:
    predictions_dir = PROJECT_ROOT / "reports" / "predictions"
    enriched_path = PROJECT_ROOT / "reports" / f"enriched_candidates_{date_str}.csv"
    if enriched_path.exists():
        df = pd.read_csv(enriched_path)
        if not df.empty:
            return df, 100, str(enriched_path)

    for n in (100, 50, 20):
        path = predictions_dir / f"top{n}_{date_str}.csv"
        if path.exists():
            df = pd.read_csv(path)
            if not df.empty:
                return df, n, str(path)
    return None, 0, None


def _render_budget_metrics(result: dict) -> None:
    labels = [
        ("입력 예산", "input_budget"),
        ("주문가능금액", "orderable_cash"),
        ("실제 사용 가능 예산", "effective_budget"),
        ("예상 사용금액", "expected_order_amount"),
        ("남은 금액", "remaining_budget"),
    ]
    cols = st.columns(len(labels))
    for col, (label, key) in zip(cols, labels):
        value = int(float(result.get(key, 0) or 0))
        col.metric(label, f"{value:,}원")


def _render_real_conditions() -> bool:
    result = check_real_order_conditions()
    conditions = result.get("conditions", {})
    st.write("실전 주문 안전조건")
    for key, value in conditions.items():
        st.caption(f"{'OK' if value else 'FAIL'} - {key}")
    if not result.get("success"):
        st.error("실전 주문 조건이 충족되지 않아 주문 버튼이 차단됩니다.")
    return bool(result.get("success"))


st.set_page_config(page_title="예산배분 및 주문", page_icon="💸", layout="wide")
st.title("예산배분 및 주문")
st.caption("+2% 익절 목표 전략입니다. 수익을 보장하지 않습니다.")

cfg = load_config()
mode = get_trade_mode(cfg)
status = get_safety_status(cfg)
render_mode_badge(mode)
render_mode_warning(mode)

today_str = get_today_str()
df_candidates, loaded_n, candidate_file = _load_candidates(today_str)

if df_candidates is None or df_candidates.empty:
    st.warning("오늘 후보 파일이 없습니다. 먼저 AI 후보 리스트 화면에서 후보를 생성하세요.")
    st.stop()

st.success(f"Top{loaded_n} 후보 파일 로드 완료: {len(df_candidates)}개 종목")

st.subheader("거래전략 선택")
try:
    from strategy_config import get_strategy_display_labels, label_to_strategy_id

    strategy_labels = get_strategy_display_labels()
except Exception:
    strategy_labels = [
        "장초반 매매 — 9시30분경 매수, +2% 익절 목표",
        "종가 매매 — 오후 3시경 매수, +2% 익절 목표",
    ]

strategy_label = st.radio("전략", strategy_labels, horizontal=True, index=0)
try:
    strategy_id = label_to_strategy_id(strategy_label)
except Exception:
    strategy_id = "morning_0930" if "장초반" in strategy_label else "afternoon_1500"
st.caption(f"선택한 전략 ID: `{strategy_id}`")

st.subheader("매도방식 선택")
sell_policies = list_sell_policies()
policy_labels = {
    p["name"]: p["id"]
    for p in sell_policies
}
policy_descriptions = {p["id"]: p.get("description", "") for p in sell_policies}
selected_policy_label = st.radio("매도방식", list(policy_labels.keys()), horizontal=True, index=0)
sell_policy_id = policy_labels[selected_policy_label]
st.caption(policy_descriptions.get(sell_policy_id, ""))
if sell_policy_id == "manual_hold":
    st.warning("수동매도 전까지 보유를 선택하면 +2%에 도달해도 자동매도되지 않습니다.")

st.subheader("예산 설정")
col1, col2, col3 = st.columns(3)
with col1:
    budget = st.number_input("총 예산 (원)", min_value=10_000, max_value=100_000_000, value=300_000, step=10_000)
with col2:
    min_orders = st.number_input("최소 주문 건수", min_value=1, max_value=100, value=1)
with col3:
    max_orders = st.number_input("최대 주문 건수", min_value=1, max_value=100, value=min(loaded_n, 100))

st.caption(f"현재 후보 {len(df_candidates)}개, 예산 {int(budget):,}원, 최대 {int(max_orders)}건 주문")

with st.expander("후보 목록 미리보기", expanded=False):
    code_col = "stock_code" if "stock_code" in df_candidates.columns else "ticker"
    name_col = "stock_name" if "stock_name" in df_candidates.columns else "name"
    show_cols = [
        c
        for c in [code_col, name_col, "current_price", "close", "buy_allowed", "disclosure_summary", "final_score"]
        if c in df_candidates.columns
    ]
    st.dataframe(df_candidates[show_cols].head(30), use_container_width=True)

st.divider()
st.subheader("예산배분 계산")
if st.button("예산배분 계산", type="primary"):
    with st.spinner("예산배분 계산 중..."):
        result = run_budget_allocation(int(budget), today_str)
    if result.get("success"):
        st.success("예산배분 완료")
        summary = result.get("summary", {})
        _render_budget_metrics(
            {
                "input_budget": int(budget),
                "expected_order_amount": summary.get("total_order_amount", 0),
                "remaining_budget": int(budget) - int(summary.get("total_order_amount", 0) or 0),
            }
        )
        data = result.get("data")
        alloc_df = data.to_dataframe() if hasattr(data, "to_dataframe") else pd.DataFrame()
        if not alloc_df.empty:
            st.dataframe(alloc_df, use_container_width=True)
            st.session_state["allocation"] = alloc_df
        if summary:
            st.json(summary)
    else:
        st.error(f"예산배분 실패: {result.get('message', '')}")

st.divider()
st.subheader("전략 매수 실행")
st.caption("수동 실행 모드: 현재 시간이 전략 매수시간이 아니어도 MOCK/PAPER 주문은 실행됩니다. REAL 전체 리스트 매수는 기본 차단됩니다.")

order_mode = st.radio("주문 모드", ["MOCK", "PAPER", "REAL"], index=0, horizontal=True, key="strategy_order_mode")
if order_mode == "REAL":
    real_order_warning()
    real_bulk_ok = (
        bool(cfg.get("real_trade", {}).get("allow_bulk_buy"))
        and bool(cfg.get("force_trade", {}).get("allow_real_bulk_order"))
        and bool(cfg.get("safety", {}).get("confirm_live_trade"))
    )
    if not real_bulk_ok:
        st.error("실전 전체 리스트 매수는 비활성화되어 있습니다. 먼저 개별 종목 1주 테스트를 완료하세요.")
else:
    real_bulk_ok = True
    if order_mode == "MOCK":
        st.info("MOCK 주문은 모의투자 서버 openapivts와 KIS_MOCK_APP_KEY만 사용해야 합니다.")

col_buy, col_preview = st.columns(2)
with col_buy:
    if st.button("현재 리스트 전부 매수", type="primary", use_container_width=True, disabled=(order_mode == "REAL" and not real_bulk_ok)):
        if not candidate_file:
            st.error("후보 파일이 없습니다.")
        else:
            with st.spinner(f"{order_mode} 전략 매수 실행 중..."):
                result = run_buy_candidates(
                    candidate_file=candidate_file,
                    budget=int(budget),
                    mode=order_mode.lower(),
                    strategy_id=strategy_id,
                    max_orders=int(max_orders),
                    sell_policy_id=sell_policy_id,
                )
            if result.get("success"):
                st.success(result.get("message", "매수 완료"))
            else:
                st.error(result.get("message", "매수 실패"))
            if isinstance(result, dict):
                _render_budget_metrics(result)
                if result.get("allocation_preview"):
                    st.dataframe(pd.DataFrame(result["allocation_preview"]), use_container_width=True)
                orders = result.get("order_results") or result.get("orders") or []
                if orders:
                    meta_cols = [
                        "requested_mode", "resolved_mode", "base_url", "token_url",
                        "key_type_used", "mock_order_called", "real_order_called",
                        "order_no", "success", "rejected_reason",
                    ]
                    meta_df = pd.DataFrame(orders)
                    show_cols = [c for c in meta_cols if c in meta_df.columns]
                    if show_cols:
                        st.dataframe(meta_df[show_cols], use_container_width=True)
                st.json(result)

with col_preview:
    if st.button("주문 미리보기", use_container_width=True):
        if not candidate_file:
            st.error("후보 파일이 없습니다.")
        else:
            with st.spinner("미리보기 생성 중..."):
                result = run_buy_candidates(
                    candidate_file=candidate_file,
                    budget=int(budget),
                    mode=order_mode.lower(),
                    strategy_id=strategy_id,
                    max_orders=int(max_orders),
                    preview_only=True,
                    sell_policy_id=sell_policy_id,
                )
            if result.get("allocation_preview"):
                st.success(f"미리보기 {len(result['allocation_preview'])}건, 실제 주문 없음")
                st.dataframe(pd.DataFrame(result["allocation_preview"]), use_container_width=True)
                _render_budget_metrics(result)
            else:
                st.warning(result.get("message", "미리보기 결과가 없습니다."))

st.divider()
st.subheader("개별 종목 주문 테스트")
single_mode = st.radio("주문 모드", ["PAPER", "MOCK", "REAL"], index=0, horizontal=True, key="single_order_mode")

if single_mode == "REAL":
    real_order_warning()
    real_ready = _render_real_conditions()
else:
    real_ready = False

with st.form("single_order_form"):
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        stock_code = st.text_input("종목코드", value="005930", max_chars=6)
    with col_b:
        stock_name = st.text_input("종목명", value="삼성전자")
    with col_c:
        order_price = st.number_input("주문가격 (원)", min_value=1, value=70_000)

    if single_mode == "REAL":
        quantity = st.number_input("REAL 테스트 수량", min_value=1, max_value=1, value=1)
        order_amount = int(order_price) * int(quantity)
        st.info(f"REAL 개별 테스트 주문금액: {order_amount:,}원")
    else:
        quantity = 1
        order_amount = st.number_input("주문금액 (원)", min_value=10_000, value=min(int(budget), 100_000))

    submitted = st.form_submit_button(
        f"{single_mode} 주문 실행",
        type="primary" if single_mode == "PAPER" else "secondary",
    )

if single_mode == "REAL":
    preview_key = f"{stock_code}:{int(quantity)}:{int(order_price)}"
    col_prev, col_check = st.columns(2)
    with col_prev:
        if st.button("주문 미리보기", key="real_single_preview"):
            preview = run_real_single_order_test(stock_code, int(quantity), int(order_price), execute=False)
            st.session_state["real_single_preview_key"] = preview_key
            st.session_state["real_single_preview"] = preview
            st.json(preview)
    with col_check:
        if st.button("실전 준비상태 점검 실행", key="real_single_readiness"):
            st.json(run_real_order_readiness_check())

    final_confirm = st.checkbox(
        "위 종목을 실제 계좌에서 1주 주문하는 것을 확인합니다.",
        key="real_single_final_confirm",
    )
else:
    preview_key = ""
    final_confirm = False

if submitted:
    if single_mode == "REAL" and not final_confirm:
        st.error("실전 주문 최종 확인 체크박스를 먼저 체크하세요.")
    elif single_mode == "REAL" and st.session_state.get("real_single_preview_key") != preview_key:
        st.error("실전 주문 전 주문 미리보기를 먼저 실행하세요.")
    elif single_mode == "REAL" and not real_ready:
        st.error("실전 주문 조건 미충족: 주문을 실행하지 않았습니다.")
    else:
        with st.spinner("주문 실행 중..."):
            if single_mode == "REAL":
                result = run_real_single_order_test(stock_code, int(quantity), int(order_price), execute=True)
            else:
                if single_mode == "MOCK":
                    st.warning("MOCK 개별 주문 테스트는 현재 PAPER 테스트 주문으로 기록합니다.")
                result = run_paper_order(stock_code, stock_name, int(order_amount), int(order_price))
        if result.get("success"):
            st.success(f"주문 성공: {result.get('order_no', '')}")
        else:
            st.error(f"주문 실패: {result.get('rejected_reason') or result.get('reason') or result.get('message', '')}")
            if single_mode == "REAL":
                st.warning("주문번호가 없으면 실제 주문 접수 실패로 판단합니다. 한국투자증권 앱의 미체결/체결내역도 확인하세요.")
                if st.button("실전 주문 실패 원인 진단", key="real_order_failure_diagnosis"):
                    diagnosis = run_real_order_diagnosis(stock_code, int(quantity), int(order_price))
                    st.write("진단 결과")
                    st.json({
                        "verdict": diagnosis.get("verdict"),
                        "error_category": diagnosis.get("error_category"),
                        "hashkey_generation_ok": diagnosis.get("hashkey_generation_ok"),
                        "payload_validation_ok": diagnosis.get("payload_validation_ok"),
                        "payload_errors": diagnosis.get("payload_errors"),
                        "orderable_cash": diagnosis.get("orderable_cash"),
                        "recent_order_check": diagnosis.get("recent_order_check"),
                        "txt_path": diagnosis.get("txt_path"),
                        "json_path": diagnosis.get("json_path"),
                    })
                verify_order_no = ""
                if isinstance(result, dict):
                    verify_order_no = (
                        result.get("result", {}).get("order_no", "")
                        if isinstance(result.get("result"), dict)
                        else result.get("order_no", "")
                    )
                if st.button("실전 주문내역 확인", key="real_order_verify_after_failure"):
                    st.json(run_real_order_verify(stock_code=stock_code, order_no=verify_order_no))
        st.json(result)

st.divider()
no_profit_guarantee_notice()
