"""Intraday technical indicators for 1-minute and 3-minute timing checks."""

import pandas as pd


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    close = pd.to_numeric(close, errors="coerce").astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))


def calculate_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    close = pd.to_numeric(close, errors="coerce").astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_hist": macd - macd_signal,
    })


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    high = pd.to_numeric(df.get("high", df["close"]), errors="coerce").astype(float)
    low = pd.to_numeric(df.get("low", df["close"]), errors="coerce").astype(float)
    close = pd.to_numeric(df["close"], errors="coerce").astype(float)
    volume = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0).astype(float)
    typical = (high + low + close) / 3
    return (typical * volume).cumsum() / (volume.cumsum() + 1e-9)


def add_intraday_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty or "close" not in out.columns:
        return out
    out["rsi_14"] = calculate_rsi(out["close"], 14)
    macd_df = calculate_macd(out["close"])
    out = pd.concat([out, macd_df], axis=1)
    out["vwap"] = calculate_vwap(out)
    out["price_above_vwap"] = pd.to_numeric(out["close"], errors="coerce") >= out["vwap"]
    volume = pd.to_numeric(out.get("volume", 0), errors="coerce").fillna(0).astype(float)
    out["volume_ratio_intraday"] = volume / (volume.rolling(20, min_periods=1).mean() + 1e-9)
    close = pd.to_numeric(out["close"], errors="coerce").astype(float)
    out["short_momentum"] = close.pct_change(3).fillna(0)
    return out
