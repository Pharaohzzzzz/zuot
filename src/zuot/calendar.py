"""Trading calendar.

The weekday check is only a fallback. The live engine must use the exchange
calendar fetched from BaoStock; ``zuot doctor`` warns when holidays are empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class TradingCalendar:
    holidays: frozenset[date] = frozenset()

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def next_trading_day(self, day: date) -> date:
        cursor = day + timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)
        raise RuntimeError(f"no trading day found within 30 days after {day}")
