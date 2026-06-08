"""plotly 차트 생성 컴포넌트."""

from typing import Optional
import pandas as pd

try:
    import plotly.express as px
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def pnl_bar_chart(daily_pnl: pd.DataFrame):
    """일별 손익 막대 차트."""
    if not HAS_PLOTLY or daily_pnl is None or daily_pnl.empty:
        return None
    fig = px.bar(
        daily_pnl, x="date", y="pnl",
        title="일별 실현손익",
        labels={"date": "날짜", "pnl": "손익(원)"},
        color="pnl",
        color_continuous_scale=["#ef4444", "#22c55e"],
    )
    fig.update_layout(showlegend=False)
    return fig


def cumulative_return_chart(daily_pnl: pd.DataFrame, initial_capital: float = 100_000_000):
    """누적수익률 라인 차트."""
    if not HAS_PLOTLY or daily_pnl is None or daily_pnl.empty:
        return None
    df = daily_pnl.copy().sort_values("date")
    df["cumulative_pnl"] = df["pnl"].cumsum()
    df["return_pct"] = df["cumulative_pnl"] / initial_capital * 100
    fig = px.line(
        df, x="date", y="return_pct",
        title="누적수익률",
        labels={"date": "날짜", "return_pct": "누적수익률(%)"},
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    return fig


def pnl_distribution_chart(df: pd.DataFrame):
    """거래별 수익률 분포 히스토그램."""
    if not HAS_PLOTLY or df is None or df.empty:
        return None
    sells = df[df.get("side", pd.Series()).eq("sell")] if "side" in df.columns else df
    if sells.empty or "pnl_rate" not in sells.columns:
        return None
    fig = px.histogram(
        sells, x="pnl_rate",
        title="거래별 수익률 분포",
        labels={"pnl_rate": "수익률(%)"},
        nbins=30,
    )
    return fig


def ticker_pnl_chart(df: pd.DataFrame):
    """종목별 손익 막대 차트."""
    if not HAS_PLOTLY or df is None or df.empty:
        return None
    if "stock_code" not in df.columns or "pnl" not in df.columns:
        return None
    by_ticker = df.groupby("stock_code")["pnl"].sum().reset_index().sort_values("pnl")
    fig = px.bar(
        by_ticker, x="stock_code", y="pnl",
        title="종목별 손익",
        labels={"stock_code": "종목코드", "pnl": "손익(원)"},
        color="pnl",
        color_continuous_scale=["#ef4444", "#22c55e"],
    )
    return fig
