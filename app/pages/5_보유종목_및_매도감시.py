"""보유종목 및 매도감시 화면."""

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

from config_service import load_config, get_trade_mode
from trading_service import (
    check_real_readiness,
    get_broker_positions,
    get_open_sell_orders,
    get_positions_with_current_price,
    list_sell_policies,
    run_bulk_sell_with_amend,
    run_force_exit,
    run_force_sell_once,
    run_market_strength,
    run_sell_all,
    run_sell_order,
    sync_broker_to_local,
)
from mode_badge import render_mode_badge, render_mode_warning
from warning_box import no_profit_guarantee_notice, real_order_warning


def _show_bulk_result(result: dict, is_dry_run: bool = False) -> None:
    """전량 일괄매도 결과 표시 헬퍼."""
    prefix = "[DRY-RUN] " if is_dry_run else ""
    success_n = result.get("success_count", 0)
    fail_n = result.get("fail_count", 0)
    amend_n = result.get("amend_count", 0)
    new_n = result.get("new_sell_count", 0)
    cancel_n = result.get("cancel_replace_count", 0)
    skip_n = result.get("skip_count", 0)
    oq_status = result.get("open_order_query_status", "")
    oq_msg = result.get("open_order_query_msg", "")

    # 미체결 조회 상태 표시
    if oq_status == "UNSUPPORTED":
        st.warning(
            f"⚠️ 미체결 주문 조회 미지원(UNSUPPORTED): {oq_msg or '해당업무가 제공되지 않습니다'}\n"
            f"정정 검증 불가 — 미체결 0건과 다릅니다."
        )
    elif oq_status == "ERROR":
        st.warning(f"⚠️ 미체결 주문 조회 오류(ERROR): {oq_msg}")

    # 차단된 경우
    if not result.get("success") and oq_status in ("UNSUPPORTED", "ERROR"):
        st.error(
            f"{prefix}미체결 조회 {oq_status} → 전량매도 차단\n"
            f"사유: {result.get('reason', '')}\n"
            f"'미체결 조회 실패해도 신규매도 진행' 체크 후 재시도 (MOCK 전용)."
        )
        return

    if result.get("success") or is_dry_run:
        st.success(
            f"{prefix}완료: 성공 {success_n}건 | "
            f"정정(AMEND) {amend_n}건 | 신규매도 {new_n}건 | "
            f"취소후재주문 {cancel_n}건 | 스킵 {skip_n}건 | 실패 {fail_n}건"
        )
    else:
        st.error(f"{prefix}실패 {fail_n}건 / 전체 {result.get('total', 0)}건")

    rows = result.get("results", [])
    if rows:
        _cols = [
            "stock_code", "stock_name", "operation_type",
            "quantity", "unfilled_qty", "old_price", "new_price",
            "original_order_no", "new_order_no",
            "mock_order_called", "real_order_called",
            "rt_cd", "msg", "success", "reason",
        ]
        _df = pd.DataFrame([{k: r.get(k, "") for k in _cols} for r in rows])
        st.dataframe(_df, use_container_width=True)

        if st.checkbox("원본 JSON 보기", key=f"bulk_json_{is_dry_run}"):
            st.json(rows)


def _position_rows(positions: list[dict]) -> list[dict]:
    rows = []
    for p in positions:
        avg = float(p.get("avg_price") or p.get("entry_price") or 0)
        cur = float(p.get("current_price", avg) or avg)
        qty = int(p.get("quantity", 0) or 0)
        target = float(p.get("target_price", avg * 1.02) or avg * 1.02)
        stop = float(p.get("stop_loss_price", avg * 0.97) or avg * 0.97)
        pnl_rate = (cur / avg - 1) * 100 if avg else 0
        target_reached = bool(cur >= target)
        manual = bool(p.get("manual_only", False))
        auto_sell = not manual and bool(p.get("auto_take_profit_enabled", True))
        rows.append({
            "종목코드": p.get("stock_code", ""),
            "종목명": p.get("stock_name", ""),
            "수량": qty,
            "평균단가": round(avg),
            "현재가": round(cur),
            "목표가(+2%)": round(target),
            "손절가(-3%)": round(stop),
            "최고가": round(float(p.get("trailing_high_price", 0) or 0)),
            "트레일링 스탑가": round(float(p.get("trailing_stop_price", 0) or 0)),
            "평가손익": round((cur - avg) * qty),
            "수익률": round(pnl_rate, 2),
            "목표가 도달 여부": target_reached,
            "자동매도 여부": auto_sell,
            "수동보유 여부": manual,
            "매도방식": p.get("sell_policy_name") or p.get("sell_policy_id", ""),
            "전략": p.get("strategy_name") or p.get("strategy_id", ""),
            "상태": p.get("status", "OPEN"),
            "주문번호": p.get("order_no", ""),
        })
    return rows


st.set_page_config(page_title="보유종목 및 매도감시", page_icon="📈", layout="wide")
st.title("보유종목 및 매도감시")
st.caption("+2% 익절 목표 전략입니다. 수익을 보장하지 않습니다.")

cfg = load_config()
config_mode = get_trade_mode(cfg)
render_mode_badge(config_mode)
render_mode_warning(config_mode)

st.warning("자동매도는 감시 프로세스가 실행 중일 때만 작동합니다. 앱을 닫거나 감시 루프가 꺼져 있으면 +2%에 도달해도 자동매도되지 않습니다.")

top_cols = st.columns(4)
with top_cols[0]:
    if st.button("KIS 계좌 새로고침", use_container_width=True):
        st.session_state["broker_refresh"] = True
with top_cols[1]:
    if st.button("KIS 계좌와 로컬 포지션 동기화 (누락 CLOSED처리)", use_container_width=True):
        result = sync_broker_to_local(mode=config_mode.lower(), apply=True, close_missing=True)
        if result.get("success"):
            closed_n = result.get("closed_count", 0)
            broker_n = result.get("broker_count", 0)
            open_n = result.get("open_count", broker_n)
            msg = f"동기화 완료: 브로커 {broker_n}개 → 로컬 OPEN {open_n}개"
            if closed_n:
                msg += f" ({closed_n}개 CLOSED 처리됨)"
            st.success(msg)
        else:
            st.error(result.get("message", "동기화 실패"))
        st.rerun()
with top_cols[2]:
    if st.button("시장강도 계산", use_container_width=True):
        st.session_state["market_strength"] = run_market_strength()
with top_cols[3]:
    if st.button("매도감시 루프 명령어 보기", use_container_width=True):
        st.code("python src/monitor_take_profit.py --mode mock --loop --sleep 5", language="bash")

if "market_strength" in st.session_state:
    ms = st.session_state["market_strength"]
    if ms.get("success"):
        st.info(f"현재 시장강도: {ms.get('level')} (score={ms.get('score')})")
    else:
        st.warning(ms.get("message", "시장강도 계산 실패"))

broker_pos = get_broker_positions()
local_pos = get_positions_with_current_price()

_open_local_count = sum(1 for p in local_pos if p.get("status", "OPEN") == "OPEN")
if broker_pos and _open_local_count != len(broker_pos):
    st.warning(
        f"⚠️ KIS 계좌 보유종목 수({len(broker_pos)})와 로컬 OPEN 종목 수({_open_local_count})가 다릅니다. "
        "'KIS 계좌와 로컬 포지션 동기화' 버튼으로 맞추세요."
    )

tab_local, tab_broker = st.tabs([f"로컬 positions.json ({len(local_pos)}개)", f"KIS 계좌 ({len(broker_pos)}개)"])

with tab_local:
    rows = _position_rows(local_pos)
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(
            df.style.apply(
                lambda r: ["background-color: #fff3cd" if bool(r.get("수동보유 여부")) and bool(r.get("목표가 도달 여부")) else "" for _ in r],
                axis=1,
            ),
            use_container_width=True,
        )
        st.caption("수동보유 종목이 목표가에 도달하면 노란색으로 표시되며 자동매도하지 않습니다.")
    else:
        st.info("로컬 positions.json 보유 종목이 없습니다.")

with tab_broker:
    if broker_pos:
        st.dataframe(pd.DataFrame(broker_pos), use_container_width=True)
    else:
        st.info("KIS 계좌 보유종목이 없거나 조회에 실패했습니다.")

st.divider()
st.subheader("매도정책 감시 실행")

policies = list_sell_policies()
policy_label_to_id = {p["name"]: p["id"] for p in policies}
selected_policy_label = st.radio("감시할 매도방식", list(policy_label_to_id.keys()), horizontal=True, index=0)
selected_policy_id = policy_label_to_id[selected_policy_label]

policy_cols = st.columns(4)
with policy_cols[0]:
    if st.button("+2% 기본 자동매도 감시 실행", use_container_width=True):
        result = run_force_sell_once(mode="mock", sell_policy_id="fixed_2pct", policy_override=True)
        st.json({k: v for k, v in result.items() if k != "rows"})
with policy_cols[1]:
    if st.button("강한 장 트레일링 감시 실행", use_container_width=True):
        result = run_force_sell_once(mode="mock", sell_policy_id="market_strength_trailing", policy_override=True)
        st.json({k: v for k, v in result.items() if k != "rows"})
with policy_cols[2]:
    if st.button("수동보유 종목 알림만 확인", use_container_width=True):
        result = run_force_sell_once(mode="mock", sell_policy_id="manual_hold", policy_override=True)
        st.json({k: v for k, v in result.items() if k != "rows"})
with policy_cols[3]:
    if st.button("선택 매도방식으로 1회 감시", use_container_width=True):
        result = run_force_sell_once(mode="mock", sell_policy_id=selected_policy_id, policy_override=True)
        st.json({k: v for k, v in result.items() if k != "rows"})

# ══════════════════════════════════════════════════════════════════════════════
# 수동매도 — 매도 주문 모드 선택
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("수동매도")

# 매도 모드 선택 (기본값: MOCK)
st.markdown("#### 매도 주문 모드 선택")
sell_mode = st.radio(
    "매도 모드",
    ["MOCK", "PAPER", "REAL"],
    index=0,
    horizontal=True,
    key="sell_order_mode",
    captions=[
        "모의투자 계좌 매도 — openapivts + KIS_MOCK_APP_KEY",
        "가상 매도 기록만 — API 호출 없음",
        "실전 계좌 매도 — 실제 자산 변동!",
    ],
)

# 모드별 진단 표시
if sell_mode in ("MOCK", "REAL"):
    try:
        from app.services.env_service import inject_to_os_env as _inj5
        _inj5()
    except Exception as _e5:
        st.warning(f"env_service import 실패: {_e5}")
    try:
        from trade_mode import get_expected_key_fingerprint_for_mode, get_base_url_for_mode
        _fp = get_expected_key_fingerprint_for_mode(sell_mode)
        _url = get_base_url_for_mode(sell_mode)
        _key_env = "KIS_MOCK_APP_KEY" if sell_mode == "MOCK" else "KIS_REAL_APP_KEY"
        _c1, _c2 = st.columns(2)
        with _c1:
            st.caption(f"API URL: `{_url}`")
            st.caption(f"환경변수: `{_key_env}`")
        with _c2:
            st.caption(f"appkey fingerprint: `{_fp}`")
        if _fp == "MISSING":
            st.error(f"⛔ {_key_env} 미설정 — {sell_mode} 매도 불가! .env 파일에 키를 등록하세요.")
        else:
            st.success(f"{sell_mode} appkey 설정 확인: `{_fp}`")
    except Exception as _e:
        st.warning(f"모드 진단 로드 실패: {_e}")

# REAL 선택 시 경고 + readiness 확인 + 확인 체크박스
real_sell_confirmed = False
_real_readiness_ok = True
if sell_mode == "REAL":
    real_order_warning()
    # REAL 계좌조회 준비상태 확인 (session_state 캐시)
    if "real_readiness" not in st.session_state:
        st.session_state["real_readiness"] = None
    _col_r1, _col_r2 = st.columns([3, 1])
    with _col_r1:
        if st.session_state["real_readiness"]:
            _r = st.session_state["real_readiness"]
            if _r.get("ready"):
                st.success(f"REAL 계좌조회 준비 완료 — {_r.get('message', '')}")
            else:
                st.error(
                    f"⛔ REAL 계좌조회 실패 — 매수/매도/정정 버튼 비활성화\n"
                    f"{_r.get('message', '')}\n"
                    f"real_api_diagnosis.py --all을 먼저 실행하세요."
                )
                _real_readiness_ok = False
    with _col_r2:
        if st.button("REAL 준비상태 확인", key="btn_real_readiness_check"):
            with st.spinner("REAL API 계좌조회 확인 중..."):
                st.session_state["real_readiness"] = check_real_readiness()
            st.rerun()
    if st.session_state["real_readiness"] and not st.session_state["real_readiness"].get("ready"):
        _real_readiness_ok = False

    real_sell_confirmed = st.checkbox(
        "실제 계좌에서 실제 매도 주문이 실행됨을 이해했습니다.",
        key="real_sell_confirm",
        disabled=not _real_readiness_ok,
    )
    if not real_sell_confirmed:
        st.error("REAL 매도를 진행하려면 위 체크박스를 먼저 선택하세요.")

st.divider()

sell_positions = local_pos if local_pos else broker_pos
codes = [str(p.get("stock_code", "")).zfill(6) for p in sell_positions if p.get("stock_code")]
names = {str(p.get("stock_code", "")).zfill(6): p.get("stock_name", "") for p in sell_positions}
quantities = {str(p.get("stock_code", "")).zfill(6): int(p.get("quantity", 0) or 0) for p in sell_positions}

if codes:
    sel_code = st.selectbox(
        "종목 선택",
        codes,
        format_func=lambda c: f"{c} ({names.get(c, '')}) — {quantities.get(c, 0)}주 보유",
    )
    reason = st.selectbox("매도 사유", ["manual", "take_profit", "stop_loss", "force_exit", "manual_all"])

    # 선택 종목 단일 매도
    sell_btn_disabled = (sell_mode == "REAL" and (not real_sell_confirmed or not _real_readiness_ok))
    sell_cols = st.columns(2)

    with sell_cols[0]:
        if st.button(
            f"{sell_mode} — 선택 종목 수동매도 (전량)",
            use_container_width=True,
            type="primary" if sell_mode == "MOCK" else "secondary",
            disabled=sell_btn_disabled,
            key="btn_sell_selected",
        ):
            with st.spinner(f"{sell_mode} 매도 주문 실행 중..."):
                result = run_sell_order(
                    stock_code=sel_code,
                    mode=sell_mode.lower(),
                    reason=reason,
                    stock_name=names.get(sel_code, ""),
                )

            _success = result.get("success")
            if _success:
                st.success(f"매도 성공: 주문번호 {result.get('order_no', '')}")
            else:
                st.error(f"매도 실패: {result.get('rejected_reason') or result.get('reason', '알 수 없음')}")

            # 진단 컬럼 표시
            _diag_cols = [
                "requested_mode", "resolved_mode", "base_url", "token_url",
                "key_type_used", "app_key_mode_valid",
                "mock_order_called", "real_order_called",
                "order_no", "rt_cd", "msg", "tr_id",
                "success", "rejected_reason",
            ]
            _diag = {k: result.get(k, "") for k in _diag_cols}
            st.json(_diag)
            st.json(result)

    with sell_cols[1]:
        # 전량 일괄매도는 아래 전용 섹션에서 처리
        st.caption("↓ 아래 '전량 일괄매도' 버튼 사용")
else:
    st.info("수동매도할 보유종목이 없습니다.")

# ══════════════════════════════════════════════════════════════════════════════
# 전량 일괄매도 — 미체결 주문 정정 포함
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("전량 일괄매도 (미체결 정정 포함)")

_bulk_sell_disabled = (sell_mode == "REAL" and (not real_sell_confirmed or not _real_readiness_ok))

# 옵션 체크박스
_opt_cols = st.columns(4)
with _opt_cols[0]:
    opt_check_open = st.checkbox("미체결 매도 주문 먼저 조회", value=True, key="opt_check_open_orders")
with _opt_cols[1]:
    opt_amend = st.checkbox("미체결 주문은 현재가 기준 정정", value=True, key="opt_amend_unfilled")
with _opt_cols[2]:
    opt_cancel_replace = st.checkbox("정정 실패 시 취소 후 재매도", value=True, key="opt_cancel_replace")
with _opt_cols[3]:
    opt_proceed_without_check = st.checkbox(
        "미체결 조회 실패해도 KIS 보유수량 기준 신규매도 진행",
        value=(sell_mode == "MOCK"),
        key="opt_proceed_without_open_order_check",
        help="MOCK: KIS 모의투자는 미체결 조회 미지원(UNSUPPORTED) → 자동 진행. REAL은 항상 차단.",
        disabled=(sell_mode == "REAL"),
    )

# 미체결 주문 조회 버튼
_bulk_btn_cols = st.columns(3)
with _bulk_btn_cols[0]:
    if st.button("🔍 미체결 주문 조회", use_container_width=True, key="btn_check_open_orders"):
        with st.spinner("KIS 미체결 매도 주문 조회 중..."):
            oo_result = get_open_sell_orders(mode=sell_mode.lower())
        oo_qstatus = oo_result.get("query_status", "OK")
        oo_qmsg = oo_result.get("query_msg", "")
        if oo_qstatus == "UNSUPPORTED":
            st.warning(
                f"⚠️ 미체결 조회 미지원(UNSUPPORTED): {oo_qmsg or '해당업무가 제공되지 않습니다'}\n"
                f"정정 검증 불가 — 미체결 0건과 다릅니다."
            )
            st.session_state["open_sell_orders"] = []
            st.session_state["open_order_query_status"] = "UNSUPPORTED"
        elif oo_qstatus == "ERROR":
            st.error(f"미체결 조회 오류(ERROR): {oo_qmsg}")
            st.session_state["open_sell_orders"] = []
            st.session_state["open_order_query_status"] = "ERROR"
        else:
            oo_list = oo_result.get("orders", [])
            if oo_list:
                _oo_cols = ["stock_code", "stock_name", "order_no", "original_order_no",
                            "order_qty", "unfilled_qty", "order_price", "order_time"]
                st.success(f"미체결 매도 주문 {len(oo_list)}건 조회됨")
                st.dataframe(
                    pd.DataFrame([{k: o.get(k, "") for k in _oo_cols} for o in oo_list]),
                    use_container_width=True,
                )
                st.session_state["open_sell_orders"] = oo_list
                st.session_state["open_order_query_status"] = "OK"
            else:
                st.info("미체결 매도 주문 없음 (정상 0건)")
                st.session_state["open_sell_orders"] = []
                st.session_state["open_order_query_status"] = "OK"

# 이전 조회 결과 표시
if st.session_state.get("open_sell_orders"):
    _oo_list = st.session_state["open_sell_orders"]
    st.caption(f"최근 조회: 미체결 매도 주문 {len(_oo_list)}건")
    _oo_cols2 = ["stock_code", "stock_name", "unfilled_qty", "order_price", "order_time"]
    _oo_preview = [{k: o.get(k, "") for k in _oo_cols2} for o in _oo_list]
    if _oo_preview:
        st.dataframe(pd.DataFrame(_oo_preview), use_container_width=True)

with _bulk_btn_cols[1]:
    if st.button(
        f"⚡ {sell_mode} — 미체결 정정 후 전량 일괄매도",
        use_container_width=True,
        type="primary" if sell_mode == "MOCK" else "secondary",
        disabled=_bulk_sell_disabled,
        key="btn_bulk_sell_amend",
    ):
        if sell_mode == "REAL":
            st.warning("REAL 전체 종목 일괄 매도 + 정정주문 — 실제 주문 실행됩니다!")
        with st.spinner(f"{sell_mode} 미체결 정정 후 전량 일괄매도 실행 중..."):
            bulk_result = run_bulk_sell_with_amend(
                mode=sell_mode.lower(),
                check_open_orders=opt_check_open,
                amend_unfilled=opt_amend,
                cancel_replace_if_amend_fails=opt_cancel_replace,
                dry_run=False,
                proceed_without_open_order_check=opt_proceed_without_check,
            )
        _show_bulk_result(bulk_result)

with _bulk_btn_cols[2]:
    if st.button(
        f"🔍 {sell_mode} — DRY-RUN (정정 예정 확인만)",
        use_container_width=True,
        disabled=_bulk_sell_disabled,
        key="btn_bulk_sell_dryrun",
    ):
        with st.spinner("DRY-RUN 실행 중 (실제 주문 없음)..."):
            dry_result = run_bulk_sell_with_amend(
                mode=sell_mode.lower(),
                check_open_orders=opt_check_open,
                amend_unfilled=opt_amend,
                cancel_replace_if_amend_fails=opt_cancel_replace,
                dry_run=True,
                proceed_without_open_order_check=opt_proceed_without_check,
            )
        _show_bulk_result(dry_result, is_dry_run=True)

no_profit_guarantee_notice()
