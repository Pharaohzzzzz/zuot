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


class OpenSourceComboStrategy:
    """S5: 8kms-style suitability score plus a mean-reversion regime veto.

    This is a bounded port of ideas, not a copy of either project:
    * the 0-100 score, gap/VWAP/position/amount/shadow weights and the 65
      threshold come from ``8kms/quant-t-system``;
    * the efficiency-ratio and ATR-slope veto come from
      ``Ye-Yu-Mo/mean-reversion-t``;
    * the buyback target is the closest-to-entry of VWAP, MA5, MA20 and an
      ATR reference, with the 0.27% practical edge as a floor.

    The source projects do not provide an out-of-sample A-share test with our
    cost/T+1 rules. The point of S5 is to test those ideas under the same Gate
    as the other candidates, not to trust their published hit rates.
    """

    name = "open_source_combo"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.entry_start = _clock(p.get("entry_start", "09:35"))
        self.entry_end = _clock(p.get("entry_end", "10:30"))
        self.min_score = float(p.get("min_score", 65.0))
        self.min_gap = float(p.get("min_gap", 0.006))
        self.max_gap = float(p.get("max_gap", 0.03))
        self.min_vwap_distance = float(p.get("min_vwap_distance", 0.003))
        self.close_position_min = float(p.get("close_position_min", 0.72))
        self.amount_ratio_min = float(p.get("amount_ratio_min", 0.035))
        self.ret5_min = float(p.get("ret5_min", 0.05))
        self.position20_min = float(p.get("position20_min", 0.82))
        self.upper_shadow_min = float(p.get("upper_shadow_min", 0.35))
        self.max_er = float(p.get("max_er", 0.80))
        self.max_abs_slope = float(p.get("max_abs_slope", 0.45))
        self.min_target_pct = float(p.get("min_target_pct", 0.0027))
        self.max_target_pct = float(p.get("max_target_pct", 0.02))
        self.min_stop_pct = float(p.get("min_stop_pct", 0.006))
        self.max_stop_pct = float(p.get("max_stop_pct", 0.015))

    def evaluate(self, row: pd.Series) -> EntrySignal | None:
        if not (self.entry_start <= row["time"] <= self.entry_end):
            return None
        required = (
            "gap",
            "vwap",
            "close",
            "preclose",
            "close_position",
            "amount_ratio",
            "upper_shadow",
            "d_ret5",
            "d_position20",
        )
        if any(pd.isna(row.get(key)) for key in required):
            return None
        close = float(row["close"])
        vwap = float(row["vwap"])
        if close <= vwap:
            return None

        gap = float(row["gap"])
        score = 45.0
        if self.min_gap <= gap <= self.max_gap:
            score += min(18.0, gap * 700.0)
        elif gap > self.max_gap:
            score += 6.0
        elif gap < -0.004:
            score -= 12.0

        vwap_distance = close / vwap - 1.0
        if vwap_distance > self.min_vwap_distance:
            score += min(14.0, vwap_distance * 1600.0)
        if float(row["close_position"]) > self.close_position_min:
            score += 8.0
        amount_ratio = float(row["amount_ratio"])
        if amount_ratio > self.amount_ratio_min:
            score += min(8.0, amount_ratio * 80.0)
        if float(row["d_ret5"]) > self.ret5_min:
            score += 8.0
        if float(row["d_position20"]) > self.position20_min:
            score += 7.0
        if float(row["upper_shadow"]) > self.upper_shadow_min:
            score += 5.0
        if bool(row.get("broke_prev_high")) and amount_ratio > 0.04:
            score -= 8.0

        er16 = float(row.get("er16", 0.0) or 0.0)
        slope = float(row.get("slope8_atr", 0.0) or 0.0)
        if er16 >= self.max_er or abs(slope) >= self.max_abs_slope:
            return None
        if score < self.min_score:
            return None

        references = [value for value in (vwap, row.get("d_ma5"), row.get("d_ma20"))
                      if value is not None and not pd.isna(value) and 0.0 < float(value) < close]
        atr_pct = float(row["d_atr_pct"]) if not pd.isna(row.get("d_atr_pct")) else 0.0
        if atr_pct > 0.0:
            atr_reference = float(row["preclose"]) * (1.0 - atr_pct * 0.4)
            if 0.0 < atr_reference < close:
                references.append(atr_reference)
        target_price = max(references) if references else close * (1.0 - self.min_target_pct)
        target_pct = (close - target_price) / close
        target_pct = max(self.min_target_pct, min(self.max_target_pct, target_pct))
        stop_pct = max(self.min_stop_pct, min(self.max_stop_pct, atr_pct * 0.5))

        reason = (
            f"score={score:.0f} gap={gap:.1%} vwap_dist={vwap_distance:.2%} "
            f"close_pos={float(row['close_position']):.2f} "
            f"amount_ratio={amount_ratio:.3f} ret5={float(row['d_ret5']):.1%} "
            f"pos20={float(row['d_position20']):.2f} er16={er16:.2f} slope={slope:.2f}"
        )
        return EntrySignal(score, reason, target_pct, stop_pct)


_STRATEGIES = {
    "gap_fade": GapFadeStrategy,
    "vwap_reversion": VwapReversionStrategy,
    "prior_high_fade": PriorHighFadeStrategy,
    "afternoon_pulse": AfternoonPulseStrategy,
    "open_source_combo": OpenSourceComboStrategy,
}


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    try:
        factory = _STRATEGIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(_STRATEGIES)}") from exc
    return factory(params)
