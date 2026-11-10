from __future__ import annotations

from pathlib import Path

import pytest

from zuot.config import load_config
from zuot.costs import CostModel

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def model() -> CostModel:
    return CostModel(load_config(ROOT).costs)


def test_300308_round_trip_cost(model: CostModel) -> None:
    assert model.round_trip_cost(471.0, 200, 0.01) == pytest.approx(69.0, abs=0.02)


def test_300476_round_trip_cost(model: CostModel) -> None:
    assert model.round_trip_cost(132.8, 1000, 0.01) == pytest.approx(111.64, abs=0.05)


def test_min_commission_floor(model: CostModel) -> None:
    fees = model.buy_fees(10.0, 100)
    assert fees.commission == pytest.approx(5.0)
    assert fees.stamp == pytest.approx(0.0)


def test_sell_has_stamp_and_buy_does_not(model: CostModel) -> None:
    sell = model.sell_fees(471.0, 200)
    buy = model.buy_fees(471.0, 200)
    assert sell.stamp > 0
    assert buy.stamp == 0


def test_net_pnl_uses_both_sides_fees(model: CostModel) -> None:
    pnl = model.net_pnl_from_fills(471.0, 464.0, 200)
    assert pnl == pytest.approx(1335.13, abs=0.01)


def test_slippage_direction(model: CostModel) -> None:
    assert model.sell_fill_price(471.0, 0.01) == pytest.approx(470.99)
    assert model.buy_fill_price(471.0, 0.01) == pytest.approx(471.01)
