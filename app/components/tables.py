"""테이블 컴포넌트."""

from typing import List, Optional
import pandas as pd
import streamlit as st


def render_candidates_table(df: pd.DataFrame) -> None:
    """AI 후보 종목 테이블 렌더링."""
    if df is None or df.empty:
        st.info("표시할 후보 종목이 없습니다.")
        return
    display_cols = {
        "ticker": "종목코드",
        "name": "종목명",
        "close": "현재가",
        "current_price": "현재가",
        "prediction_score": "예측점수",
        "probability": "상승확률",
        "trading_value": "거래대금",
        "avg_20d_trading_value": "20일평균거래대금",
        "selected_by_step": "선정단계",
        "exclusion_reason": "제외사유",
        "final_rank": "순위",
    }
    cols = [c for c in display_cols if c in df.columns]
    view = df[cols].rename(columns={c: display_cols[c] for c in cols})
    if "현재가" in view.columns:
        view["현재가"] = view["현재가"].apply(lambda x: f"{int(x):,}원" if pd.notna(x) else "-")
    if "거래대금" in view.columns:
        view["거래대금"] = view["거래대금"].apply(lambda x: f"{x/1e8:.0f}억" if pd.notna(x) and x > 0 else "-")
    if "20일평균거래대금" in view.columns:
        view["20일평균거래대금"] = view["20일평균거래대금"].apply(lambda x: f"{x/1e8:.0f}억" if pd.notna(x) and x > 0 else "-")
    st.dataframe(view, use_container_width=True)


def render_positions_table(positions: List[dict]) -> None:
    """보유종목 테이블 렌더링."""
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
            "매수가": f"{ep:,.0f}",
            "현재가": f"{cp:,.0f}",
            "목표가(+2%)": f"{tp:,.0f}",
            "손절가(-3%)": f"{sp:,.0f}",
            "평가손익": f"{pnl:+,.0f}",
            "수익률": f"{pnl_rate:+.2f}%",
            "주문번호": pos.get("order_no", "-"),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def render_order_results_table(results: List[dict]) -> None:
    """주문 결과 테이블 렌더링."""
    if not results:
        st.info("주문 결과가 없습니다.")
        return
    cols = ["trade_mode", "stock_code", "stock_name", "quantity", "price",
            "amount", "order_no", "filled_quantity", "rejected_reason",
            "api_called", "real_order_called", "success"]
    rows = []
    for r in results:
        row = {c: r.get(c, "") for c in cols}
        rows.append(row)
    df = pd.DataFrame(rows)
    rename = {
        "trade_mode": "모드", "stock_code": "종목코드", "stock_name": "종목명",
        "quantity": "수량", "price": "가격", "amount": "주문금액",
        "order_no": "주문번호", "filled_quantity": "체결수량",
        "rejected_reason": "거부사유", "api_called": "API호출",
        "real_order_called": "실전주문", "success": "성공여부",
    }
    st.dataframe(df.rename(columns=rename), use_container_width=True)
