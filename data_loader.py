from __future__ import annotations

import pandas as pd
import yfinance as yf


def fetch_hourly_btc_data(days: int = 730, symbol: str = "BTC-USD") -> pd.DataFrame:
    """Fetch hourly OHLCV data using Yahoo's supported intraday range windows."""
    # Yahoo's 1h interval has a strict max lookback near 730 days.
    # Use `period` (not explicit start/end) and stay slightly below the hard cap.
    safe_days = max(1, min(int(days), 729))
    periods = [f"{safe_days}d"]
    if safe_days > 700:
        periods.append("700d")
    periods.append("365d")

    df = pd.DataFrame()
    for period in periods:
        df = yf.download(
            symbol,
            period=period,
            interval="1h",
            auto_adjust=False,
            progress=False,
        )
        if not df.empty:
            break

    if df.empty:
        raise ValueError("No data returned from yfinance.")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
    df = df[~df.index.duplicated(keep="last")]
    return df.dropna()
