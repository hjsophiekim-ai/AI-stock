"""Shared Streamlit table renderers."""

from typing import List

import pandas as pd
import streamlit as st


def render_candidates_table(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        st.info("표시할 후보 종목이 없습니다.")
        return
    display_cols = {
        "rank": "순위",
        "final_rank": "순위",
        "stock_code": "종목코드",
        "ticker": "종목코드",
        "stock_name": "종목명",
        "name": "종목명",
        "close": "현재가",
        "current_price": "현재가",
        "buy_allowed": "매수허용",
        "disclosure_score": "공시점수",
        "disclosure_risk_score": "공시위험",
        "disclosure_summary": "공시요약",
        "disclosure_risk_reason": "위험사유",
        "final_score": "최종점수",
        "probability_2pct": "상승확률",
        "prediction_score": "예측점수",
        "trading_value": "거래대금",
        "exclusion_reason": "제외사유",
    }
    seen = set()
    cols = []
    for col, label in display_cols.items():
        if col in df.columns and label not in seen:
            cols.append(col)
            seen.add(label)
    view = df[cols].rename(columns={c: display_cols[c] for c in cols})
    st.dataframe(view, use_container_width=True)


def render_positions_table(positions: List[dict]) -> None:
    if not positions:
        st.info("보유 중인 종목이 없습니다.")
        return
    rows = []
    for pos in positions:
        ep = float(pos.get("entry_price", 0) or 0)
        cp = float(pos.get("current_price", ep) or ep)
        tp = float(pos.get("target_price", ep * 1.02) or ep * 1.02)
        sp = float(pos.get("stop_loss_price", ep * 0.97) or ep * 0.97)
        qty = int(pos.get("quantity", 0) or 0)
        pnl = (cp - ep) * qty
        pnl_rate = (cp / ep - 1) * 100 if ep > 0 else 0
        rows.append({
            "종목코드": pos.get("stock_code", ""),
            "종목명": pos.get("stock_name", ""),
            "수량": qty,
            "매수가": round(ep),
            "현재가": round(cp),
            "목표가(+2%)": round(tp),
            "손절가(-3%)": round(sp),
            "최고가": round(float(pos.get("trailing_high_price", 0) or 0)),
            "트레일링 스탑가": round(float(pos.get("trailing_stop_price", 0) or 0)),
            "평가손익": round(pnl),
            "수익률": round(pnl_rate, 2),
            "매도방식": pos.get("sell_policy_name", pos.get("sell_policy_id", "")),
            "자동매도": not bool(pos.get("manual_only", False)),
            "수동보유": bool(pos.get("manual_only", False)),
            "주문번호": pos.get("order_no", "-"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def render_order_results_table(results: List[dict]) -> None:
    if not results:
        st.info("주문 결과가 없습니다.")
        return
    cols = [
        "requested_mode", "resolved_mode", "stock_code", "stock_name", "quantity",
        "price", "order_price", "amount", "order_amount", "sell_policy_id",
        "sell_policy_name", "order_no", "rejected_reason", "api_called",
        "mock_order_called", "real_order_called", "success",
    ]
    rows = [{c: r.get(c, "") for c in cols if c in r} for r in results]
    st.dataframe(pd.DataFrame(rows), use_container_width=True)
