"""Intraday features computed from 5-minute bars.

Every feature is causal: row ``i`` only uses bars up to and including ``i``.
The backtest executes a signal on the *next* bar, so there is no look-ahead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _rsi(close: pd.Series, period: int = 6) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.fillna(50.0)


def add_features(bars: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Attach day context and intraday features to a 5-minute frame.

    ``bars`` columns: dt, open, high, low, close, volume, amount.
    ``daily`` columns: date, high, preclose (one row per trading day).
    """
    if bars.empty:
        return bars.copy()
    frame = bars.copy()
    frame["dt"] = pd.to_datetime(frame["dt"])
    frame = frame.sort_values("dt").reset_index(drop=True)
    frame["date"] = frame["dt"].dt.date

    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    daily = daily.sort_values("date").reset_index(drop=True)
    preclose_map = daily.set_index("date")["preclose"].to_dict()
    prior_high_map = daily.set_index("date")["high"].shift(1).to_dict()
    if "low" in daily:
        prior_low_map = daily.set_index("date")["low"].shift(1).to_dict()
    else:
        prior_low_map = {}

    frame["preclose"] = frame["date"].map(preclose_map)
    frame["prior_high"] = frame["date"].map(prior_high_map)
    frame["prior_low"] = frame["date"].map(prior_low_map)
    frame["day_open"] = frame.groupby("date")["open"].transform("first")
    frame["gap"] = frame["day_open"] / frame["preclose"] - 1.0

    # Daily context is shifted one day: the current session may only use the
    # previous close and earlier daily bars. This keeps the strategy causal.
    ctx = daily.copy()
    if "open" not in ctx:
        ctx["open"] = ctx["preclose"]
    if "low" not in ctx:
        ctx["low"] = ctx["high"]
    if "close" not in ctx:
        ctx["close"] = ctx["preclose"]
    if "amount" not in ctx:
        ctx["amount"] = np.nan
    close = ctx["close"].astype(float)
    ctx["d_ret5"] = close.pct_change(5)
    ctx["d_ret20"] = close.pct_change(20)
    ctx["d_ma5"] = close.rolling(5, min_periods=3).mean()
    ctx["d_ma20"] = close.rolling(20, min_periods=10).mean()
    daily_range = (ctx["high"].astype(float) - ctx["low"].astype(float)) / ctx[
        "preclose"
    ].astype(float).replace(0.0, np.nan)
    ctx["d_atr_pct"] = daily_range.rolling(14, min_periods=5).mean()
    high20 = ctx["high"].astype(float).rolling(20, min_periods=10).max()
    low20 = ctx["low"].astype(float).rolling(20, min_periods=10).min()
    span20 = (high20 - low20).replace(0.0, np.nan)
    ctx["d_position20"] = ((close - low20) / span20).fillna(0.5)
    ctx["d_avg_amount20"] = ctx["amount"].astype(float).rolling(20, min_periods=5).mean()
    ctx = ctx.set_index("date").shift(1)
    for column in (
        "d_ret5",
        "d_ret20",
        "d_ma5",
        "d_ma20",
        "d_atr_pct",
        "d_position20",
        "d_avg_amount20",
    ):
        frame[column] = frame["date"].map(ctx[column])

    cum_amount = frame.groupby("date")["amount"].cumsum()
    cum_volume = frame.groupby("date")["volume"].cumsum()
    frame["vwap"] = cum_amount / cum_volume.replace(0.0, np.nan)

    frame["rsi"] = frame.groupby("date")["close"].transform(_rsi)
    frame["max_rsi"] = frame.groupby("date")["rsi"].cummax()

    deviation = frame["close"] - frame["vwap"]
    frame["deviation"] = deviation
    std = deviation.groupby(frame["date"]).transform(
        lambda series: series.rolling(20, min_periods=5).std()
    )
    frame["z"] = (deviation / std.replace(0.0, np.nan)).fillna(0.0)
    frame["max_z"] = frame.groupby("date")["z"].cummax()

    volume_mean = frame.groupby("date")["volume"].transform(
        lambda series: series.rolling(20, min_periods=5).mean()
    )
    frame["vol_ratio"] = (frame["volume"] / volume_mean.replace(0.0, np.nan)).fillna(1.0)

    frame["prev_close"] = frame.groupby("date")["close"].shift(1)
    frame["prev_vwap"] = frame.groupby("date")["vwap"].shift(1)
    frame["day_high"] = frame.groupby("date")["high"].cummax()
    frame["day_low"] = frame.groupby("date")["low"].cummin()
    frame["ret_from_open"] = frame["close"] / frame["day_open"] - 1.0
    frame["high_from_open"] = frame["high"] / frame["day_open"] - 1.0
    frame["time"] = frame["dt"].dt.time

    # Execution-context features used by the open-source-inspired candidate.
    frame["vwap_distance"] = frame["close"] / frame["vwap"] - 1.0
    day_span = (frame["day_high"] - frame["day_low"]).replace(0.0, np.nan)
    frame["close_position"] = (
        (frame["close"] - frame["day_low"]) / day_span
    ).fillna(0.5)
    frame["amount_ratio"] = (
        cum_amount / frame["d_avg_amount20"].replace(0.0, np.nan)
    ).fillna(0.0)
    bar_span = (frame["high"] - frame["low"]).replace(0.0, np.nan)
    frame["upper_shadow"] = (
        (frame["high"] - frame[["open", "close"]].max(axis=1)) / bar_span
    ).fillna(0.0)
    frame["broke_prev_high"] = frame["day_high"] > frame["prior_high"]
    frame["broke_prev_low"] = frame["day_low"] < frame["prior_low"]

    # Regime filter: efficiency ratio and ATR-normalised slope. These are
    # coarse 5-minute proxies for the 15m/1h regime veto in mean-reversion-t.
    prev_bar_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_bar_close).abs(),
            (frame["low"] - prev_bar_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_abs = true_range.rolling(14, min_periods=5).mean()
    movement = frame["close"].diff().abs().rolling(16, min_periods=8).sum()
    frame["er16"] = (
        (frame["close"] - frame["close"].shift(16)).abs()
        / movement.replace(0.0, np.nan)
    ).fillna(0.0)
    frame["slope8_atr"] = (
        (frame["close"] - frame["close"].shift(8)) / (atr_abs * 8.0)
    ).fillna(0.0)

    frame = frame.dropna(subset=["preclose", "vwap"]).reset_index(drop=True)
    return frame
