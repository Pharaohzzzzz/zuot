"""Aggregate 3-second level-1 snapshots into 5-minute bars."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from ..data.baostock_source import Bar5m


def bar_end(ts: datetime) -> datetime:
    """Return the end timestamp of the 5-minute bar containing ``ts``.

    A tick at 09:35:00 starts the 09:40 bar; the 09:35 bar is complete at that
    instant. The 15:00 closing tick is folded into a final 15:00 bar.
    """
    if ts.time() >= time(15, 0):
        return datetime.combine(ts.date(), time(15, 0))
    minutes = ts.hour * 60 + ts.minute
    end_minutes = ((minutes - 9 * 60 - 30) // 5 + 1) * 5 + 9 * 60 + 30
    return datetime.combine(ts.date(), time()) + timedelta(minutes=end_minutes)


@dataclass
class _Bar:
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    last_cum_volume: float = 0.0
    last_cum_amount: float = 0.0
    ticks: int = 0

    def add(self, price: float, cum_volume: float, cum_amount: float) -> None:
        if self.ticks == 0:
            self.open = price
            self.high = price
            self.low = price
        self.close = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.volume += max(0.0, cum_volume - self.last_cum_volume)
        self.amount += max(0.0, cum_amount - self.last_cum_amount)
        self.last_cum_volume = cum_volume
        self.last_cum_amount = cum_amount
        self.ticks += 1


@dataclass
class BarBuilder:
    symbol: str
    bars: list[Bar5m] = field(default_factory=list)
    _current: _Bar | None = None
    _last_cum_volume: float = 0.0
    _last_cum_amount: float = 0.0

    def update(
        self,
        *,
        price: float,
        cumulative_volume: float,
        cumulative_amount: float,
        ts: datetime,
    ) -> Bar5m | None:
        if price <= 0 or ts.time() < time(9, 30):
            return None
        end = bar_end(ts)
        finished: Bar5m | None = None
        if self._current is None:
            self._current = _Bar(
                end=end,
                open=price,
                high=price,
                low=price,
                close=price,
                last_cum_volume=self._last_cum_volume,
                last_cum_amount=self._last_cum_amount,
            )
        elif end > self._current.end:
            finished = self._finish()
            self._current = _Bar(
                end=end,
                open=price,
                high=price,
                low=price,
                close=price,
                last_cum_volume=self._last_cum_volume,
                last_cum_amount=self._last_cum_amount,
            )
        self._current.add(price, cumulative_volume, cumulative_amount)
        self._last_cum_volume = cumulative_volume
        self._last_cum_amount = cumulative_amount
        return finished

    def flush(self) -> Bar5m | None:
        return self._finish()

    def _finish(self) -> Bar5m | None:
        bar = self._current
        self._current = None
        if bar is None or bar.ticks == 0:
            return None
        out = Bar5m(
            symbol=self.symbol,
            dt=bar.end,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=round(bar.volume, 2),
            amount=round(bar.amount, 2),
            source="live",
        )
        self.bars.append(out)
        return out
