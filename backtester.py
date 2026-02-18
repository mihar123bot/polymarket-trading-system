from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM


@dataclass
class BacktestResults:
    data: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict
    bull_state: int
    bear_state: int


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    out["returns"] = out["Close"].pct_change()
    out["range"] = (out["High"] - out["Low"]) / out["Close"].replace(0, np.nan)
    out["volume_volatility"] = out["Volume"].pct_change().rolling(24).std()

    delta = out["Close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean().replace(0, np.nan)
    rs = avg_gain / avg_loss
    out["rsi"] = 100 - (100 / (1 + rs))

    out["momentum"] = out["Close"].pct_change(12)
    out["volatility"] = out["returns"].rolling(24).std() * 100

    plus_dm = out["High"].diff()
    minus_dm = -out["Low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = out["High"] - out["Low"]
    tr2 = (out["High"] - out["Close"].shift()).abs()
    tr3 = (out["Low"] - out["Close"].shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(14).mean().replace(0, np.nan)
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr)
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di)).replace([np.inf, -np.inf], np.nan)
    out["adx"] = dx.rolling(14).mean()

    out["ema50"] = out["Close"].ewm(span=50, adjust=False).mean()
    out["ema200"] = out["Close"].ewm(span=200, adjust=False).mean()

    ema12 = out["Close"].ewm(span=12, adjust=False).mean()
    ema26 = out["Close"].ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()

    return out


def add_hmm_regimes(df: pd.DataFrame, n_components: int = 7, random_state: int = 42) -> tuple[pd.DataFrame, int, int]:
    out = df.copy()

    features = out[["returns", "range", "volume_volatility"]].copy()
    features = features.replace([np.inf, -np.inf], np.nan).dropna()

    if len(features) < 120:
        raise ValueError("Not enough data after feature engineering to fit HMM (need at least 120 rows).")

    # Use fewer states when history is limited to keep the fit stable.
    adaptive_components = int(max(2, min(n_components, len(features) // 60)))

    mean = features.mean()
    std = features.std().replace(0, 1e-8)
    scaled = ((features - mean) / std).values

    model = GaussianHMM(
        n_components=adaptive_components,
        covariance_type="full",
        n_iter=500,
        random_state=random_state,
    )
    model.fit(scaled)

    states = pd.Series(model.predict(scaled), index=features.index, name="regime_state")
    out = out.join(states, how="left")
    out["regime_state"] = out["regime_state"].ffill().bfill().astype(int)

    state_returns = out.groupby("regime_state")["returns"].mean()
    bull_state = int(state_returns.idxmax())
    bear_state = int(state_returns.idxmin())

    out["regime_label"] = "Neutral"
    out.loc[out["regime_state"] == bull_state, "regime_label"] = "Bull Run"
    out.loc[out["regime_state"] == bear_state, "regime_label"] = "Bear/Crash"

    return out, bull_state, bear_state


def evaluate_confirmations(row: pd.Series) -> tuple[int, dict]:
    checks = {
        "rsi_lt_90": bool(row["rsi"] < 90),
        "momentum_gt_1pct": bool(row["momentum"] > 0.01),
        "volatility_lt_6pct": bool(row["volatility"] < 6),
        "adx_gt_25": bool(row["adx"] > 25),
        "price_gt_ema50": bool(row["Close"] > row["ema50"]),
        "price_gt_ema200": bool(row["Close"] > row["ema200"]),
        "ema50_gt_ema200": bool(row["ema50"] > row["ema200"]),
        "macd_gt_signal": bool(row["macd"] > row["macd_signal"]),
    }
    votes = sum(checks.values())
    return votes, checks


def max_drawdown(equity_curve: pd.Series) -> float:
    running_max = equity_curve.cummax()
    drawdown = equity_curve / running_max - 1.0
    return float(drawdown.min())


def run_backtest(initial_capital: float = 10_000.0, leverage: float = 2.5) -> BacktestResults:
    from data_loader import fetch_hourly_btc_data

    raw = fetch_hourly_btc_data(days=730)
    df = compute_indicators(raw)
    df, bull_state, bear_state = add_hmm_regimes(df)

    df = df.dropna().copy()

    capital = float(initial_capital)
    in_position = False
    entry_price = 0.0
    entry_time = None
    entry_capital = capital
    entry_votes = 0

    cooldown_until = pd.Timestamp.min
    trades: list[dict] = []
    equity_points: list[float] = []
    signal_points: list[str] = []

    for ts, row in df.iterrows():
        votes, _ = evaluate_confirmations(row)
        regime_is_bull = row["regime_state"] == bull_state
        regime_is_bear = row["regime_state"] == bear_state

        if in_position:
            leveraged_return = leverage * ((row["Close"] / entry_price) - 1.0)
            marked_equity = max(entry_capital * (1 + leveraged_return), 0.0)
        else:
            marked_equity = capital

        should_exit = in_position and regime_is_bear
        if should_exit:
            pnl = marked_equity - entry_capital
            ret = (pnl / entry_capital) if entry_capital else 0.0
            capital = marked_equity
            trades.append(
                {
                    "entry_time": entry_time,
                    "exit_time": ts,
                    "entry_price": entry_price,
                    "exit_price": row["Close"],
                    "entry_votes": entry_votes,
                    "exit_reason": "Regime flipped to Bear/Crash",
                    "pnl": pnl,
                    "return_pct": ret * 100,
                }
            )
            in_position = False
            entry_price = 0.0
            entry_time = None
            entry_capital = capital
            entry_votes = 0
            cooldown_until = ts + pd.Timedelta(hours=48)

        can_enter = (not in_position) and (ts >= cooldown_until)
        if can_enter and regime_is_bull and votes >= 7:
            in_position = True
            entry_price = float(row["Close"])
            entry_time = ts
            entry_capital = capital
            entry_votes = votes

        equity_points.append(marked_equity if in_position else capital)
        signal_points.append("Long" if in_position else "Cash")

    if in_position and entry_time is not None:
        last_price = float(df["Close"].iloc[-1])
        leveraged_return = leverage * ((last_price / entry_price) - 1.0)
        marked_equity = max(entry_capital * (1 + leveraged_return), 0.0)
        pnl = marked_equity - entry_capital
        ret = (pnl / entry_capital) if entry_capital else 0.0
        capital = marked_equity
        trades.append(
            {
                "entry_time": entry_time,
                "exit_time": df.index[-1],
                "entry_price": entry_price,
                "exit_price": last_price,
                "entry_votes": entry_votes,
                "exit_reason": "End of backtest",
                "pnl": pnl,
                "return_pct": ret * 100,
            }
        )

    df["equity"] = equity_points
    df["signal"] = signal_points

    trades_df = pd.DataFrame(trades)
    total_return = (capital / initial_capital - 1) if initial_capital else 0.0

    bh_return = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) if len(df) > 1 else 0.0
    alpha = total_return - bh_return

    if len(trades_df):
        win_rate = float((trades_df["pnl"] > 0).mean())
    else:
        win_rate = 0.0

    metrics = {
        "initial_capital": initial_capital,
        "ending_capital": capital,
        "total_return": total_return,
        "buy_hold_return": bh_return,
        "alpha": alpha,
        "win_rate": win_rate,
        "max_drawdown": max_drawdown(df["equity"]),
        "total_trades": int(len(trades_df)),
    }

    return BacktestResults(
        data=df,
        trades=trades_df,
        metrics=metrics,
        bull_state=bull_state,
        bear_state=bear_state,
    )
