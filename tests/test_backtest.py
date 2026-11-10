"""Backtest mechanics with a synthetic one-shot strategy."""

from __future__ import annotations

from datetime import date, time

import pandas as pd
import pytest

from zuot.backtest.engine import run_backtest
from zuot.config import load_config
from zuot.data.baostock_source import Bar5m, BarDaily
from zuot.data.store import upsert_5m, upsert_daily
from zuot.db import Database
from zuot.strategy.strategies import EntrySignal


class OneShot:
    name = "one_shot"

    def __init__(self, at: time) -> None:
        self.at = at

    def evaluate(self, row: pd.Series) -> EntrySignal | None:
        if row["time"] != self.at:
            return None
        return EntrySignal(1.0, "synthetic", 0.01, 0.01)


def _bars(day: date = date(2024, 1, 2)) -> list[Bar5m]:
    stamps = pd.date_range(f"{day} 09:35", f"{day} 15:00", freq="5min")
    out = []
    for ts in stamps:
        # The target bar: a 1% drop from the 9.99 sell fill is 9.89.
        low = 9.85 if ts.time() == time(9, 55) else 9.98
        out.append(
            Bar5m(
                symbol="300476",
                dt=ts.to_pydatetime(),
                open=10.0,
                high=10.02,
                low=low,
                close=10.0,
                volume=1000.0,
                amount=10000.0,
            )
        )
    return out


def _daily() -> list[BarDaily]:
    return [
        BarDaily(
            symbol="300476",
            date=date(2024, 1, 1),
            open=9.8,
            high=9.9,
            low=9.7,
            close=9.8,
            preclose=9.7,
            volume=1.0,
            amount=1.0,
        ),
        BarDaily(
            symbol="300476",
            date=date(2024, 1, 2),
            open=10.0,
            high=10.2,
            low=9.7,
            close=10.0,
            preclose=9.8,
            volume=1.0,
            amount=1.0,
        ),
    ]


def _result(tmp_path, strategy: OneShot):
    cfg = load_config()
    db = Database(tmp_path / "zuot.db")
    db.init_schema()
    db.seed_position("300476", 2000)
    upsert_5m(db, _bars())
    upsert_daily(db, _daily())
    return run_backtest(
        db,
        cfg,
        date(2024, 1, 2),
        date(2024, 1, 2),
        strategy,
        symbols=["300476"],
    )


def test_target_exit(tmp_path) -> None:
    result = _result(tmp_path, OneShot(time(9, 45)))
    assert result.metrics["n_trades"] == 1
    trade = result.trades[0]
    assert trade.sell_price == pytest.approx(9.99)
    assert trade.buy_price == pytest.approx(9.90)
    assert trade.exit_reason == "target"
    assert trade.net_pnl == pytest.approx(74.80)


def test_force_buyback_at_1450(tmp_path) -> None:
    result = _result(tmp_path, OneShot(time(13, 55)))
    assert result.metrics["n_trades"] == 1
    trade = result.trades[0]
    assert trade.exit_reason == "force_1450"
    assert trade.sell_time.time() == time(14, 0)
    assert trade.buy_time.time() == time(14, 50)
    assert trade.buy_price == pytest.approx(10.01)


def test_one_round_trip_per_symbol_per_day(tmp_path) -> None:
    class TwoShots:
        name = "two_shots"

        def evaluate(self, row: pd.Series) -> EntrySignal | None:
            if row["time"] in {time(9, 45), time(13, 55)}:
                return EntrySignal(1.0, "synthetic", 0.01, 0.01)
            return None

    result = _result(tmp_path, TwoShots())
    assert result.metrics["n_trades"] == 1
