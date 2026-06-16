"""예산배분 및 주문 화면."""

import os
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
    get_kis_token_status,
    check_kis_account,
    check_orderable_cash,
    run_full_trading_diagnosis,
    get_active_buy_candidate_file,
)
from prediction_service import get_today_str
from market_safety_filter import normalize_bool, validate_orderable_buy_top20_df
from warning_box import no_profit_guarantee_notice, real_order_warning
from mode_badge import render_mode_badge, render_mode_warning


def _load_candidates(date_str: str):
    """buy_top20만 주문 후보로 반환. top100/enriched fallback 없음. (df, n, file, loaded_date) 반환."""
    predictions_dir = PROJECT_ROOT / "reports" / "predictions"

    buy20_path = predictions_dir / f"buy_top20_{date_str}.csv"
    if buy20_path.exists():
        try:
            df = pd.read_csv(buy20_path)
            if not df.empty:
                return df, 20, str(buy20_path), date_str
        except Exception:
            pass

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
        raw = result.get(key)
        if raw is None:
            col.metric(label, "조회 실패")
        else:
            value = int(float(raw or 0))
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
    st.error("buy_top20 파일이 없습니다. 주문 불가.")
    st.info(
        "**해결 방법**: AI 후보 리스트 페이지 → '장중 매수 Top20 필터 실행' 버튼 클릭 "
        "(top100 파일이 있어야 합니다. 없으면 '전체 파이프라인 실행' 먼저 클릭)"
    )
    # 진단 정보
    _preds_dir = PROJECT_ROOT / "reports" / "predictions"
    _preds_exists = _preds_dir.exists()
    st.subheader("후보 파일 부재 진단")
    _dc1, _dc2, _dc3 = st.columns(3)
    _dc1.metric("reports/predictions 폴더", "✅ 있음" if _preds_exists else "❌ 없음")
    if _preds_exists:
        _buy20_files = list(_preds_dir.glob(f"buy_top20_{today_str}*.csv"))
        _any_buy20_files = list(_preds_dir.glob("buy_top20_????????.csv"))
        _dc2.metric(f"buy_top20_{today_str}.csv", "✅ 있음" if _buy20_files else "❌ 없음")
        _dc3.metric("전체 buy_top20 파일 수", f"{len(_any_buy20_files)}개")
        if _any_buy20_files:
            _latest = sorted(_any_buy20_files, key=lambda f: f.stat().st_mtime, reverse=True)[0]
            st.info(f"가장 최근 buy_top20 파일: {_latest.name}")
    else:
        _dc2.metric(f"buy_top20_{today_str}.csv", "❌ 없음")
        _dc3.metric("전체 buy_top20 파일 수", "0개")
    st.stop()

if loaded_date != today_str:
    st.warning(
        f"오늘({today_str}) 파일이 없습니다. 가장 최근({loaded_date}) 데이터를 표시합니다. "
        f"장 마감(16:30) 후 파이프라인을 실행하면 오늘 결과가 생성됩니다."
    )

# ── 로드된 파일 정보 및 가격 신선도 경고 ─────────────────────────────────
_cfile_name = Path(candidate_file).name if candidate_file else "알수없음"
_is_buy_top20 = "buy_top20" in _cfile_name
_is_top20_plain = "top20" in _cfile_name and "buy_top20" not in _cfile_name
_cfile_mtime_str = ""
if candidate_file and Path(candidate_file).exists():
    import time as _time
    _cfile_mtime_str = _time.strftime("%H:%M:%S", _time.localtime(Path(candidate_file).stat().st_mtime))

# 가격 데이터 소스 확인
_price_stale = False
_price_source_msg = ""
if df_candidates is not None and "_data_source" in df_candidates.columns:
    _sources = df_candidates["_data_source"].dropna().unique().tolist()
    if any("csv" in str(s).lower() or "paper" in str(s).lower() for s in _sources):
        _price_stale = True
        _price_source_msg = f"데이터 소스: {', '.join(str(s) for s in _sources[:3])} — 전날 종가 기준"
elif df_candidates is not None and "current_price" not in df_candidates.columns:
    _price_stale = True
    _price_source_msg = "current_price 컬럼 없음 — 일봉 종가(close) 기준"

# 파일 일치 여부 확인 (top20_plain vs buy_top20)
_preds_dir_4 = PROJECT_ROOT / "reports" / "predictions"
_top20_plain_path = _preds_dir_4 / f"top20_{loaded_date}.csv"
_buy_top20_path = _preds_dir_4 / f"buy_top20_{loaded_date}.csv"
_file_mismatch_warn = _is_buy_top20 and _top20_plain_path.exists()

_f_col1, _f_col2, _f_col3 = st.columns(3)
_f_col1.metric("로드된 파일", _cfile_name)
_f_col2.metric("파일 생성시각", _cfile_mtime_str or "알수없음")
_f_col3.metric("종목 수", f"{len(df_candidates)}개")

if _is_buy_top20:
    _has_intra_prob = df_candidates is not None and "prob_intraday_2pct" in df_candidates.columns
    if _has_intra_prob:
        _mean_prob = df_candidates["prob_intraday_2pct"].mean()
        st.info(
            f"**buy_top20 파일 사용 중** (장중 AI 모델 기반) — "
            f"당일 +2% 목표 intraday 모델 적용. "
            f"평균 prob_intraday_2pct: **{_mean_prob:.3f}**"
        )
    else:
        st.info(
            "**buy_top20 파일 사용 중** (장중 필터 적용 버전) — "
            "AI 예측 점수 + 장중 거래량/가격 조건으로 선별된 종목입니다. "
            "AI 후보 리스트 페이지에서 '장중 AI 예측' → '장중 Top20 선정 (AI)'를 실행하면 "
            "prob_intraday_2pct 기반으로 업그레이드됩니다."
        )
elif _is_top20_plain:
    st.info("**top20 파일 사용 중** (AI 예측 점수 순위 기준) — 장중 필터 미적용 버전입니다.")

if _file_mismatch_warn:
    st.warning(
        f"⚠ AI 후보 리스트 페이지에서 보이는 top20(예측순위)과 이 페이지에서 사용하는 "
        f"buy_top20(장중 필터)은 종목이 다를 수 있습니다. "
        f"두 페이지의 리스트를 일치시키려면 AI 후보 리스트 페이지에서 '장중 Top20 필터 실행'을 다시 실행하세요."
    )

if _price_stale:
    st.warning(
        f"⚠ 가격 정보가 실시간이 아닙니다 ({_price_source_msg}). "
        f"현재가 반영 주문을 위해 아래 '주문 미리보기' 클릭 전 KIS API로 현재가를 갱신하세요. "
        f"또는 '현재가 갱신 후 매수' 버튼을 사용하세요."
    )

st.success(f"Top{loaded_n} 후보 파일 로드 완료: {len(df_candidates)}개 종목 ({loaded_date})")

_safe_ok, _safe_failed_checks = validate_orderable_buy_top20_df(df_candidates, cfg.get("safe_intraday_filter", {}))
_min_candidates_to_trade = int(cfg.get("safe_intraday_filter", {}).get("min_candidates_to_trade", 10))
if len(df_candidates) < _min_candidates_to_trade and not cfg.get("safe_intraday_filter", {}).get("allow_trade_when_candidates_below_min", False):
    _safe_ok = False
    if "candidate_count_below_min_candidates_to_trade" not in _safe_failed_checks:
        _safe_failed_checks.append("candidate_count_below_min_candidates_to_trade")

if _safe_ok:
    st.success("안전 필터 검증 통과: 주문 가능한 buy_top20 파일입니다.")
else:
    st.error(
        "안전 필터를 통과하지 못한 종목이 포함되어 주문을 중단합니다.\n\n"
        f"실패 항목: {', '.join(_safe_failed_checks)}"
    )

_order_blocked_by_safety = not _safe_ok

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
    max_orders = st.number_input("최대 주문 건수", min_value=1, max_value=20, value=min(loaded_n, 20))

st.caption(f"현재 후보 {len(df_candidates)}개, 예산 {int(budget):,}원, 최대 {int(max_orders)}건 주문")

with st.expander("후보 목록 미리보기", expanded=False):
    code_col = "stock_code" if "stock_code" in df_candidates.columns else "ticker"
    name_col = "stock_name" if "stock_name" in df_candidates.columns else "name"
    show_cols = [
        c
        for c in [
            code_col, name_col,
            "prob_intraday_2pct", "prob_intraday_3pct", "prob_intraday_5pct",
            "final_intraday_score", "final_buy_rank",
            "current_price", "close", "gap_rate",
            "buy_allowed", "disclosure_summary",
            "probability_2pct", "final_score",
        ]
        if c in df_candidates.columns
    ]
    _col_cfg = {}
    if "prob_intraday_2pct" in df_candidates.columns:
        _col_cfg["prob_intraday_2pct"] = st.column_config.ProgressColumn(
            "prob +2% (당일)", format="%.3f", min_value=0, max_value=1)
    if "prob_intraday_3pct" in df_candidates.columns:
        _col_cfg["prob_intraday_3pct"] = st.column_config.ProgressColumn(
            "prob +3% (당일)", format="%.3f", min_value=0, max_value=1)
    if "probability_2pct" in df_candidates.columns:
        _col_cfg["probability_2pct"] = st.column_config.ProgressColumn(
            "prob (익일)", format="%.3f", min_value=0, max_value=1)
    st.dataframe(df_candidates[show_cols].head(30), use_container_width=True,
                 column_config=_col_cfg if _col_cfg else None)

st.divider()
st.subheader("예산배분 계산")
st.caption(
    "예산배분 결과는 실제 주문과 동일한 로직을 사용합니다: "
    "이미 보유 중인 종목(OPEN)은 제외되며, 주문가능금액이 입력 예산보다 작으면 주문가능금액이 적용됩니다."
)
if st.button("예산배분 계산", type="primary"):
    _alloc_mode = st.session_state.get("strategy_order_mode", "MOCK").lower()
    with st.spinner("예산배분 계산 중..."):
        result = run_budget_allocation(int(budget), candidate_file=candidate_file, mode=_alloc_mode)
    if result.get("success"):
        summary = result.get("summary", {})
        _held_count = int(summary.get("held_count", 0))
        _total_cands = int(summary.get("total_candidates", len(df_candidates)))
        _avail_cands = int(summary.get("available_candidates", _total_cands))
        _alloc_count = int(summary.get("allocated_count", 0))
        _price_stale_alloc = summary.get("price_stale", False)
        _price_src_alloc = summary.get("price_source", "")

        # orderable_cash 조회 실패 경고 (0원과 구분)
        if not summary.get("orderable_cash_success", True):
            st.warning(
                f"⚠ 주문가능금액 조회 실패: {summary.get('orderable_cash_error', 'KIS API 오류')}\n"
                "입력 예산을 기준으로 예산배분을 계산했습니다. 실제 매수 시 주문가능금액을 다시 확인하세요."
            )

        _held_source = summary.get("held_source", "local")
        _held_source_label = "KIS 브로커 실계좌" if _held_source == "kis_broker" else "로컬 positions.json"
        if _held_count > 0:
            _held_names = summary.get("held_names", [])
            _held_list = ", ".join(
                f"{h.get('name','?')}({h.get('code','?')})" for h in _held_names[:10]
            )
            st.warning(
                f"KIS 계좌에서 보유 중인 종목 {_held_count}개 제외 ({_held_source_label} 기준) → "
                f"유효 후보 {_avail_cands}개 중 {_alloc_count}개 배분\n\n"
                f"제외 종목: {_held_list}"
                + (" 외..." if len(_held_names) > 10 else "")
            )
        else:
            st.success(
                f"예산배분 완료 — {_avail_cands}개 후보 중 {_alloc_count}개 종목 배분 "
                f"(보유종목 조회: {_held_source_label})"
            )

        if _price_stale_alloc:
            st.warning(
                f"⚠ 가격 기준: {_price_src_alloc} — "
                "실시간 가격이 아닙니다. 실제 주문가격과 다를 수 있습니다."
            )

        _ord_ok = summary.get("orderable_cash_success", True)
        _ord_cash_raw = summary.get("orderable_cash")
        _render_budget_metrics(
            {
                "input_budget": int(budget),
                "orderable_cash": int(_ord_cash_raw) if (_ord_ok and _ord_cash_raw is not None) else None,
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
        with st.expander("예산배분 상세 요약", expanded=False):
            st.json(summary)
    else:
        st.error(f"예산배분 실패: {result.get('message', '')}")

st.divider()

# ── MOCK 전체 진단 ────────────────────────────────────────────────────────────
st.subheader("MOCK 전체 진단")
st.caption("현재가·계좌·매수가능금액·후보파일·주문 가능 여부를 한 번에 점검합니다.")
_diag_cols = st.columns([1, 2])
with _diag_cols[0]:
    _diag_mode = st.selectbox("진단 모드", ["mock", "paper"], key="diag_mode_select")
with _diag_cols[1]:
    if st.button("MOCK 전체 진단 실행", use_container_width=True, type="secondary"):
        with st.spinner(f"진단 중 (mode={_diag_mode.upper()})..."):
            from env_service import inject_to_os_env as _inj_diag
            _inj_diag()
            _diag_result = run_full_trading_diagnosis(mode=_diag_mode, budget=int(budget))
        _dc = st.columns(4)
        _dc[0].metric("후보 파일", "✅ 있음" if _diag_result.get("candidate_file") else "❌ 없음")
        _dc[1].metric("현재가 갱신", "✅ 최신" if _diag_result.get("current_price_updated") else "❌ 미갱신")
        _dc[2].metric("KIS 보유 종목",
                      str(_diag_result.get("broker_position_count", -1)) + "개"
                      if _diag_result.get("broker_position_count", -1) >= 0
                      else "조회 실패")
        _dc[3].metric("로컬 OPEN",
                      str(_diag_result.get("local_open_position_count", -1)) + "개"
                      if _diag_result.get("local_open_position_count", -1) >= 0
                      else "조회 실패")

        _dc2 = st.columns(3)
        _ord_cash = _diag_result.get("orderable_cash")
        _ord_ok = _diag_result.get("orderable_cash_success", False)
        _dc2[0].metric(
            "주문가능금액",
            f"{_ord_cash:,}원" if (_ord_ok and _ord_cash is not None) else "조회 실패"
        )
        _dc2[1].metric("전부 매수 가능", "✅ 가능" if _diag_result.get("buy_preflight_ok") else "❌ 불가")
        _dc2[2].metric("Render 파이프라인", "✅ 준비" if _diag_result.get("render_pipeline_ready") else "❌ 미준비")

        if _diag_result.get("buy_block_reason"):
            st.error(f"매수 차단 사유: {_diag_result['buy_block_reason']}")
        if _diag_result.get("failed_checks"):
            st.warning("실패 항목:\n" + "\n".join(f"- {c}" for c in _diag_result["failed_checks"]))
        if _diag_result.get("latest_buy_orders_file"):
            st.caption(f"최신 매수 주문: {Path(_diag_result['latest_buy_orders_file']).name}")
        with st.expander("진단 결과 전체 JSON", expanded=False):
            st.json(_diag_result)

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

elif order_mode == "MOCK":
    # ── MOCK 전부 매수 preflight ──────────────────────────────────
    st.subheader("MOCK 매수 Preflight 체크")
    st.caption("MOCK 주문은 모의투자 서버(openapivts)와 KIS_MOCK_APP_KEY만 사용합니다.")

    # .env 주입
    try:
        from env_service import inject_to_os_env as _inj_mock
        _inj_mock()
    except Exception:
        pass

    # 1. 환경변수
    _mk_ok = bool(os.environ.get("KIS_MOCK_APP_KEY"))
    _ms_ok = bool(os.environ.get("KIS_MOCK_APP_SECRET"))
    _ma_ok = bool(os.environ.get("KIS_MOCK_ACCOUNT_NO"))
    _mock_env_all_ok = _mk_ok and _ms_ok and _ms_ok and _ma_ok

    # 2. 토큰 캐시 상태
    _mts = get_kis_token_status("mock")
    _mock_cache = _mts.get("cache_exists", False)
    _mock_token_valid = _mock_cache and not _mts.get("is_expired", True)

    # 3. 계좌조회 (세션 상태)
    _mock_acc_result = st.session_state.get("mock_preflight_account_ok", None)

    # 4. 미리보기 결과 (세션 상태)
    _mock_alloc_preview = st.session_state.get("mock_allocation_preview", [])
    _mock_alloc_exists = len(_mock_alloc_preview) > 0
    _mock_total_qty = sum(int(r.get("quantity", 0) or 0) for r in _mock_alloc_preview)
    _mock_total_amt = sum(int(r.get("order_amount", 0) or 0) for r in _mock_alloc_preview)

    # preflight 아이템 (None = 미확인, True = OK, False = FAIL)
    _pf_items = [
        ("MOCK 환경변수\n(KEY/SECRET/ACCOUNT)", _mock_env_all_ok),
        ("MOCK 토큰 유효\n(캐시 상태)", _mock_token_valid if _mock_cache else (None if _mock_env_all_ok else False)),
        ("MOCK 계좌조회", _mock_acc_result),
        ("주문계획 존재\n(미리보기 필요)", _mock_alloc_exists),
        ("주문계획 해시\n(MOCK: 자동 OK)", True),
        ("주문수량 > 0", (_mock_total_qty > 0) if _mock_alloc_exists else None),
        ("예상주문금액 > 0", (_mock_total_amt > 0) if _mock_alloc_exists else None),
    ]

    # 테이블 표시
    _pf_cols = st.columns(len(_pf_items))
    for _col, (_lbl, _val) in zip(_pf_cols, _pf_items):
        if _val is None:
            _col.metric(_lbl, "⬜ 미확인")
        elif _val:
            _col.metric(_lbl, "✅ OK")
        else:
            _col.metric(_lbl, "❌ FAIL")

    # 계좌조회 버튼
    if st.button("MOCK 계좌조회 확인", key="mock_preflight_acc_btn"):
        with st.spinner("MOCK 계좌조회 중..."):
            _acc_r = check_kis_account("mock")
        if _acc_r.get("account_ok"):
            st.session_state["mock_preflight_account_ok"] = True
            st.success(f"MOCK 계좌조회 OK — {_acc_r.get('broker_count', 0)}개 종목")
        else:
            st.session_state["mock_preflight_account_ok"] = False
            _acc_err = (_acc_r.get("response_text") or _acc_r.get("error") or "")[:200]
            st.error(f"MOCK 계좌조회 실패: {_acc_err}")
        st.rerun()

    # ── 포지션 상태 상세 표시 ──────────────────────────────────
    with st.expander("포지션 상태 상세 (매수 차단 원인 확인)", expanded=False):
        try:
            import sys as _sys_pf
            _src_pf = str(PROJECT_ROOT / "src")
            if _src_pf not in _sys_pf.path:
                _sys_pf.path.insert(0, _src_pf)
            from position_manager import PositionManager as _PM_pf
            from utils import load_config as _lc_pf
            _cfg_pf = _lc_pf(str(PROJECT_ROOT / "config.yaml"))
            _max_pos_pf = _cfg_pf.get("risk", {}).get("max_positions", 20)
            _pm_pf = _PM_pf(str(PROJECT_ROOT / "config.yaml"), mode="mock")
            _all_pf = _pm_pf.get_all_positions()
            _open_pf = [p for p in _all_pf.values() if not p.is_closed and p.status == "OPEN" and int(p.quantity) > 0]
            _pending_pf = [p for p in _all_pf.values() if p.status == "OPEN_WITH_PENDING_SELL"]
            _closed_pf = [p for p in _all_pf.values() if p.is_closed or p.status == "CLOSED"]
            _broker_count_pf = _acc_r.get("broker_count", "미확인") if _mock_acc_result else "미확인"
            _pf_detail_cols = st.columns(5)
            _pf_detail_cols[0].metric("KIS 실제 보유", f"{_broker_count_pf}개")
            _pf_detail_cols[1].metric("로컬 OPEN", f"{len(_open_pf)}개")
            _pf_detail_cols[2].metric("미체결 매도", f"{len(_pending_pf)}개")
            _pf_detail_cols[3].metric("CLOSED", f"{len(_closed_pf)}개")
            _pf_detail_cols[4].metric("max_positions", f"{_max_pos_pf}개")
            _buy_ok_pf = len(_open_pf) < _max_pos_pf
            if _buy_ok_pf:
                st.success(f"✅ 매수 가능 — OPEN {len(_open_pf)}/{_max_pos_pf}")
            else:
                st.error(f"❌ 최대 보유 종목 수 초과: OPEN {len(_open_pf)}/{_max_pos_pf}")
            if _pending_pf:
                st.warning(
                    f"미체결 매도 주문 {len(_pending_pf)}건: "
                    + ", ".join(p.stock_code for p in _pending_pf[:10])
                    + "\n\n보유종목 페이지에서 계좌 동기화 후 재시도하세요."
                )
            st.caption(f"포지션 파일: {_pm_pf._positions_file}")
        except Exception as _pf_ex:
            st.caption(f"포지션 상태 조회 실패: {_pf_ex}")

    # 실패 사유 표시
    _fail_reasons = [_lbl.replace("\n", " ") for _lbl, _val in _pf_items if _val is False]
    if _fail_reasons:
        st.error(f"MOCK 전부 매수 차단 사유: {' | '.join(_fail_reasons)}")
        if not _mock_env_all_ok:
            st.error(
                "❌ KIS_MOCK_APP_KEY / KIS_MOCK_APP_SECRET / KIS_MOCK_ACCOUNT_NO 가 누락되었습니다.\n\n"
                "**Render Dashboard > Environment Variables에 MOCK 키를 입력하세요.**"
            )

    _fail_definite = [_val for _, _val in _pf_items if _val is False]
    real_bulk_ok = True
    mock_bulk_ok = len(_fail_definite) == 0
    _real_confirm1 = False
    _real_confirm2 = False

else:  # PAPER
    real_bulk_ok = True
    mock_bulk_ok = True
    _real_confirm1 = False
    _real_confirm2 = False

# ── 현재 보유 종목 현황 표시 (KIS 브로커 실계좌 기준, 주문 전 참고용) ────────────────
try:
    _cand_codes4 = set()
    if df_candidates is not None and not df_candidates.empty:
        _cc4 = "stock_code" if "stock_code" in df_candidates.columns else "ticker"
        _cand_codes4 = {str(c).zfill(6) for c in df_candidates[_cc4].tolist()}

    _broker_overlap4 = []
    _overlap_source4 = "로컬 positions.json"

    if order_mode.lower() != "paper":
        # KIS 브로커 실계좌에서 보유종목 조회 (8초 타임아웃)
        import concurrent.futures as _cf4
        _broker_pos4: list = [None]

        def _fetch_pos4():
            try:
                from kis_api import KISApiClient
                from safety_gate import SafetyGate
                _g4 = SafetyGate(str(PROJECT_ROOT / "config.yaml"), runtime_mode=order_mode.lower())
                _a4 = KISApiClient(str(PROJECT_ROOT / "config.yaml"), gate=_g4)
                _a4.auth.get_access_token()
                _df4 = _a4.get_positions()
                _broker_pos4[0] = _df4
            except Exception:
                pass

        with _cf4.ThreadPoolExecutor(max_workers=1) as _ex4:
            _fut4 = _ex4.submit(_fetch_pos4)
            try:
                _fut4.result(timeout=8)
            except _cf4.TimeoutError:
                pass

        if _broker_pos4[0] is not None and not _broker_pos4[0].empty:
            _broker_codes4 = set(_broker_pos4[0]["stock_code"].astype(str).str.zfill(6).tolist())
            _broker_overlap4 = [
                {"stock_code": c, "stock_name": row.get("stock_name", c)}
                for _, row in _broker_pos4[0].iterrows()
                for c in [str(row["stock_code"]).zfill(6)]
                if c in _cand_codes4
            ]
            _overlap_source4 = "KIS 브로커 실계좌"
        elif _broker_pos4[0] is not None:
            # KIS 조회 성공했지만 보유종목 없음
            _broker_overlap4 = []
            _overlap_source4 = "KIS 브로커 실계좌"
        # KIS 조회 실패 시 로컬 fallback
        if _broker_pos4[0] is None:
            from position_manager import PositionManager as _PM4
            _pm4 = _PM4(str(PROJECT_ROOT / "config.yaml"), mode=order_mode.lower())
            _all4 = _pm4.get_all_positions()
            _open4 = [p for p in _all4.values() if getattr(p, "status", "OPEN") == "OPEN" and not getattr(p, "is_closed", False)]
            _broker_overlap4 = [
                {"stock_code": p.stock_code, "stock_name": p.stock_name}
                for p in _open4 if str(p.stock_code).zfill(6) in _cand_codes4
            ]
    else:
        from position_manager import PositionManager as _PM4
        _pm4 = _PM4(str(PROJECT_ROOT / "config.yaml"), mode=order_mode.lower())
        _all4 = _pm4.get_all_positions()
        _open4 = [p for p in _all4.values() if getattr(p, "status", "OPEN") == "OPEN" and not getattr(p, "is_closed", False)]
        _broker_overlap4 = [
            {"stock_code": p.stock_code, "stock_name": p.stock_name}
            for p in _open4 if str(p.stock_code).zfill(6) in _cand_codes4
        ]

    if _broker_overlap4:
        _excl_names4 = ", ".join(
            f"{h.get('stock_name','?')}({h.get('stock_code','?')})" for h in _broker_overlap4[:10]
        )
        st.info(
            f"KIS 계좌에서 보유 중인 종목 {len(_broker_overlap4)}개가 후보 리스트와 겹칩니다 → 주문 시 제외됩니다. "
            f"({_overlap_source4} 기준)\n\n"
            f"제외 예정: {_excl_names4}" + (" 외..." if len(_broker_overlap4) > 10 else "") + "\n\n"
            f"남은 주문 대상: 후보 {len(df_candidates)}개 중 {len(df_candidates) - len(_broker_overlap4)}개"
        )
except Exception:
    pass

# 현재가 갱신 옵션 (MOCK/REAL: paper_csv 데이터면 갱신 권장)
_refresh_prices_opt = False
if order_mode in ("MOCK", "REAL") and _price_stale:
    _refresh_prices_opt = st.checkbox(
        "주문 전 현재가 갱신 (KIS API에서 실시간 가격 조회, 1-2분 소요)",
        value=False,
        help="buy_top20 파일이 전날 종가를 사용하는 경우 현재가로 갱신합니다. "
             "미갱신 시 파일의 가격(전날 종가)으로 주문됩니다.",
    )

col_buy, col_preview = st.columns(2)
with col_buy:
    # REAL: real_bulk_ok 조건 / MOCK: mock_bulk_ok 조건 / PAPER: 항상 허용
    _buy_disabled = (
        (order_mode == "REAL" and not real_bulk_ok)
        or (order_mode == "MOCK" and not locals().get("mock_bulk_ok", True))
        or _order_blocked_by_safety
    )
    if st.button("현재 리스트 전부 매수", type="primary", use_container_width=True, disabled=_buy_disabled):
        if not candidate_file:
            st.error("후보 파일이 없습니다.")
        elif len(df_candidates) > 20:
            st.error(f"후보 종목이 {len(df_candidates)}개입니다. 최대 20개까지만 주문 가능합니다. 장중 Top20 필터를 먼저 실행하세요.")
        else:
            _safe_max_orders = min(int(max_orders), 20)
            with st.spinner(f"{order_mode} 전략 매수 실행 중..."):
                result = run_buy_candidates(
                    candidate_file=candidate_file,
                    budget=int(budget),
                    mode=order_mode.lower(),
                    strategy_id=strategy_id,
                    max_orders=_safe_max_orders,
                    sell_policy_id=sell_policy_id,
                    refresh_prices=_refresh_prices_opt,
                )
            if result.get("success"):
                _orders_placed = result.get("orders_placed", 0)
                _total_attempted = result.get("total_attempted", 0)
                _held_cnt = result.get("held_count", 0) if hasattr(result, "get") else 0
                st.success(result.get("message", "매수 완료"))
                if _total_attempted > 0 and _orders_placed < _total_attempted:
                    st.info(
                        f"{_total_attempted}개 배분 중 {_orders_placed}개 주문 완료. "
                        f"나머지 {_total_attempted - _orders_placed}개는 RiskManager 차단 또는 오류."
                    )
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
                    _prev_df = pd.DataFrame(result["allocation_preview"])
                    st.dataframe(_prev_df, use_container_width=True)
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
                with st.expander("전체 주문 결과 JSON", expanded=False):
                    st.json(result)

with col_preview:
    if st.button("주문 미리보기", use_container_width=True, disabled=_order_blocked_by_safety):
        if not candidate_file:
            st.error("후보 파일이 없습니다.")
        else:
            _safe_max_orders = min(int(max_orders), 20)
            with st.spinner("미리보기 생성 중..."):
                result = run_buy_candidates(
                    candidate_file=candidate_file,
                    budget=int(budget),
                    mode=order_mode.lower(),
                    strategy_id=strategy_id,
                    max_orders=_safe_max_orders,
                    preview_only=True,
                    sell_policy_id=sell_policy_id,
                    refresh_prices=_refresh_prices_opt,
                )
            if result.get("allocation_preview"):
                _prev_list = result["allocation_preview"]
                _held_in_prev = result.get("held_count", 0)
                _total_in_prev = result.get("total_candidates", len(df_candidates))
                st.success(f"미리보기 {len(_prev_list)}건 (후보 {_total_in_prev}개 중), 실제 주문 없음")
                if _held_in_prev:
                    st.info(f"이미 보유 중인 종목 {_held_in_prev}개 제외됨 → {_total_in_prev - _held_in_prev}개 후보만 배분")
                st.dataframe(pd.DataFrame(_prev_list), use_container_width=True)
                _render_budget_metrics(result)
                # REAL 모드: order_plan_id를 session_state에 저장
                if order_mode == "REAL":
                    import uuid, datetime as _dt
                    _plan_id = _dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:8]
                    _plan_total = sum(int(r.get("order_amount", 0) or 0) for r in _prev_list)
                    st.session_state["current_order_plan_id"] = _plan_id
                    st.session_state["current_order_plan_total"] = _plan_total
                    st.session_state["current_order_plan_preview"] = _prev_list
                    st.session_state["order_plan_just_created"] = True
                    st.info(f"주문계획 생성됨: `{_plan_id}` | 예정금액: {_plan_total:,}원")
                    st.rerun()
                # MOCK 모드: preflight 수량/금액 체크용으로 저장
                elif order_mode == "MOCK":
                    st.session_state["mock_allocation_preview"] = _prev_list
                    _mock_qty = sum(int(r.get("quantity", 0) or 0) for r in _prev_list)
                    _mock_amt = sum(int(r.get("order_amount", 0) or 0) for r in _prev_list)
                    st.info(f"MOCK 미리보기 저장됨 — 총 {_mock_qty}주, 예상금액 {_mock_amt:,}원")
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
