"""Unit tests for the open-source-inspired S5 candidate."""

from __future__ import annotations

from datetime import time

import pandas as pd
import pytest

from zuot.strategy.strategies import OpenSourceComboStrategy


def _row(**overrides: object) -> pd.Series:
    values: dict[str, object] = {
        "time": time(9, 40),
        "gap": 0.02,
        "vwap": 100.0,
        "close": 102.0,
        "preclose": 100.0,
        "close_position": 0.80,
        "amount_ratio": 0.05,
        "upper_shadow": 0.40,
        "d_ret5": 0.06,
        "d_position20": 0.85,
        "d_ma5": 101.0,
        "d_ma20": 100.5,
        "d_atr_pct": 0.04,
        "er16": 0.40,
        "slope8_atr": 0.10,
        "broke_prev_high": False,
    }
    values.update(overrides)
    return pd.Series(values)


def test_s5_score_and_multi_reference_target() -> None:
    signal = OpenSourceComboStrategy().evaluate(_row())
    assert signal is not None
    assert signal.score == pytest.approx(105.0)
    # The closest buyback reference is MA5 at 101; 102 -> 101 is 0.98%.
    assert signal.target_pct == pytest.approx(1.0 / 102.0)
    assert signal.stop_pct == pytest.approx(0.015)


def test_s5_trend_veto() -> None:
    assert OpenSourceComboStrategy().evaluate(_row(er16=0.90)) is None
    assert OpenSourceComboStrategy().evaluate(_row(slope8_atr=-0.60)) is None


def test_s5_requires_price_above_vwap_and_score() -> None:
    assert OpenSourceComboStrategy().evaluate(_row(close=99.5)) is None
    weak = _row(
        gap=0.0,
        vwap=101.5,
        close_position=0.50,
        amount_ratio=0.01,
        d_ret5=0.0,
        d_position20=0.40,
        upper_shadow=0.10,
    )
    assert OpenSourceComboStrategy().evaluate(weak) is None
