"""예산배분 및 주문 화면."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (
    str(PROJECT_ROOT),
    str(PROJECT_ROOT / "src"),
    str(PROJECT_ROOT / "app" / "services"),
    str(PROJECT_ROOT / "app" / "components"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config_service import load_config, get_trade_mode, get_safety_status
from trading_service import (
    check_real_order_conditions,
    check_real_readiness,
    get_real_bulk_buy_readiness,
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


def _load_candidates(date_str: str):
    """오늘 날짜 파일 우선, 없으면 가장 최근 파일로 fallback. (df, n, file, loaded_date) 반환."""
    predictions_dir = PROJECT_ROOT / "reports" / "predictions"
    enriched_path = PROJECT_ROOT / "reports" / f"enriched_candidates_{date_str}.csv"
    if enriched_path.exists():
        df = pd.read_csv(enriched_path)
        if not df.empty:
            return df, 100, str(enriched_path), date_str

    for n in (100, 50, 20):
        path = predictions_dir / f"top{n}_{date_str}.csv"
        if path.exists():
            df = pd.read_csv(path)
            if not df.empty:
                return df, n, str(path), date_str

    # Fallback: 가장 최근 날짜 파일
    for n in (100, 50, 20):
        for p in sorted(predictions_dir.glob(f"top{n}_????????.csv"), reverse=True):
            try:
                df = pd.read_csv(p)
                if not df.empty:
                    found_date = p.stem.split("_")[1]
                    return df, n, str(p), found_date
            except Exception:
                continue
    return None, 0, None, date_str


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
df_candidates, loaded_n, candidate_file, loaded_date = _load_candidates(today_str)

if df_candidates is None or df_candidates.empty:
    st.warning("후보 파일이 없습니다. 먼저 파이프라인을 실행해 AI 후보 리스트를 생성하세요.")
    st.stop()

if loaded_date != today_str:
    st.warning(
        f"오늘({today_str}) 파일이 없습니다. 가장 최근({loaded_date}) 데이터를 표시합니다. "
        f"장 마감(16:30) 후 파이프라인을 실행하면 오늘 결과가 생성됩니다."
    )
st.success(f"Top{loaded_n} 후보 파일 로드 완료: {len(df_candidates)}개 종목 ({loaded_date})")

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
    _alloc_mode = st.session_state.get("strategy_order_mode", "MOCK").lower()
    with st.spinner("예산배분 계산 중..."):
        result = run_budget_allocation(int(budget), candidate_file=candidate_file, mode=_alloc_mode)
    if result.get("success"):
        st.success("예산배분 완료")
        summary = result.get("summary", {})
        _render_budget_metrics(
            {
                "input_budget": int(budget),
                "orderable_cash": summary.get("orderable_cash", int(budget)),
                "effective_budget": summary.get("effective_budget", int(budget)),
                "expected_order_amount": summary.get("total_order_amount", 0),
                "remaining_budget": int(summary.get("effective_budget", int(budget))) - int(summary.get("total_order_amount", 0) or 0),
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

# 모드 진단 표시 (필수 6)
if order_mode in ("MOCK", "REAL"):
    try:
        from app.services.env_service import inject_to_os_env as _inj4
        _inj4()
    except Exception as _e4:
        st.warning(f"env_service import 실패: {_e4}")
    try:
        from trade_mode import get_expected_key_fingerprint_for_mode, get_base_url_for_mode
        _expected_fp = get_expected_key_fingerprint_for_mode(order_mode)
        _base_url = get_base_url_for_mode(order_mode)
        _key_env = "KIS_MOCK_APP_KEY" if order_mode == "MOCK" else "KIS_REAL_APP_KEY"
        _diag_col1, _diag_col2 = st.columns(2)
        with _diag_col1:
            st.caption(f"예상 API URL: `{_base_url}`")
            st.caption(f"사용 환경변수: `{_key_env}`")
        with _diag_col2:
            st.caption(f"예상 appkey fingerprint: `{_expected_fp}`")
        if _expected_fp == "MISSING":
            st.error(f"⛔ {_key_env} 환경변수 미설정 — {order_mode} 주문 불가! .env 파일에 키를 등록하세요.")
        else:
            st.success(f"{order_mode} appkey 설정 확인됨: `{_expected_fp}`")
    except Exception as _e:
        st.warning(f"모드 진단 로드 실패: {_e}")

if order_mode == "REAL":
    # Consume rerun flag set by preview button so conditions table reflects new plan_id
    if st.session_state.pop("order_plan_just_created", False):
        pass  # rerun already happened; flag consumed
    real_order_warning()
    import json as _json4
    from trading_service import get_real_bulk_buy_readiness

    # Safety flag check
    _sflag_path4 = PROJECT_ROOT / "reports" / "real_order_safety_flag.json"
    if _sflag_path4.exists():
        try:
            _sflag4 = _json4.loads(_sflag_path4.read_text(encoding="utf-8"))
            if _sflag4.get("blocked"):
                st.error(
                    f"이전 실전 주문 미검증: 주문번호 {_sflag4.get('order_no','?')}, 종목 {_sflag4.get('stock_code','?')}\n"
                    f"추가 실전 주문이 차단됩니다. reports/real_order_safety_flag.json 확인 후 삭제하세요."
                )
        except Exception:
            pass

    # API 설정 당일 확인 상태
    _confirm_path4 = PROJECT_ROOT / "data" / "real_trade_confirmation.json"
    _confirm4 = _json4.loads(_confirm_path4.read_text(encoding="utf-8")) if _confirm_path4.exists() else {}
    _today4 = __import__("datetime").date.today().strftime("%Y%m%d")
    _api_confirmed_today = _confirm4.get("confirmation_date") == _today4 and _confirm4.get("real_trade_confirmed", False)
    if _api_confirmed_today:
        st.success(f"오늘 실전 주문 확인 완료 ({_confirm4.get('confirmed_at','')[:19]})")
    else:
        st.warning("오늘 실전 주문 확인이 필요합니다. [API 설정] 화면에서 확인을 완료하세요.")

    # REAL readiness 확인
    if "real_readiness_buy" not in st.session_state:
        st.session_state["real_readiness_buy"] = None
    _rb_col1, _rb_col2 = st.columns([3, 1])
    with _rb_col1:
        if st.session_state["real_readiness_buy"]:
            _rb = st.session_state["real_readiness_buy"]
            if _rb.get("ready"):
                st.success(f"REAL 계좌조회 준비 완료 — {_rb.get('message', '')}")
            else:
                st.error(f"REAL 계좌조회 실패 — {_rb.get('message', '')}")
    with _rb_col2:
        if st.button("REAL 준비상태 확인", key="btn_real_readiness_buy"):
            with st.spinner("REAL API 계좌조회 확인 중..."):
                st.session_state["real_readiness_buy"] = check_real_readiness()
            st.rerun()

    # 주문계획 상태
    _order_plan_id = st.session_state.get("current_order_plan_id", "")
    _order_plan_total = st.session_state.get("current_order_plan_total", 0)
    _max_real_bulk = int(cfg.get("real_trade", {}).get("max_real_bulk_order_amount", 300_000))

    if _order_plan_id:
        st.info(f"주문계획: `{_order_plan_id}` | 예정금액: {int(_order_plan_total):,}원 / 한도: {_max_real_bulk:,}원")
    else:
        st.warning("주문계획(order_plan_id)이 없습니다. 먼저 '주문 미리보기'를 실행해 주문계획을 생성하세요.")

    # 사용자 최종 확인 체크박스
    _real_confirm1 = st.checkbox(
        "위 주문계획의 모든 종목을 실제 계좌에서 매수하는 것을 확인합니다.",
        key="real_bulk_confirm1",
    )
    _real_confirm2 = st.checkbox(
        "실제 자금이 사용되며 손실 및 미체결 가능성을 이해합니다.",
        key="real_bulk_confirm2",
    )

    # 안전조건 점검
    _bulk_readiness = get_real_bulk_buy_readiness(
        planned_total_amount=int(_order_plan_total),
        order_plan_id=_order_plan_id,
        user_confirmed_bulk_real=(_real_confirm1 and _real_confirm2),
    )

    # 조건 표시
    _cond_table = {
        "real_readiness_ready": "REAL API 준비 완료",
        "api_confirmation_today": "오늘 API 설정 확인 완료",
        "order_plan_exists": "주문계획(order_plan_id) 존재",
        "order_plan_hash_valid": "주문계획 해시 유효",
        "budget_within_limit": f"주문금액 한도 내 ({_max_real_bulk:,}원 이하)",
        "user_confirmed_bulk_real": "사용자 최종 확인 체크박스",
        "real_bulk_enabled": "config 전체매수 허용",
    }
    _cond_data = _bulk_readiness.get("conditions", {})
    _cond_rows = [{"조건": v, "상태": "OK" if _cond_data.get(k) else "FAIL"} for k, v in _cond_table.items()]
    st.dataframe(_cond_rows, use_container_width=True, hide_index=True)

    if not _bulk_readiness.get("ready"):
        _missing = _bulk_readiness.get("missing_conditions", [])
        st.error(f"REAL 전체 리스트 매수를 위해 필요한 조건이 아직 충족되지 않았습니다.\n미충족: {', '.join(_missing)}")

    real_bulk_ok = _bulk_readiness.get("ready", False)

else:
    real_bulk_ok = True
    _real_confirm1 = False
    _real_confirm2 = False
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
            if order_mode == "REAL":
                # Clear order plan after execution
                st.session_state.pop("current_order_plan_id", None)
                st.session_state.pop("current_order_plan_total", None)
                st.session_state.pop("current_order_plan_preview", None)
            if isinstance(result, dict):
                _render_budget_metrics(result)
                if result.get("allocation_preview"):
                    st.dataframe(pd.DataFrame(result["allocation_preview"]), use_container_width=True)
                orders = result.get("order_results") or result.get("orders") or []
                if orders:
                    meta_cols = [
                        "stock_code", "stock_name", "quantity", "order_price",
                        "order_no", "rt_cd", "msg",
                        "requested_mode", "resolved_mode",
                        "api_called", "real_order_called", "mock_order_called",
                        "success", "fill_status", "rejected_reason",
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
                # REAL 모드: order_plan_id를 session_state에 저장
                if order_mode == "REAL":
                    import uuid, datetime as _dt
                    _plan_id = _dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:8]
                    _plan_total = sum(int(r.get("order_amount", 0) or 0) for r in result["allocation_preview"])
                    st.session_state["current_order_plan_id"] = _plan_id
                    st.session_state["current_order_plan_total"] = _plan_total
                    st.session_state["current_order_plan_preview"] = result["allocation_preview"]
                    st.session_state["order_plan_just_created"] = True
                    st.info(f"주문계획 생성됨: `{_plan_id}` | 예정금액: {_plan_total:,}원")
                    st.rerun()
            else:
                st.warning(result.get("message", "미리보기 결과가 없습니다."))

st.divider()
st.subheader("개별 종목 주문 테스트")
single_mode = st.radio("주문 모드", ["PAPER", "MOCK", "REAL"], index=0, horizontal=True, key="single_order_mode")

if single_mode == "REAL":
    real_order_warning()
    # Safety flag check
    import json as _json
    _sflag_path = PROJECT_ROOT / "reports" / "real_order_safety_flag.json"
    if _sflag_path.exists():
        try:
            _sflag = _json.load(open(_sflag_path, encoding="utf-8"))
            if _sflag.get("blocked"):
                st.error(
                    f"⛔ 이전 실전 주문이 증권사 주문내역에서 검증되지 않았습니다.\n"
                    f"주문번호: {_sflag.get('order_no', '?')}, 종목: {_sflag.get('stock_code', '?')}\n"
                    f"추가 실전 주문이 차단됩니다. 파일 확인 후 삭제: reports/real_order_safety_flag.json"
                )
        except Exception:
            pass
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
        _fill_status = result.get("fill_status", "") or (result.get("result", {}) or {}).get("fill_status", "")
        _order_no = (result.get("result", {}) or {}).get("order_no", "") or result.get("order_no", "")
        _order_verify_ok = result.get("order_verify_success", False) or result.get("order_found_in_broker", False)

        if _fill_status == "FILLED":
            st.success(f"주문 접수 및 체결 완료 — 주문번호 {_order_no}")
        elif _fill_status in ("ACCEPTED_UNFILLED", "PARTIALLY_FILLED"):
            label = "주문 접수됨 — 미체결 상태" if _fill_status == "ACCEPTED_UNFILLED" else "일부 체결됨 — 잔량 미체결"
            st.warning(f"{label} — 주문번호 {_order_no}")
        elif _fill_status == "SUBMITTED_UNVERIFIED":
            st.error(
                f"⚠️ 주문 전송 응답은 받았으나 증권사 주문내역에서 확인되지 않습니다.\n"
                f"주문번호: {_order_no}\n"
                f"추가 실전 주문을 중단하고 한국투자증권 앱에서 주문내역을 확인하세요."
            )
        elif _fill_status == "DRY_RUN_ONLY":
            st.info(f"주문 미리보기 성공 — 실제 주문 없음")
        elif _fill_status == "BLOCKED_BY_SAFETY_FLAG":
            st.error("이전 실전 주문이 검증되지 않아 추가 실전 주문이 차단됩니다. reports/real_order_safety_flag.json을 확인하세요.")
        elif result.get("success"):
            st.success(f"주문 전송 완료 — 주문번호 {_order_no}")
        else:
            st.error(f"주문 실패: {result.get('rejected_reason') or result.get('reason') or result.get('message', '')}")

        if single_mode == "REAL" and not _order_verify_ok and _order_no:
            st.warning("주문번호가 있으나 증권사 주문내역 자동 검증에 실패했습니다. 한국투자증권 앱의 미체결/체결내역도 직접 확인하세요.")
            if st.button("실전 주문 실패 원인 진단", key="real_order_failure_diagnosis"):
                diagnosis = run_real_order_diagnosis(stock_code, int(quantity), int(order_price))
                st.json({
                    "verdict": diagnosis.get("verdict"),
                    "error_category": diagnosis.get("error_category"),
                    "hashkey_generation_ok": diagnosis.get("hashkey_generation_ok"),
                    "payload_validation_ok": diagnosis.get("payload_validation_ok"),
                    "payload_errors": diagnosis.get("payload_errors"),
                    "orderable_cash": diagnosis.get("orderable_cash"),
                })
            if st.button("실전 주문내역 재확인", key="real_order_verify_after_failure"):
                st.json(run_real_order_verify(stock_code=stock_code, order_no=_order_no))
        st.json(result)

st.divider()
no_profit_guarantee_notice()
