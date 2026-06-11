"""보유종목 및 매도감시 화면 — MOCK/REAL/PAPER 탭 분리."""

import json
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
    get_broker_positions_result,
    get_open_sell_orders,
    get_positions_with_current_price,
    list_sell_policies,
    run_bulk_sell_with_amend,
    run_force_sell_once,
    run_market_strength,
    run_sell_order,
    sync_broker_to_local,
)
from mode_badge import render_mode_badge, render_mode_warning
from warning_box import no_profit_guarantee_notice, real_order_warning


def _position_rows(positions: list) -> list:
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
            "평가손익": round((cur - avg) * qty),
            "수익률": round(pnl_rate, 2),
            "목표가 도달": target_reached,
            "자동매도": auto_sell,
            "수동보유": manual,
            "매도방식": p.get("sell_policy_name") or p.get("sell_policy_id", ""),
            "전략": p.get("strategy_name") or p.get("strategy_id", ""),
            "상태": p.get("status", "OPEN"),
            "주문번호": p.get("order_no", ""),
        })
    return rows


def _show_bulk_result(result: dict, is_dry_run: bool = False) -> None:
    prefix = "[DRY-RUN] " if is_dry_run else ""
    success_n = result.get("success_count", 0)
    fail_n = result.get("fail_count", 0)
    amend_n = result.get("amend_count", 0)
    new_n = result.get("new_sell_count", 0)
    cancel_n = result.get("cancel_replace_count", 0)
    skip_n = result.get("skip_count", 0)
    oq_status = result.get("open_order_query_status", "")
    oq_msg = result.get("open_order_query_msg", "")

    if oq_status == "UNSUPPORTED":
        st.warning(f"⚠️ 미체결 주문 조회 미지원(UNSUPPORTED): {oq_msg or '해당업무가 제공되지 않습니다'}")
    elif oq_status == "ERROR":
        st.warning(f"⚠️ 미체결 주문 조회 오류(ERROR): {oq_msg}")

    if not result.get("success") and oq_status in ("UNSUPPORTED", "ERROR"):
        st.error(f"{prefix}미체결 조회 {oq_status} → 전량매도 차단\n사유: {result.get('reason', '')}")
        return

    if result.get("success") or is_dry_run:
        st.success(f"{prefix}완료: 성공 {success_n}건 | 정정 {amend_n}건 | 신규매도 {new_n}건 | 취소후재주문 {cancel_n}건 | 스킵 {skip_n}건 | 실패 {fail_n}건")
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
        st.dataframe(pd.DataFrame([{k: r.get(k, "") for k in _cols} for r in rows]), use_container_width=True)


def _render_sell_tabs(mode_str: str, local_pos: list, broker_pos: list, real_readiness_ok: bool = True) -> None:
    """매도 탭 — 2% 자동매도 / 트레일링 매도 / 수동매도 / 일괄매도 탭 구조."""
    real_sell_confirmed = False
    if mode_str == "REAL":
        real_sell_confirmed = st.checkbox(
            "실제 계좌에서 실제 매도 주문이 실행됨을 이해했습니다.",
            key=f"real_sell_confirm_{mode_str}",
            disabled=not real_readiness_ok,
        )
        if not real_sell_confirmed:
            st.error("REAL 매도를 진행하려면 위 체크박스를 먼저 선택하세요.")

    sell_btn_disabled = (mode_str == "REAL" and (not real_sell_confirmed or not real_readiness_ok))

    # 미체결 조회 옵션 (탭 공통)
    _opt_cols = st.columns(4)
    with _opt_cols[0]:
        opt_check_open = st.checkbox("미체결 매도 주문 먼저 조회", value=True, key=f"opt_check_{mode_str}")
    with _opt_cols[1]:
        opt_amend = st.checkbox("미체결 주문은 현재가 기준 정정", value=True, key=f"opt_amend_{mode_str}")
    with _opt_cols[2]:
        opt_cancel_replace = st.checkbox("정정 실패 시 취소 후 재매도", value=True, key=f"opt_cancel_{mode_str}")
    with _opt_cols[3]:
        opt_proceed = st.checkbox(
            "미체결 조회 실패해도 신규매도 진행",
            value=(mode_str == "MOCK"),
            key=f"opt_proceed_{mode_str}",
            disabled=(mode_str == "REAL"),
        )

    _stab_2pct, _stab_trail, _stab_manual, _stab_bulk = st.tabs([
        "2% 자동매도",
        "트레일링 매도",
        "수동매도",
        "일괄매도",
    ])

    with _stab_2pct:
        st.caption("현재 보유종목 중 +2% 목표가 도달 종목 자동매도 1회 실행")
        if st.button(
            f"+2% 자동매도 감시 1회 실행 ({mode_str})",
            key=f"sell2pct_{mode_str}",
            use_container_width=True,
            type="primary",
        ):
            with st.spinner("+2% 목표가 도달 감시 실행 중..."):
                result = run_force_sell_once(mode=mode_str.lower(), sell_policy_id="fixed_2pct", policy_override=True)
            if result.get("success"):
                st.success(f"완료: {result.get('message', '')}")
            else:
                st.error(f"실패: {result.get('message', '알 수 없음')}")
            st.json({k: v for k, v in result.items() if k != "rows"})

    with _stab_trail:
        st.caption("시장강도 기반 트레일링 매도 감시 1회 실행")
        if st.button(
            f"트레일링 매도 감시 1회 실행 ({mode_str})",
            key=f"sell_trail_{mode_str}",
            use_container_width=True,
            type="primary",
        ):
            with st.spinner("트레일링 매도 감시 실행 중..."):
                result = run_force_sell_once(mode=mode_str.lower(), sell_policy_id="market_strength_trailing", policy_override=True)
            if result.get("success"):
                st.success(f"완료: {result.get('message', '')}")
            else:
                st.error(f"실패: {result.get('message', '알 수 없음')}")
            st.json({k: v for k, v in result.items() if k != "rows"})

    with _stab_manual:
        st.caption("수동보유(manual_hold) 종목 알림 확인 + 개별 종목 수동매도")

        if st.button(
            f"수동보유 종목 알림 확인 ({mode_str})",
            key=f"sell_manual_mon_{mode_str}",
            use_container_width=True,
        ):
            with st.spinner("수동보유 종목 확인 중..."):
                result = run_force_sell_once(mode=mode_str.lower(), sell_policy_id="manual_hold", policy_override=True)
            if result.get("success"):
                st.success(f"완료: {result.get('message', '')}")
            else:
                st.error(f"실패: {result.get('message', '알 수 없음')}")
            st.json({k: v for k, v in result.items() if k != "rows"})

        st.divider()

        sell_positions = local_pos if local_pos else broker_pos
        codes = [str(p.get("stock_code", "")).zfill(6) for p in sell_positions if p.get("stock_code")]
        names = {str(p.get("stock_code", "")).zfill(6): p.get("stock_name", "") for p in sell_positions}
        quantities = {str(p.get("stock_code", "")).zfill(6): int(p.get("quantity", 0) or 0) for p in sell_positions}

        if not codes:
            st.info("수동매도할 보유종목이 없습니다. (로컬 포지션 및 브로커 계좌 모두 없음)")
        else:
            sel_code = st.selectbox(
                "종목 선택",
                codes,
                format_func=lambda c: f"{c} ({names.get(c, '')}) — {quantities.get(c, 0)}주 보유",
                key=f"sel_code_{mode_str}",
            )
            reason = st.selectbox(
                "매도 사유",
                ["manual", "take_profit", "stop_loss", "force_exit"],
                key=f"sell_reason_{mode_str}",
            )
            if st.button(
                f"{mode_str} — 선택 종목 수동매도 (전량)",
                use_container_width=True,
                type="primary" if mode_str == "MOCK" else "secondary",
                disabled=sell_btn_disabled,
                key=f"btn_sell_single_{mode_str}",
            ):
                with st.spinner(f"{mode_str} 매도 주문 실행 중..."):
                    result = run_sell_order(
                        stock_code=sel_code,
                        mode=mode_str.lower(),
                        reason=reason,
                        stock_name=names.get(sel_code, ""),
                    )
                if result.get("success"):
                    st.success(f"매도 성공: 주문번호 {result.get('order_no', '')}")
                else:
                    st.error(f"매도 실패: {result.get('rejected_reason') or result.get('reason', '알 수 없음')}")
                st.json({k: result.get(k, "") for k in [
                    "requested_mode", "resolved_mode", "base_url", "mock_order_called", "real_order_called",
                    "order_no", "rt_cd", "msg", "success", "rejected_reason",
                ]})

    with _stab_bulk:
        st.caption(f"전 종목 일괄매도 및 DRY-RUN 테스트. {mode_str} 포지션 파일 기준.")

        _bcols = st.columns(2)
        with _bcols[0]:
            if st.button(
                f"⚡ {mode_str} 계좌 일괄매도",
                use_container_width=True,
                type="primary" if mode_str == "MOCK" else "secondary",
                disabled=sell_btn_disabled,
                key=f"btn_bulk_sell_{mode_str}",
            ):
                if mode_str == "REAL":
                    st.warning("REAL 전체 종목 일괄 매도 — 실제 주문 실행됩니다!")
                with st.spinner(f"{mode_str} 미체결 정정 후 전량 일괄매도 실행 중..."):
                    bulk_result = run_bulk_sell_with_amend(
                        mode=mode_str.lower(),
                        check_open_orders=opt_check_open,
                        amend_unfilled=opt_amend,
                        cancel_replace_if_amend_fails=opt_cancel_replace,
                        dry_run=False,
                        proceed_without_open_order_check=opt_proceed,
                    )
                _show_bulk_result(bulk_result)

        with _bcols[1]:
            if st.button(
                f"🔍 {mode_str} DRY-RUN (주문없음)",
                use_container_width=True,
                key=f"btn_dryrun_{mode_str}",
            ):
                with st.spinner("DRY-RUN 실행 중 (실제 주문 없음)..."):
                    dry_result = run_bulk_sell_with_amend(
                        mode=mode_str.lower(),
                        check_open_orders=opt_check_open,
                        amend_unfilled=opt_amend,
                        cancel_replace_if_amend_fails=opt_cancel_replace,
                        dry_run=True,
                        proceed_without_open_order_check=opt_proceed,
                    )
                _show_bulk_result(dry_result, is_dry_run=True)

        st.divider()
        if st.button(
            f"🔍 미체결 주문 조회 ({mode_str})",
            use_container_width=True,
            key=f"btn_open_orders_{mode_str}",
        ):
            with st.spinner("KIS 미체결 매도 주문 조회 중..."):
                oo_result = get_open_sell_orders(mode=mode_str.lower())
            oo_qstatus = oo_result.get("query_status", "OK")
            if oo_qstatus == "UNSUPPORTED":
                st.warning(f"⚠️ 미체결 조회 미지원(UNSUPPORTED): {oo_result.get('query_msg', '')}")
            elif oo_qstatus == "ERROR":
                st.error(f"미체결 조회 오류: {oo_result.get('query_msg', '')}")
            else:
                oo_list = oo_result.get("orders", [])
                if oo_list:
                    st.success(f"미체결 매도 주문 {len(oo_list)}건")
                    _oo_cols = ["stock_code", "stock_name", "order_no", "unfilled_qty", "order_price", "order_time"]
                    st.dataframe(pd.DataFrame([{k: o.get(k, "") for k in _oo_cols} for o in oo_list]), use_container_width=True)
                else:
                    st.info("미체결 매도 주문 없음 (0건)")


# ══════════════════════════════════════════════════════════════════════════════
# Page setup
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="보유종목 및 매도감시", page_icon="📈", layout="wide")
st.title("보유종목 및 매도감시")
st.caption("+2% 익절 목표 전략입니다. 수익을 보장하지 않습니다.")
st.warning("자동매도는 감시 프로세스가 실행 중일 때만 작동합니다.")

cfg = load_config()
config_mode = get_trade_mode(cfg)

tab_mock, tab_real, tab_paper, tab_diag = st.tabs([
    "MOCK 계좌 보기",
    "REAL 계좌 보기",
    "PAPER 가상 포지션",
    "전체 이력/진단",
])

# ══════════════════════════════════════════════════════════════════════════════
# MOCK TAB
# ══════════════════════════════════════════════════════════════════════════════
with tab_mock:
    st.subheader("MOCK 모의투자 계좌")
    st.caption("한국투자증권 모의투자 계좌 기준 보유종목입니다. API URL: openapivts | key: KIS_MOCK_APP_KEY")
    st.caption("포지션 파일: data/positions_mock.json | 브로커 캐시: data/broker_positions_mock.json")

    _mock_top = st.columns(4)
    with _mock_top[0]:
        if st.button("KIS MOCK 계좌 새로고침", use_container_width=True, key="mock_refresh"):
            with st.spinner("KIS MOCK 계좌 조회 중..."):
                _refresh_result = get_broker_positions_result(mode="mock")
            if _refresh_result.get("success"):
                st.session_state["mock_broker_pos"] = _refresh_result.get("positions", [])
                st.session_state["mock_broker_error"] = None
            else:
                st.session_state["mock_broker_pos"] = []
                st.session_state["mock_broker_error"] = _refresh_result.get("message", "알 수 없는 오류")
            st.rerun()
    with _mock_top[1]:
        if st.button("MOCK 계좌 동기화 (누락 CLOSED처리)", use_container_width=True, key="mock_sync"):
            result = sync_broker_to_local(mode="mock", apply=True, close_missing=True)
            if result.get("success"):
                st.success(f"MOCK 동기화 완료: 브로커 {result.get('broker_count', 0)}개 → 로컬 OPEN {result.get('open_count', 0)}개")
            else:
                st.error(result.get("message", "MOCK 동기화 실패"))
            st.rerun()
    with _mock_top[2]:
        if st.button("시장강도 계산", use_container_width=True, key="mock_market_strength"):
            st.session_state["market_strength"] = run_market_strength()
    with _mock_top[3]:
        if st.button("매도감시 루프 명령어", use_container_width=True, key="mock_cmd"):
            st.code("python src/monitor_take_profit.py --mode mock --loop --sleep 5", language="bash")

    if "market_strength" in st.session_state:
        ms = st.session_state["market_strength"]
        if ms.get("success"):
            st.info(f"현재 시장강도: {ms.get('level')} (score={ms.get('score')})")

    # Load MOCK positions
    _mock_local = get_positions_with_current_price(mode="mock")
    _mock_open = [p for p in _mock_local if p.get("status", "OPEN") == "OPEN"]
    if "mock_broker_pos" not in st.session_state:
        st.session_state["mock_broker_pos"] = []
    if "mock_broker_error" not in st.session_state:
        st.session_state["mock_broker_error"] = None
    _mock_broker = st.session_state.get("mock_broker_pos", [])
    _mock_broker_error = st.session_state.get("mock_broker_error")

    _mock_open_count = len(_mock_open)
    _mock_broker_count = len(_mock_broker)
    st.caption(f"로컬 OPEN: {_mock_open_count}개 | KIS MOCK 계좌 (마지막 조회): {_mock_broker_count}개")

    if _mock_broker_error:
        st.error(f"KIS MOCK 계좌 조회 오류: {_mock_broker_error}")
    elif _mock_broker_count and _mock_open_count != _mock_broker_count:
        st.warning(f"⚠️ KIS MOCK 계좌 {_mock_broker_count}개 vs 로컬 OPEN {_mock_open_count}개 — 'MOCK 계좌 동기화' 버튼으로 맞추세요.")

    _mock_sub1, _mock_sub2, _mock_sub3 = st.tabs([
        f"MOCK 로컬 포지션 ({_mock_open_count}개)",
        f"KIS MOCK 계좌 원본 ({_mock_broker_count}개)",
        "MOCK 계좌 비교",
    ])
    with _mock_sub1:
        rows = _position_rows(_mock_open)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        else:
            st.info("data/positions_mock.json OPEN 포지션 없음")
    with _mock_sub2:
        if _mock_broker:
            st.dataframe(pd.DataFrame(_mock_broker), use_container_width=True)
        else:
            st.info("KIS MOCK 계좌 조회 결과 없음. 'KIS MOCK 계좌 새로고침' 버튼을 눌러 조회하세요.")
    with _mock_sub3:
        _mock_codes_local = {p.get("stock_code") for p in _mock_open}
        _mock_codes_broker = {p.get("stock_code") for p in _mock_broker}
        _only_local = _mock_codes_local - _mock_codes_broker
        _only_broker = _mock_codes_broker - _mock_codes_local
        if _only_local:
            st.warning(f"로컬 ONLY (KIS에 없음): {sorted(_only_local)}")
        if _only_broker:
            st.warning(f"KIS ONLY (로컬에 없음): {sorted(_only_broker)}")
        if not _only_local and not _only_broker and _mock_broker:
            st.success("MOCK 로컬과 KIS MOCK 계좌가 일치합니다.")

    st.divider()
    st.subheader("MOCK 매도 감시")
    _render_sell_tabs("MOCK", _mock_open, _mock_broker)


# ══════════════════════════════════════════════════════════════════════════════
# REAL TAB
# ══════════════════════════════════════════════════════════════════════════════
with tab_real:
    real_order_warning()
    st.subheader("REAL 실전투자 계좌")
    st.caption("한국투자증권 실전 계좌 기준 보유종목입니다. API URL: openapi | key: KIS_REAL_APP_KEY")
    st.caption("포지션 파일: data/positions_real.json | 브로커 캐시: data/broker_positions_real.json")

    # REAL readiness check
    if "real_readiness_sell" not in st.session_state:
        st.session_state["real_readiness_sell"] = None
    _real_readiness_ok = False
    _real_col1, _real_col2 = st.columns([3, 1])
    with _real_col1:
        if st.session_state["real_readiness_sell"]:
            _r = st.session_state["real_readiness_sell"]
            if _r.get("ready"):
                st.success(f"REAL 준비 완료 — {_r.get('message', '')}")
                _real_readiness_ok = True
            else:
                st.error(f"⛔ REAL 준비 실패 — 매도/정정 버튼 비활성화\n{_r.get('message', '')}")
        else:
            st.info("REAL 준비상태 확인 버튼을 눌러주세요.")
    with _real_col2:
        if st.button("REAL 준비상태 확인", key="btn_real_readiness_sell"):
            with st.spinner("REAL API 계좌조회 확인 중..."):
                st.session_state["real_readiness_sell"] = check_real_readiness()
            st.rerun()

    # Safety flag
    import json as _json5
    _sflag_path5 = PROJECT_ROOT / "reports" / "real_order_safety_flag.json"
    if _sflag_path5.exists():
        try:
            _sflag5 = _json5.loads(_sflag_path5.read_text(encoding="utf-8"))
            if _sflag5.get("blocked"):
                st.error(f"⛔ 이전 실전 주문 미검증: {_sflag5.get('order_no','?')} — reports/real_order_safety_flag.json 삭제 후 재시도")
                _real_readiness_ok = False
        except Exception:
            pass

    _real_top = st.columns(4)
    with _real_top[0]:
        if st.button("KIS REAL 계좌 새로고침", use_container_width=True, key="real_refresh", disabled=not _real_readiness_ok):
            with st.spinner("KIS REAL 계좌 조회 중..."):
                _real_refresh_result = get_broker_positions_result(mode="real")
            if _real_refresh_result.get("success"):
                st.session_state["real_broker_pos"] = _real_refresh_result.get("positions", [])
                st.session_state["real_broker_error"] = None
            else:
                st.session_state["real_broker_pos"] = []
                st.session_state["real_broker_error"] = _real_refresh_result.get("message", "알 수 없는 오류")
            st.rerun()
    with _real_top[1]:
        if st.button("REAL 계좌 동기화 (조회만)", use_container_width=True, key="real_sync_dry", disabled=not _real_readiness_ok):
            result = sync_broker_to_local(mode="real", apply=False, close_missing=False)
            st.json(result)
    with _real_top[2]:
        if st.button("REAL 계좌 동기화 (적용)", use_container_width=True, key="real_sync_apply", disabled=not _real_readiness_ok):
            _rc2a = st.checkbox("REAL 포지션 파일 변경 확인", key="real_sync_confirm2")
            if _rc2a:
                result = sync_broker_to_local(mode="real", apply=True, close_missing=True)
                if result.get("success"):
                    st.success(f"REAL 동기화 완료: {result.get('broker_count', 0)}개 → OPEN {result.get('open_count', 0)}개")
                else:
                    st.error(result.get("message", "REAL 동기화 실패"))
                st.rerun()
    with _real_top[3]:
        if st.button("매도감시 루프 명령어", use_container_width=True, key="real_cmd"):
            st.code("python src/monitor_take_profit.py --mode real --loop --sleep 5", language="bash")

    # Load REAL positions
    _real_local = get_positions_with_current_price(mode="real")
    _real_open = [p for p in _real_local if p.get("status", "OPEN") == "OPEN"]
    if "real_broker_pos" not in st.session_state:
        st.session_state["real_broker_pos"] = []
    if "real_broker_error" not in st.session_state:
        st.session_state["real_broker_error"] = None
    _real_broker = st.session_state.get("real_broker_pos", [])
    _real_broker_error = st.session_state.get("real_broker_error")

    _real_open_count = len(_real_open)
    _real_broker_count = len(_real_broker)
    st.caption(f"로컬 OPEN: {_real_open_count}개 | KIS REAL 계좌 (마지막 조회): {_real_broker_count}개")

    if _real_broker_error:
        st.error(f"KIS REAL 계좌 조회 오류: {_real_broker_error}")
    elif _real_broker_count and _real_open_count != _real_broker_count:
        st.warning(f"⚠️ KIS REAL 계좌 {_real_broker_count}개 vs 로컬 OPEN {_real_open_count}개")

    _real_sub1, _real_sub2, _real_sub3 = st.tabs([
        f"REAL 로컬 포지션 ({_real_open_count}개)",
        f"KIS REAL 계좌 원본 ({_real_broker_count}개)",
        "REAL 계좌 비교",
    ])
    with _real_sub1:
        rows = _position_rows(_real_open)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        else:
            st.info("data/positions_real.json OPEN 포지션 없음")
    with _real_sub2:
        if _real_broker:
            st.dataframe(pd.DataFrame(_real_broker), use_container_width=True)
        else:
            st.info("KIS REAL 계좌 조회 결과 없음. 'KIS REAL 계좌 새로고침' 버튼을 눌러 조회하세요.")
    with _real_sub3:
        _real_codes_local = {p.get("stock_code") for p in _real_open}
        _real_codes_broker = {p.get("stock_code") for p in _real_broker}
        _only_real_local = _real_codes_local - _real_codes_broker
        _only_real_broker = _real_codes_broker - _real_codes_local
        if _only_real_local:
            st.warning(f"로컬 ONLY: {sorted(_only_real_local)}")
        if _only_real_broker:
            st.warning(f"KIS ONLY: {sorted(_only_real_broker)}")
        if not _only_real_local and not _only_real_broker and _real_broker:
            st.success("REAL 로컬과 KIS REAL 계좌가 일치합니다.")

    st.divider()
    st.subheader("REAL 매도 감시")
    _render_sell_tabs("REAL", _real_open, _real_broker, real_readiness_ok=_real_readiness_ok)


# ══════════════════════════════════════════════════════════════════════════════
# PAPER TAB
# ══════════════════════════════════════════════════════════════════════════════
with tab_paper:
    st.subheader("PAPER 가상 포지션")
    st.caption("실제 API 호출 없음. 포지션 파일: data/positions_paper.json")

    _paper_local = get_positions_with_current_price(mode="paper")
    _paper_open = [p for p in _paper_local if p.get("status", "OPEN") == "OPEN"]
    st.caption(f"PAPER OPEN: {len(_paper_open)}개")

    rows = _position_rows(_paper_open)
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("data/positions_paper.json OPEN 포지션 없음")

    st.divider()
    st.subheader("PAPER 매도")
    _render_sell_tabs("PAPER", _paper_open, [])


# ══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTICS TAB
# ══════════════════════════════════════════════════════════════════════════════
with tab_diag:
    st.subheader("전체 이력/진단")
    st.caption("읽기 전용. 주문/매도에는 사용되지 않습니다.")

    _diag_files = {
        "MOCK 포지션 (positions_mock.json)": PROJECT_ROOT / "data" / "positions_mock.json",
        "REAL 포지션 (positions_real.json)": PROJECT_ROOT / "data" / "positions_real.json",
        "PAPER 포지션 (positions_paper.json)": PROJECT_ROOT / "data" / "positions_paper.json",
        "Legacy 포지션 (positions.json)": PROJECT_ROOT / "data" / "positions.json",
        "MOCK 브로커 캐시": PROJECT_ROOT / "data" / "broker_positions_mock.json",
        "REAL 브로커 캐시": PROJECT_ROOT / "data" / "broker_positions_real.json",
        "Legacy 브로커 캐시": PROJECT_ROOT / "data" / "broker_positions.json",
    }
    for label, path in _diag_files.items():
        with st.expander(f"{label} {'✅' if path.exists() else '❌'}"):
            if path.exists():
                try:
                    _data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(_data, dict):
                        st.caption(f"{len(_data)}개 항목")
                        st.json({k: v for k, v in list(_data.items())[:5]})
                        if len(_data) > 5:
                            st.caption(f"... (총 {len(_data)}개, 처음 5개만 표시)")
                    elif isinstance(_data, list):
                        st.caption(f"{len(_data)}개 항목")
                        st.json(_data[:5])
                        if len(_data) > 5:
                            st.caption(f"... (총 {len(_data)}개, 처음 5개만 표시)")
                    else:
                        st.json(_data)
                except Exception as e:
                    st.error(f"읽기 실패: {e}")
            else:
                st.info(f"파일 없음: {path}")

no_profit_guarantee_notice()
