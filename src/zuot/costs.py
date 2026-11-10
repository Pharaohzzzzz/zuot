"""A-share cost model: commission, stamp duty, transfer fee, slippage."""

from __future__ import annotations

from dataclasses import dataclass

from .config import CostConfig


@dataclass(frozen=True)
class Fees:
    commission: float
    stamp: float
    transfer: float

    @property
    def total(self) -> float:
        return round(self.commission + self.stamp + self.transfer, 2)


class CostModel:
    """Fees and slippage for one side or a round trip.

    Actual broker fills already include slippage; call ``net_pnl_from_fills`` for
    those. The ``*_fill_price`` helpers are for backtests that start from a
    signal price.
    """

    def __init__(self, cfg: CostConfig) -> None:
        self.cfg = cfg

    def buy_fees(self, price: float, qty: int) -> Fees:
        notional = price * qty
        commission = max(self.cfg.commission_rate * notional, self.cfg.min_commission)
        transfer = self.cfg.transfer_rate * notional
        return Fees(
            commission=round(commission, 2),
            stamp=0.0,
            transfer=round(transfer, 2),
        )

    def sell_fees(self, price: float, qty: int) -> Fees:
        notional = price * qty
        commission = max(self.cfg.commission_rate * notional, self.cfg.min_commission)
        stamp = self.cfg.stamp_sell_rate * notional
        transfer = self.cfg.transfer_rate * notional
        return Fees(
            commission=round(commission, 2),
            stamp=round(stamp, 2),
            transfer=round(transfer, 2),
        )

    def buy_fill_price(self, price: float, tick: float) -> float:
        return round(price + self.cfg.slippage_ticks * tick, 2)

    def sell_fill_price(self, price: float, tick: float) -> float:
        return round(price - self.cfg.slippage_ticks * tick, 2)

    def net_pnl_from_fills(self, sell_price: float, buy_price: float, qty: int) -> float:
        fees = self.sell_fees(sell_price, qty).total + self.buy_fees(buy_price, qty).total
        return round((sell_price - buy_price) * qty - fees, 2)

    def round_trip_cost(self, price: float, qty: int, tick: float) -> float:
        """Cost of selling and buying the same quantity at the same mid price."""
        sell = self.sell_fill_price(price, tick)
        buy = self.buy_fill_price(price, tick)
        return round(-self.net_pnl_from_fills(sell, buy, qty), 2)
