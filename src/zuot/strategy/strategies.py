"""Candidate intraday fade strategies.

All strategies are short-first (sell then buy back), because the account can
only sell shares held from the previous close. A signal is evaluated on a
closed 5-minute bar and executed on the next bar's open by the backtest engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any, Protocol

import pandas as pd


@dataclass(frozen=True)
class EntrySignal:
    score: float
    reason: str
    target_pct: float
    stop_pct: float


class Strategy(Protocol):
    name: str

    def evaluate(self, row: pd.Series) -> EntrySignal | None: ...


def _clock(value: str | time) -> time:
    if isinstance(value, time):
        return value
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _crossed_below_vwap(row: pd.Series) -> bool:
    if pd.isna(row.get("prev_close")) or pd.isna(row.get("prev_vwap")):
        return False
    return bool(row["prev_close"] >= row["prev_vwap"] and row["close"] < row["vwap"])


class GapFadeStrategy:
    """S1: fade a high open after an overextended push fails at VWAP."""

    name = "gap_fade"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.entry_start = _clock(p.get("entry_start", "09:45"))
        self.entry_end = _clock(p.get("entry_end", "10:30"))
        self.gap_min = float(p.get("gap_min", 0.025))
        self.gap_max = float(p.get("gap_max", 0.07))
        self.vwap_z = float(p.get("vwap_z", 1.0))
        self.volume_ratio_max = float(p.get("volume_ratio_max", 1.2))
        self.rsi_min = float(p.get("rsi_min", 65.0))
        self.target_pct = float(p.get("target_pct", 0.015))
        self.stop_pct = float(p.get("stop_pct", 0.015))

    def evaluate(self, row: pd.Series) -> EntrySignal | None:
        if not (self.entry_start <= row["time"] <= self.entry_end):
            return None
        gap = float(row["gap"])
        if not (self.gap_min <= gap <= self.gap_max):
            return None
        if float(row["max_z"]) < self.vwap_z:
            return None
        if not _crossed_below_vwap(row):
            return None
        if float(row["vol_ratio"]) > self.volume_ratio_max:
            return None
        if float(row["max_rsi"]) < self.rsi_min:
            return None
        score = (
            (float(row["max_z"]) - self.vwap_z)
            + (float(row["max_rsi"]) - self.rsi_min) / 20.0
            - max(0.0, float(row["vol_ratio"]) - 1.0) * 0.5
        )
        reason = (
            f"gap={gap:.1%} max_z={float(row['max_z']):.2f} "
            f"max_rsi={float(row['max_rsi']):.0f} vol_ratio={float(row['vol_ratio']):.2f}"
        )
        return EntrySignal(score, reason, self.target_pct, self.stop_pct)


class VwapReversionStrategy:
    """S2: fade any overextension far above VWAP that rolls back through it."""

    name = "vwap_reversion"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.entry_start = _clock(p.get("entry_start", "10:00"))
        self.entry_end = _clock(p.get("entry_end", "14:00"))
        self.vwap_z = float(p.get("vwap_z", 1.5))
        self.volume_ratio_max = float(p.get("volume_ratio_max", 1.5))
        self.rsi_min = float(p.get("rsi_min", 70.0))
        self.target_pct = float(p.get("target_pct", 0.015))
        self.stop_pct = float(p.get("stop_pct", 0.015))

    def evaluate(self, row: pd.Series) -> EntrySignal | None:
        if not (self.entry_start <= row["time"] <= self.entry_end):
            return None
        if float(row["max_z"]) < self.vwap_z:
            return None
        if not _crossed_below_vwap(row):
            return None
        if float(row["vol_ratio"]) > self.volume_ratio_max:
            return None
        if float(row["max_rsi"]) < self.rsi_min:
            return None
        score = (float(row["max_z"]) - self.vwap_z) + (float(row["max_rsi"]) - self.rsi_min) / 20.0
        reason = (
            f"max_z={float(row['max_z']):.2f} max_rsi={float(row['max_rsi']):.0f} "
            f"vol_ratio={float(row['vol_ratio']):.2f}"
        )
        return EntrySignal(score, reason, self.target_pct, self.stop_pct)


class PriorHighFadeStrategy:
    """S3: fade a failed intraday break of the previous day's high."""

    name = "prior_high_fade"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.entry_start = _clock(p.get("entry_start", "10:00"))
        self.entry_end = _clock(p.get("entry_end", "14:30"))
        self.probe_pct = float(p.get("probe_pct", 0.002))
        self.fail_pct = float(p.get("fail_pct", 0.003))
        self.target_pct = float(p.get("target_pct", 0.015))
        self.stop_pct = float(p.get("stop_pct", 0.015))

    def evaluate(self, row: pd.Series) -> EntrySignal | None:
        if not (self.entry_start <= row["time"] <= self.entry_end):
            return None
        prior_high = row.get("prior_high")
        if pd.isna(prior_high):
            return None
        high = float(row["high"])
        close = float(row["close"])
        if high < float(prior_high) * (1.0 - self.probe_pct):
            return None
        if close >= float(prior_high) * (1.0 - self.fail_pct):
            return None
        if close >= float(row["open"]):
            return None
        score = (high / float(prior_high) - 1.0) * 100.0 - (
            close / float(row["open"]) - 1.0
        ) * 100.0
        reason = (
            f"prior_high={float(prior_high):.2f} high={high:.2f} close={close:.2f} "
            f"bar_ret={close / float(row['open']) - 1.0:.2%}"
        )
        return EntrySignal(score, reason, self.target_pct, self.stop_pct)


class AfternoonPulseStrategy:
    """S4: fade a late-session pulse that loses VWAP."""

    name = "afternoon_pulse"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.entry_start = _clock(p.get("entry_start", "13:00"))
        self.entry_end = _clock(p.get("entry_end", "14:00"))
        self.min_ret_from_open = float(p.get("min_ret_from_open", 0.02))
        self.vwap_z = float(p.get("vwap_z", 1.5))
        self.rsi_min = float(p.get("rsi_min", 68.0))
        self.target_pct = float(p.get("target_pct", 0.012))
        self.stop_pct = float(p.get("stop_pct", 0.015))

    def evaluate(self, row: pd.Series) -> EntrySignal | None:
        if not (self.entry_start <= row["time"] <= self.entry_end):
            return None
        if float(row["ret_from_open"]) < self.min_ret_from_open:
            return None
        if float(row["max_z"]) < self.vwap_z:
            return None
        if not _crossed_below_vwap(row):
            return None
        if float(row["max_rsi"]) < self.rsi_min:
            return None
        score = (float(row["max_z"]) - self.vwap_z) + float(row["ret_from_open"]) * 10.0
        reason = (
            f"ret_from_open={float(row['ret_from_open']):.2%} "
            f"max_z={float(row['max_z']):.2f} max_rsi={float(row['max_rsi']):.0f}"
        )
        return EntrySignal(score, reason, self.target_pct, self.stop_pct)


_STRATEGIES = {
    "gap_fade": GapFadeStrategy,
    "vwap_reversion": VwapReversionStrategy,
    "prior_high_fade": PriorHighFadeStrategy,
    "afternoon_pulse": AfternoonPulseStrategy,
}


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    try:
        factory = _STRATEGIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(_STRATEGIES)}") from exc
    return factory(params)
