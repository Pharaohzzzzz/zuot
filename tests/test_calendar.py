from __future__ import annotations

from datetime import date, timedelta

import pytest

from zuot.calendar import TradingCalendar


def test_weekday_and_weekend() -> None:
    cal = TradingCalendar()
    assert cal.is_trading_day(date(2026, 11, 10))  # Tuesday
    assert not cal.is_trading_day(date(2026, 11, 14))  # Saturday


def test_holiday_is_excluded() -> None:
    cal = TradingCalendar(holidays=frozenset({date(2026, 11, 10)}))
    assert not cal.is_trading_day(date(2026, 11, 10))


def test_next_trading_day_skips_weekend() -> None:
    cal = TradingCalendar()
    assert cal.next_trading_day(date(2026, 11, 13)) == date(2026, 11, 16)


def test_next_trading_day_raises_when_blocked() -> None:
    blocked = frozenset(date(2026, 11, 11) + timedelta(days=i) for i in range(31))
    cal = TradingCalendar(holidays=blocked)
    with pytest.raises(RuntimeError):
        cal.next_trading_day(date(2026, 11, 10))
