from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backtester import run_backtest

st.set_page_config(page_title="Regime-Based BTC Trading App", layout="wide")
st.title("Regime-Based Trading Dashboard")


@st.cache_data(show_spinner=True)
def load_results():
    return run_backtest(initial_capital=10_000.0, leverage=2.5)


results = load_results()
df = results.data.copy()
trades_df = results.trades.copy()
metrics = results.metrics

latest = df.iloc[-1]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Current Signal", latest["signal"])
col2.metric("Detected Regime", latest["regime_label"])
col3.metric("Bull State ID", str(results.bull_state))
col4.metric("Bear State ID", str(results.bear_state))

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Return", f"{metrics['total_return'] * 100:.2f}%")
m2.metric("Alpha vs Buy & Hold", f"{metrics['alpha'] * 100:.2f}%")
m3.metric("Win Rate", f"{metrics['win_rate'] * 100:.2f}%")
m4.metric("Max Drawdown", f"{metrics['max_drawdown'] * 100:.2f}%")

plot_df = df.tail(2000).copy()
fig = go.Figure(
    data=[
        go.Candlestick(
            x=plot_df.index,
            open=plot_df["Open"],
            high=plot_df["High"],
            low=plot_df["Low"],
            close=plot_df["Close"],
            name="BTC-USD",
        ),
        go.Scatter(
            x=plot_df.index,
            y=plot_df["ema50"],
            name="EMA 50",
            mode="lines",
            line=dict(width=1.2, color="#1f77b4"),
        ),
        go.Scatter(
            x=plot_df.index,
            y=plot_df["ema200"],
            name="EMA 200",
            mode="lines",
            line=dict(width=1.2, color="#ff7f0e"),
        ),
    ]
)

regimes = plot_df["regime_label"].fillna("Neutral")
regime_change = regimes.ne(regimes.shift()).cumsum()
for _, chunk in plot_df.groupby(regime_change):
    label = chunk["regime_label"].iloc[0]
    if label == "Bull Run":
        color = "rgba(0, 180, 0, 0.10)"
    elif label == "Bear/Crash":
        color = "rgba(220, 0, 0, 0.10)"
    else:
        continue

    fig.add_vrect(
        x0=chunk.index[0],
        x1=chunk.index[-1],
        fillcolor=color,
        opacity=0.2,
        layer="below",
        line_width=0,
    )

fig.update_layout(
    title="BTC-USD Candlestick with HMM Regime Background",
    xaxis_title="Time",
    yaxis_title="Price",
    xaxis_rangeslider_visible=False,
    template="plotly_white",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    height=650,
)

st.plotly_chart(fig, use_container_width=True)

st.subheader("Equity Curve")
eq_fig = go.Figure()
eq_fig.add_trace(
    go.Scatter(
        x=df.index,
        y=df["equity"],
        mode="lines",
        name="Strategy Equity",
        line=dict(width=1.6),
    )
)
eq_fig.update_layout(template="plotly_white", height=300, yaxis_title="Portfolio Value ($)")
st.plotly_chart(eq_fig, use_container_width=True)

st.subheader("Trade Log")
if trades_df.empty:
    st.info("No trades triggered with current regime/confirmation thresholds.")
else:
    display_cols = [
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "entry_votes",
        "exit_reason",
        "pnl",
        "return_pct",
    ]
    trades_df = trades_df[display_cols].copy()
    trades_df["entry_time"] = pd.to_datetime(trades_df["entry_time"]).dt.strftime("%Y-%m-%d %H:%M")
    trades_df["exit_time"] = pd.to_datetime(trades_df["exit_time"]).dt.strftime("%Y-%m-%d %H:%M")
    st.dataframe(trades_df, use_container_width=True)
