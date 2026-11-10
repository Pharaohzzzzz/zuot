from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from zuot.db import Database
from zuot.ledger import TLot

DAY = date(2026, 11, 10)


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    database.init_schema()
    database.seed_position("300308", 500)
    database.seed_position("300476", 2000)
    yield database
    database.close()


def make_lot() -> TLot:
    return TLot(
        date=DAY,
        symbol="300308",
        qty=200,
        sell_price=471.0,
        sell_time=datetime(2026, 11, 10, 10, 0),
        target_price=464.0,
        stop_price=478.0,
        deadline=datetime(2026, 11, 10, 14, 50),
        fees=56.05,
    )


def test_seed_positions(db: Database) -> None:
    assert db.positions() == {"300308": 500, "300476": 2000}
    db.seed_position("300308", 999)
    assert db.positions()["300308"] == 500


def test_insert_and_close_lot(db: Database) -> None:
    lot = make_lot()
    lot_id = db.insert_lot(lot)
    assert lot_id == lot.id
    assert len(db.open_lots(DAY)) == 1
    db.close_lot(
        lot_id,
        buy_price=464.0,
        buy_time=datetime(2026, 11, 10, 13, 0),
        fees=65.0,
        net_pnl=1335.0,
    )
    assert db.open_lots(DAY) == []
    assert db.realized_pnl(DAY) == pytest.approx(1335.0)
    assert len(db.lots_for_day(DAY)) == 1


def test_signals_and_fills(db: Database) -> None:
    day = DAY
    signal_id = db.insert_signal(
        day=day,
        ts=datetime(2026, 11, 10, 10, 0),
        symbol="300308",
        setup="gap_fade",
        action="SELL",
        price=471.0,
        qty=200,
        target=464.0,
        stop=478.0,
        score=1.5,
        reason="test",
        payload={"gap": 0.04},
    )
    assert signal_id > 0
    fill_id = db.insert_fill(
        ts=datetime(2026, 11, 10, 10, 0, 5),
        symbol="300308",
        side="SELL",
        price=471.0,
        qty=200,
        fees=56.05,
        lot_id=None,
    )
    assert fill_id > 0


def test_audit_and_day_state(db: Database) -> None:
    db.insert_audit("start", {"mode": "live"})
    db.set_day_state(DAY, "CLOSED", 1335.0, "ok")
    row = db.conn.execute(
        "SELECT status, realized_pnl FROM day_state WHERE date=?", (DAY.isoformat(),)
    ).fetchone()
    assert row["status"] == "CLOSED"
    assert row["realized_pnl"] == pytest.approx(1335.0)
