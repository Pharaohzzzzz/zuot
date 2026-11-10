from __future__ import annotations

from datetime import date, datetime

import pytest

from zuot.ledger import Ledger, Position, RiskError

DAY = date(2026, 11, 10)
SELL_TIME = datetime(2026, 11, 10, 10, 0)
DEADLINE = datetime(2026, 11, 10, 14, 50)


def make_ledger() -> Ledger:
    return Ledger(
        {
            "300308": Position("300308", 500),
            "300476": Position("300476", 2000),
        },
        max_open_t=1,
        max_round_trips=1,
    )


def open_300308(ledger: Ledger, qty: int = 200) -> int:
    lot = ledger.open_lot(
        symbol="300308",
        qty=qty,
        sell_price=471.0,
        sell_time=SELL_TIME,
        target_price=464.0,
        stop_price=478.0,
        deadline=DEADLINE,
        fees=56.05,
    )
    return lot.id


def test_sell_then_buy_restores_base() -> None:
    ledger = make_ledger()
    lot_id = open_300308(ledger)
    assert ledger.positions["300308"].sellable_today == 300
    assert ledger.positions["300308"].expected_base_eod == 300

    lot = ledger.close_lot(
        lot_id,
        buy_price=464.0,
        buy_time=datetime(2026, 11, 10, 13, 0),
        fees=8.95,
    )
    pos = ledger.positions["300308"]
    assert lot.status == "CLOSED"
    assert lot.net_pnl == pytest.approx(1335.0)
    assert pos.sellable_today == 300  # bought shares are T+1 locked
    assert pos.expected_base_eod == 500
    assert ledger.realized_pnl(DAY) == pytest.approx(1335.0)


def test_reconcile_after_round_trip() -> None:
    ledger = make_ledger()
    lot_id = open_300308(ledger)
    ledger.close_lot(lot_id, buy_price=464.0, buy_time=datetime(2026, 11, 10, 13, 0), fees=8.95)
    assert ledger.reconcile({"300308": 500, "300476": 2000}) == {}
    assert ledger.reconcile({"300308": 400, "300476": 2000}) == {"300308": (500, 400)}


def test_only_one_open_t_globally() -> None:
    ledger = make_ledger()
    open_300308(ledger)
    with pytest.raises(RiskError):
        ledger.open_lot(
            symbol="300476",
            qty=1000,
            sell_price=132.8,
            sell_time=SELL_TIME,
            target_price=130.0,
            stop_price=135.0,
            deadline=DEADLINE,
        )


def test_max_one_round_trip_per_symbol_per_day() -> None:
    ledger = make_ledger()
    lot_id = open_300308(ledger)
    ledger.close_lot(lot_id, buy_price=464.0, buy_time=datetime(2026, 11, 10, 13, 0))
    with pytest.raises(RiskError):
        open_300308(ledger)


def test_cannot_sell_more_than_sellable() -> None:
    ledger = make_ledger()
    with pytest.raises(RiskError):
        open_300308(ledger, qty=600)


def test_qty_must_be_lot_size() -> None:
    ledger = make_ledger()
    with pytest.raises(RiskError):
        open_300308(ledger, qty=150)


def test_target_stop_order_is_validated() -> None:
    ledger = make_ledger()
    with pytest.raises(RiskError):
        ledger.open_lot(
            symbol="300308",
            qty=200,
            sell_price=471.0,
            sell_time=SELL_TIME,
            target_price=480.0,
            stop_price=478.0,
            deadline=DEADLINE,
        )


def test_cancel_restores_sellable() -> None:
    ledger = make_ledger()
    lot_id = open_300308(ledger)
    ledger.cancel_lot(lot_id)
    assert ledger.positions["300308"].sellable_today == 500
    assert ledger.positions["300308"].expected_base_eod == 500
    assert not ledger.has_open_t()
    open_300308(ledger)  # cancelled lots do not consume the daily round trip


def test_unwind_failed_keeps_short_exposure_visible() -> None:
    ledger = make_ledger()
    lot_id = open_300308(ledger)
    ledger.mark_unwind_failed(lot_id, note="limit up, no buyback")
    assert ledger.positions["300308"].expected_base_eod == 300
    assert not ledger.has_open_t()
    assert ledger.realized_pnl(DAY) == 0


def test_assert_flat_raises_on_open_lot() -> None:
    ledger = make_ledger()
    open_300308(ledger)
    with pytest.raises(RiskError):
        ledger.assert_flat(DAY)
    ledger.close_lot(1, buy_price=464.0, buy_time=datetime(2026, 11, 10, 13, 0))
    ledger.assert_flat(DAY)


def test_unknown_symbol_raises() -> None:
    ledger = make_ledger()
    with pytest.raises(RiskError):
        ledger.position("000001")
