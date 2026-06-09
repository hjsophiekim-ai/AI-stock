"""보유종목 및 매도감시 화면."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "components"))

from config_service import load_config, get_trade_mode
from trading_service import (
    get_broker_positions,
    get_positions_with_current_price,
    list_sell_policies,
    run_force_exit,
    run_force_sell_once,
    run_market_strength,
    run_sell_all,
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
mode = get_trade_mode(cfg)
render_mode_badge(mode)
render_mode_warning(mode)

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

st.divider()
st.subheader("수동매도")

sell_positions = local_pos if local_pos else broker_pos
codes = [str(p.get("stock_code", "")).zfill(6) for p in sell_positions if p.get("stock_code")]
names = {str(p.get("stock_code", "")).zfill(6): p.get("stock_name", "") for p in sell_positions}

if codes:
    sel_code = st.selectbox("종목 선택", codes, format_func=lambda c: f"{c} ({names.get(c, '')})")
    reason = st.selectbox("매도 사유", ["manual", "take_profit", "stop_loss", "force_exit"])
    sell_cols = st.columns(2)
    with sell_cols[0]:
        if st.button("선택 종목 수동매도", use_container_width=True):
            if mode == "REAL":
                real_order_warning()
            result = run_sell_all(sel_code, reason=reason, mode=mode.lower())
            st.success("매도 주문 완료") if result.get("success") else st.error(result.get("reason", result.get("message", "매도 실패")))
            st.json(result)
    with sell_cols[1]:
        if st.button("전량 수동매도", use_container_width=True):
            if mode == "REAL":
                real_order_warning()
            results = [run_sell_all(code, reason="manual_all", mode=mode.lower()) for code in codes]
            st.json(results)
else:
    st.info("수동매도할 보유종목이 없습니다.")

no_profit_guarantee_notice()
