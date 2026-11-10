"""In-memory T ledger: positions, lots, T+1 sellable shares, PnL.

The ledger is intentionally strict. A live/backtest path that would violate a
rule raises ``RiskError`` instead of silently continuing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


class RiskError(RuntimeError):
    pass


@dataclass
class TLot:
    date: date
    symbol: str
    qty: int
    sell_price: float
    sell_time: datetime
    target_price: float
    stop_price: float
    deadline: datetime
    status: str = "OPEN"
    buy_price: float | None = None
    buy_time: datetime | None = None
    fees: float = 0.0
    net_pnl: float = 0.0
    note: str = ""
    id: int = 0


@dataclass
class Position:
    symbol: str
    base_qty: int
    sold_today: int = 0
    bought_today: int = 0

    @property
    def sellable_today(self) -> int:
        """Shares held at the open that have not been sold today.

        Buybacks do not replenish this: shares bought today are T+1 locked.
        """
        return max(0, self.base_qty - self.sold_today)

    @property
    def expected_base_eod(self) -> int:
        return self.base_qty - self.sold_today + self.bought_today

    @property
    def open_qty(self) -> int:
        return self.sold_today - self.bought_today

    def reset_day(self) -> None:
        self.sold_today = 0
        self.bought_today = 0


class Ledger:
    def __init__(
        self,
        positions: dict[str, Position],
        lots: list[TLot] | None = None,
        max_open_t: int = 1,
        max_round_trips: int = 1,
    ) -> None:
        self.positions = positions
        self.lots = lots or []
        self.max_open_t = max_open_t
        self.max_round_trips = max_round_trips
        self._next_id = max((lot.id for lot in self.lots), default=0) + 1

    def position(self, symbol: str) -> Position:
        try:
            return self.positions[symbol]
        except KeyError as exc:
            raise RiskError(f"unknown symbol {symbol}") from exc

    def open_lots(self) -> list[TLot]:
        return [lot for lot in self.lots if lot.status == "OPEN"]

    def has_open_t(self) -> bool:
        return bool(self.open_lots())

    def open_lot(
        self,
        *,
        symbol: str,
        qty: int,
        sell_price: float,
        sell_time: datetime,
        target_price: float,
        stop_price: float,
        deadline: datetime,
        fees: float = 0.0,
        note: str = "",
    ) -> TLot:
        pos = self.position(symbol)
        if qty <= 0 or qty % 100 != 0:
            raise RiskError(f"qty must be a positive multiple of 100, got {qty}")
        if qty > pos.sellable_today:
            raise RiskError(
                f"{symbol}: qty {qty} exceeds sellable_today {pos.sellable_today}"
            )
        if len(self.open_lots()) >= self.max_open_t:
            raise RiskError(f"max_open_t={self.max_open_t} already reached")
        trips = sum(
            1
            for lot in self.lots
            if lot.date == sell_time.date()
            and lot.symbol == symbol
            and lot.status in {"OPEN", "CLOSED"}
        )
        if trips >= self.max_round_trips:
            raise RiskError(
                f"{symbol}: max_round_trips={self.max_round_trips} already reached for "
                f"{sell_time.date()}"
            )
        if not (target_price < sell_price < stop_price):
            raise RiskError(
                "expected target_price < sell_price < stop_price, got "
                f"{target_price} / {sell_price} / {stop_price}"
            )
        lot = TLot(
            id=self._next_id,
            date=sell_time.date(),
            symbol=symbol,
            qty=qty,
            sell_price=sell_price,
            sell_time=sell_time,
            target_price=target_price,
            stop_price=stop_price,
            deadline=deadline,
            fees=round(fees, 2),
            note=note,
        )
        self._next_id += 1
        self.lots.append(lot)
        pos.sold_today += qty
        return lot

    def find_lot(self, lot_id: int) -> TLot:
        for lot in self.lots:
            if lot.id == lot_id:
                return lot
        raise RiskError(f"unknown lot id {lot_id}")

    def close_lot(
        self,
        lot_id: int,
        *,
        buy_price: float,
        buy_time: datetime,
        fees: float = 0.0,
    ) -> TLot:
        lot = self.find_lot(lot_id)
        if lot.status != "OPEN":
            raise RiskError(f"lot {lot_id} is {lot.status}, not OPEN")
        pos = self.position(lot.symbol)
        lot.status = "CLOSED"
        lot.buy_price = buy_price
        lot.buy_time = buy_time
        lot.fees = round(lot.fees + fees, 2)
        lot.net_pnl = round((lot.sell_price - buy_price) * lot.qty - lot.fees, 2)
        pos.bought_today += lot.qty
        return lot

    def cancel_lot(self, lot_id: int) -> TLot:
        """Cancel a lot that was recorded before the sell actually filled."""
        lot = self.find_lot(lot_id)
        if lot.status != "OPEN":
            raise RiskError(f"lot {lot_id} is {lot.status}, not OPEN")
        lot.status = "CANCELLED"
        self.position(lot.symbol).sold_today -= lot.qty
        return lot

    def mark_unwind_failed(self, lot_id: int, *, note: str = "") -> TLot:
        lot = self.find_lot(lot_id)
        if lot.status != "OPEN":
            raise RiskError(f"lot {lot_id} is {lot.status}, not OPEN")
        lot.status = "UNWIND_FAILED"
        lot.note = note
        return lot

    def realized_pnl(self, day: date | None = None) -> float:
        total = 0.0
        for lot in self.lots:
            if lot.status != "CLOSED":
                continue
            if day is not None and lot.date != day:
                continue
            total += lot.net_pnl
        return round(total, 2)

    def reconcile(
        self, actual: dict[str, int], day: date | None = None
    ) -> dict[str, tuple[int, int]]:
        """Return symbol -> (expected, actual) for mismatches."""
        mismatches: dict[str, tuple[int, int]] = {}
        for symbol, pos in self.positions.items():
            expected = pos.expected_base_eod
            seen = actual.get(symbol, pos.base_qty)
            if seen != expected:
                mismatches[symbol] = (expected, seen)
        return mismatches

    def assert_flat(self, day: date | None = None) -> None:
        open_lots = [
            lot for lot in self.lots if lot.status == "OPEN" and (day is None or lot.date == day)
        ]
        if open_lots:
            raise RiskError(
                "open T lots remain: "
                + ", ".join(f"{lot.symbol}#{lot.id}x{lot.qty}" for lot in open_lots)
            )
