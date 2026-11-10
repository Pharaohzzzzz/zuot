"""Feature calculations on synthetic bars."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from zuot.strategy.features import add_features


def _bars() -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-02 09:35", "2024-01-02 15:00", freq="5min")
    rows = []
    for index, ts in enumerate(timestamps):
        price = 10.0 + index * 0.01
        rows.append(
            {
                "dt": ts,
                "open": price,
                "high": price + 0.02,
                "low": price - 0.02,
                "close": price,
                "volume": 1000.0,
                "amount": price * 1000.0,
            }
        )
    return pd.DataFrame(rows)


def _daily() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": date(2024, 1, 1), "high": 9.9, "preclose": 9.8},
            {"date": date(2024, 1, 2), "high": 10.5, "preclose": 9.8},
        ]
    )


def test_features_are_causal_and_bounded() -> None:
    frame = add_features(_bars(), _daily())
    assert not frame.empty
    assert frame["vwap"].notna().all()
    assert frame["z"].abs().lt(100).all()
    assert frame["rsi"].between(0, 100).all()
    assert frame["max_z"].ge(frame["z"]).all()
    assert frame["max_rsi"].ge(frame["rsi"]).all()
    assert frame["gap"].iloc[0] == pytest.approx(frame["open"].iloc[0] / 9.8 - 1.0)


def test_features_previous_day_high() -> None:
    frame = add_features(_bars(), _daily())
    assert frame["prior_high"].iloc[0] == 9.9
