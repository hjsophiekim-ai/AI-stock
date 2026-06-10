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
    get_broker_positions,
    get_positions_with_current_price,
    list_sell_policies,
    run_force_exit,
    run_force_sell_once,
    run_market_strength,
    run_sell_all,
    run_sell_order,
    sync_broker_to_local,
)
from mode_badge import render_mode_badge, render_mode_warning
from warning_box import no_profit_guarantee_notice, real_order_warning


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
    if st.button("KIS 계좌와 로컬 포지션 동기화", use_container_width=True):
        result = sync_broker_to_local(mode="mock")
        st.success(result.get("message", "동기화 완료")) if result.get("success") else st.error(result.get("message", "동기화 실패"))
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

# REAL 선택 시 경고 + 확인 체크박스
real_sell_confirmed = False
if sell_mode == "REAL":
    real_order_warning()
    real_sell_confirmed = st.checkbox(
        "실제 계좌에서 실제 매도 주문이 실행됨을 이해했습니다.",
        key="real_sell_confirm",
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
    sell_btn_disabled = (sell_mode == "REAL" and not real_sell_confirmed)
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
        if st.button(
            f"{sell_mode} — 전량 수동매도 (전체 {len(codes)}종목)",
            use_container_width=True,
            disabled=sell_btn_disabled,
            key="btn_sell_all",
        ):
            if sell_mode == "REAL":
                st.warning("REAL 전체 종목 일괄 매도 — 주의하세요.")
            with st.spinner(f"{sell_mode} 전체 종목 매도 실행 중 ({len(codes)}종목)..."):
                results = []
                for code in codes:
                    r = run_sell_order(
                        stock_code=code,
                        mode=sell_mode.lower(),
                        reason="manual_all",
                        stock_name=names.get(code, ""),
                    )
                    results.append(r)

            success_n = sum(1 for r in results if r.get("success"))
            fail_n = len(results) - success_n
            if success_n > 0:
                st.success(f"매도 완료: {success_n}개 성공, {fail_n}개 실패")
            else:
                st.error(f"모두 실패: {fail_n}개")

            # 결과 표시
            _diag_cols = [
                "stock_code", "stock_name", "requested_mode", "resolved_mode",
                "base_url", "key_type_used", "app_key_mode_valid",
                "mock_order_called", "real_order_called",
                "order_no", "rt_cd", "success", "rejected_reason",
            ]
            _diag_df = pd.DataFrame([{k: r.get(k, "") for k in _diag_cols} for r in results])
            st.dataframe(_diag_df, use_container_width=True)
            st.json(results)
else:
    st.info("수동매도할 보유종목이 없습니다.")

no_profit_guarantee_notice()
