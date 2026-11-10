"""BaoStock 5-minute/daily history.

Long ranges make BaoStock hang, so 5-minute fetch is split by calendar year.
The data is unadjusted by default (adjustflag=3); intraday percentage moves are
unaffected by a constant adjustment factor within one day.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import baostock as bs

FIELDS_5M = "date,time,code,open,high,low,close,volume,amount,adjustflag"
FIELDS_DAILY = "date,code,open,high,low,close,preclose,volume,amount,adjustflag"


@dataclass(frozen=True)
class Bar5m:
    symbol: str
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    source: str = "baostock"


@dataclass(frozen=True)
class BarDaily:
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    preclose: float
    volume: float
    amount: float
    source: str = "baostock"


@contextmanager
def session(timeout: int = 30) -> Iterator[None]:
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    login = bs.login()
    if login.error_code != "0":
        socket.setdefaulttimeout(previous)
        raise RuntimeError(f"BaoStock login failed: {login.error_code} {login.error_msg}")
    try:
        yield
    finally:
        bs.logout()
        socket.setdefaulttimeout(previous)


def parse_time(raw: str) -> datetime:
    return datetime.strptime(raw[:14], "%Y%m%d%H%M%S")


def _symbol_from_baostock(code: str) -> str:
    return code.split(".")[-1]


def fetch_5m(code: str, start: date, end: date, adjustflag: str = "3") -> list[Bar5m]:
    rs = bs.query_history_k_data_plus(
        code,
        FIELDS_5M,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        frequency="5",
        adjustflag=adjustflag,
    )
    if rs.error_code != "0":
        raise RuntimeError(f"BaoStock 5m {code} failed: {rs.error_code} {rs.error_msg}")
    bars: list[Bar5m] = []
    while rs.next():
        row = rs.get_row_data()
        bars.append(
            Bar5m(
                symbol=_symbol_from_baostock(row[2]),
                dt=parse_time(row[1]),
                open=float(row[3]),
                high=float(row[4]),
                low=float(row[5]),
                close=float(row[6]),
                volume=float(row[7]),
                amount=float(row[8]),
            )
        )
    return bars


def fetch_5m_chunks(
    code: str, start: date, end: date, adjustflag: str = "3"
) -> Iterator[tuple[date, date, list[Bar5m]]]:
    """Yield 5-minute bars in quarter-year chunks.

    A full calendar year can hang on the BaoStock server for recent years, while
    the same data split into halves/quarters returns normally. Quarter chunks also
    let the caller persist progress before the next request.
    """
    for year in range(start.year, end.year + 1):
        for month in (1, 4, 7, 10):
            chunk_start = max(start, date(year, month, 1))
            if month == 10:
                chunk_end = min(end, date(year, 12, 31))
            else:
                chunk_end = min(end, date(year, month + 3, 1) - timedelta(days=1))
            if chunk_start > chunk_end:
                continue
            yield chunk_start, chunk_end, fetch_5m(
                code, chunk_start, chunk_end, adjustflag=adjustflag
            )


def fetch_5m_range(
    code: str, start: date, end: date, adjustflag: str = "3"
) -> list[Bar5m]:
    bars: list[Bar5m] = []
    for _, _, chunk in fetch_5m_chunks(code, start, end, adjustflag=adjustflag):
        bars.extend(chunk)
    return bars


def fetch_daily(code: str, start: date, end: date, adjustflag: str = "3") -> list[BarDaily]:
    rs = bs.query_history_k_data_plus(
        code,
        FIELDS_DAILY,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        frequency="d",
        adjustflag=adjustflag,
    )
    if rs.error_code != "0":
        raise RuntimeError(f"BaoStock daily {code} failed: {rs.error_code} {rs.error_msg}")
    bars: list[BarDaily] = []
    while rs.next():
        row = rs.get_row_data()
        bars.append(
            BarDaily(
                symbol=_symbol_from_baostock(row[1]),
                date=date.fromisoformat(row[0]),
                open=float(row[2]),
                high=float(row[3]),
                low=float(row[4]),
                close=float(row[5]),
                preclose=float(row[6]),
                volume=float(row[7]),
                amount=float(row[8]),
            )
        )
    return bars


def fetch_trade_dates(start: date, end: date) -> dict[date, bool]:
    rs = bs.query_trade_dates(start_date=start.isoformat(), end_date=end.isoformat())
    if rs.error_code != "0":
        raise RuntimeError(f"BaoStock trade dates failed: {rs.error_code} {rs.error_msg}")
    out: dict[date, bool] = {}
    while rs.next():
        row = rs.get_row_data()
        out[date.fromisoformat(row[0])] = row[1] == "1"
    return out
