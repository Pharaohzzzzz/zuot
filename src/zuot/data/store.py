"""Persistence helpers for market data."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

import pandas as pd

from ..db import Database
from .baostock_source import Bar5m, BarDaily


def upsert_5m(db: Database, bars: Iterable[Bar5m]) -> int:
    rows = [
        (
            bar.symbol,
            bar.dt.isoformat(timespec="seconds"),
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            bar.amount,
            bar.source,
        )
        for bar in bars
    ]
    if not rows:
        return 0
    db.conn.executemany(
        "INSERT OR REPLACE INTO bars_5m"
        "(symbol, dt, open, high, low, close, volume, amount, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.conn.commit()
    return len(rows)


def upsert_daily(db: Database, bars: Iterable[BarDaily]) -> int:
    rows = [
        (
            bar.symbol,
            bar.date.isoformat(),
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.preclose,
            bar.volume,
            bar.amount,
            bar.source,
        )
        for bar in bars
    ]
    if not rows:
        return 0
    db.conn.executemany(
        "INSERT OR REPLACE INTO bars_daily(symbol, date, open, high, low, close, preclose, "
        "volume, amount, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.conn.commit()
    return len(rows)


def load_5m(db: Database, symbol: str, start: date, end: date) -> pd.DataFrame:
    end_dt = datetime.combine(end, datetime.max.time()).isoformat()
    frame = pd.read_sql_query(
        "SELECT dt, open, high, low, close, volume, amount FROM bars_5m "
        "WHERE symbol=? AND dt>=? AND dt<=? ORDER BY dt",
        db.conn,
        params=(symbol, start.isoformat(), end_dt),
    )
    if not frame.empty:
        frame["dt"] = pd.to_datetime(frame["dt"])
    return frame


def load_daily(db: Database, symbol: str, start: date, end: date) -> pd.DataFrame:
    frame = pd.read_sql_query(
        "SELECT date, open, high, low, close, preclose, volume, amount FROM bars_daily "
        "WHERE symbol=? AND date>=? AND date<=? ORDER BY date",
        db.conn,
        params=(symbol, start.isoformat(), end.isoformat()),
    )
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame
