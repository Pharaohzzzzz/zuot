from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from zuot.data.baostock_source import Bar5m, BarDaily
from zuot.data.store import load_5m, load_daily, upsert_5m, upsert_daily
from zuot.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "bars.db")
    database.init_schema()
    yield database
    database.close()


def make_5m() -> list[Bar5m]:
    return [
        Bar5m(
            "300308",
            datetime(2024, 1, 2, 9, 35),
            112.85,
            112.98,
            110.70,
            111.40,
            2098800,
            234821472.0,
        ),
        Bar5m(
            "300308",
            datetime(2024, 1, 2, 9, 40),
            111.40,
            111.60,
            111.10,
            111.50,
            1000000,
            111500000.0,
        ),
    ]


def test_upsert_and_load_5m(db: Database) -> None:
    assert upsert_5m(db, make_5m()) == 2
    frame = load_5m(db, "300308", date(2024, 1, 2), date(2024, 1, 2))
    assert len(frame) == 2
    assert frame.iloc[0]["close"] == pytest.approx(111.40)
    assert str(frame.iloc[1]["dt"]) == "2024-01-02 09:40:00"


def test_upsert_is_idempotent(db: Database) -> None:
    upsert_5m(db, make_5m())
    upsert_5m(db, make_5m())
    frame = load_5m(db, "300308", date(2024, 1, 2), date(2024, 1, 2))
    assert len(frame) == 2


def test_upsert_and_load_daily(db: Database) -> None:
    bars = [
        BarDaily("300308", date(2024, 1, 2), 112.85, 113.0, 110.0, 111.4, 112.0, 100, 200),
        BarDaily("300308", date(2024, 1, 3), 111.4, 112.0, 109.0, 110.0, 111.4, 100, 200),
    ]
    assert upsert_daily(db, bars) == 2
    frame = load_daily(db, "300308", date(2024, 1, 2), date(2024, 1, 3))
    assert len(frame) == 2
    assert frame.iloc[0]["date"] == date(2024, 1, 2)
    assert frame.iloc[1]["close"] == pytest.approx(110.0)
