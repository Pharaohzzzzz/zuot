"""SQLite persistence for bars, signals, lots, fills and audit events."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .ledger import TLot

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    base_qty INTEGER NOT NULL CHECK (base_qty >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS t_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    qty INTEGER NOT NULL,
    sell_price REAL NOT NULL,
    sell_time TEXT NOT NULL,
    target_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    deadline TEXT NOT NULL,
    status TEXT NOT NULL,
    buy_price REAL,
    buy_time TEXT,
    fees REAL NOT NULL DEFAULT 0,
    net_pnl REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_lots_date ON t_lots(date);
CREATE INDEX IF NOT EXISTS idx_lots_status ON t_lots(status);

CREATE TABLE IF NOT EXISTS bars_5m (
    symbol TEXT NOT NULL,
    dt TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    amount REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (symbol, dt)
);

CREATE TABLE IF NOT EXISTS bars_daily (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    preclose REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    amount REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    setup TEXT NOT NULL,
    action TEXT NOT NULL,
    price REAL NOT NULL,
    qty INTEGER NOT NULL,
    target REAL,
    stop REAL,
    score REAL,
    reason TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    qty INTEGER NOT NULL,
    fees REAL NOT NULL DEFAULT 0,
    lot_id INTEGER,
    source TEXT NOT NULL DEFAULT 'manual',
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fills_date ON fills(date);

CREATE TABLE IF NOT EXISTS day_state (
    date TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # positions ---------------------------------------------------------
    def seed_position(self, symbol: str, base_qty: int) -> None:
        self.conn.execute(
            "INSERT INTO positions(symbol, base_qty, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(symbol) DO NOTHING",
            (symbol, base_qty, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def set_position(self, symbol: str, base_qty: int) -> None:
        self.conn.execute(
            "INSERT INTO positions(symbol, base_qty, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET base_qty=excluded.base_qty, "
            "updated_at=excluded.updated_at",
            (symbol, base_qty, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def positions(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT symbol, base_qty FROM positions").fetchall()
        return {row["symbol"]: int(row["base_qty"]) for row in rows}

    # lots --------------------------------------------------------------
    def insert_lot(self, lot: TLot) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO t_lots(date, symbol, qty, sell_price, sell_time, target_price,
                               stop_price, deadline, status, fees, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lot.date.isoformat(),
                lot.symbol,
                lot.qty,
                lot.sell_price,
                lot.sell_time.isoformat(timespec="seconds"),
                lot.target_price,
                lot.stop_price,
                lot.deadline.isoformat(timespec="seconds"),
                lot.status,
                lot.fees,
                lot.note,
            ),
        )
        self.conn.commit()
        new_id = int(cur.lastrowid or 0)
        lot.id = new_id
        return new_id

    def close_lot(
        self,
        lot_id: int,
        *,
        buy_price: float,
        buy_time: datetime,
        fees: float,
        net_pnl: float,
    ) -> None:
        self.conn.execute(
            "UPDATE t_lots SET status='CLOSED', buy_price=?, buy_time=?, fees=?, "
            "net_pnl=? WHERE id=?",
            (
                buy_price,
                buy_time.isoformat(timespec="seconds"),
                fees,
                net_pnl,
                lot_id,
            ),
        )
        self.conn.commit()

    def open_lots(self, day: date | None = None) -> list[sqlite3.Row]:
        if day is None:
            return list(
                self.conn.execute("SELECT * FROM t_lots WHERE status='OPEN' ORDER BY id")
            )
        return list(
            self.conn.execute(
                "SELECT * FROM t_lots WHERE status='OPEN' AND date=? ORDER BY id",
                (day.isoformat(),),
            )
        )

    def lots_for_day(self, day: date) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM t_lots WHERE date=? ORDER BY id", (day.isoformat(),)
            )
        )

    def realized_pnl(self, day: date) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) AS pnl FROM t_lots "
            "WHERE date=? AND status='CLOSED'",
            (day.isoformat(),),
        ).fetchone()
        return round(float(row["pnl"]), 2)

    # signals / fills / audit ------------------------------------------
    def insert_signal(
        self,
        *,
        day: date,
        ts: datetime,
        symbol: str,
        setup: str,
        action: str,
        price: float,
        qty: int,
        target: float | None = None,
        stop: float | None = None,
        score: float | None = None,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO signals(date, ts, symbol, setup, action, price, qty,
                                target, stop, score, reason, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day.isoformat(),
                ts.isoformat(timespec="seconds"),
                symbol,
                setup,
                action,
                price,
                qty,
                target,
                stop,
                score,
                reason,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def insert_fill(
        self,
        *,
        ts: datetime,
        symbol: str,
        side: str,
        price: float,
        qty: int,
        fees: float = 0.0,
        lot_id: int | None = None,
        source: str = "manual",
        note: str = "",
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO fills(ts, date, symbol, side, price, qty, fees, lot_id, source, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts.isoformat(timespec="seconds"),
                ts.date().isoformat(),
                symbol,
                side,
                price,
                qty,
                fees,
                lot_id,
                source,
                note,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def insert_audit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO audit(ts, event, payload_json) VALUES (?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                event,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def set_day_state(
        self, day: date, status: str, realized_pnl: float = 0.0, notes: str = ""
    ) -> None:
        self.conn.execute(
            "INSERT INTO day_state(date, status, realized_pnl, notes, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET status=excluded.status, "
            "realized_pnl=excluded.realized_pnl, notes=excluded.notes, "
            "updated_at=excluded.updated_at",
            (
                day.isoformat(),
                status,
                realized_pnl,
                notes,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()
