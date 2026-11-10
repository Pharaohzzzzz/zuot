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
    daily = daily.sort_values("date")
    preclose_map = daily.set_index("date")["preclose"].to_dict()
    prior_high_map = daily.set_index("date")["high"].shift(1).to_dict()

    frame["preclose"] = frame["date"].map(preclose_map)
    frame["prior_high"] = frame["date"].map(prior_high_map)
    frame["day_open"] = frame.groupby("date")["open"].transform("first")
    frame["gap"] = frame["day_open"] / frame["preclose"] - 1.0

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
    frame["ret_from_open"] = frame["close"] / frame["day_open"] - 1.0
    frame["high_from_open"] = frame["high"] / frame["day_open"] - 1.0
    frame["time"] = frame["dt"].dt.time

    frame = frame.dropna(subset=["preclose", "vwap"]).reset_index(drop=True)
    return frame
