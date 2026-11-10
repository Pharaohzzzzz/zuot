"""Intraday live loop: poll L1, build 5m bars, alert, and track manual fills.

The loop never places an order. A sell alert is advisory until the operator
records the fill with ``zuot fill --side sell``; only then does the DB contain an
open T lot and the target/stop/deadline monitor becomes active.
"""

from __future__ import annotations

import time as sleep_time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import pandas as pd

from ..config import AppConfig
from ..data.baostock_source import Bar5m
from ..data.realtime import Quote, fetch_quotes
from ..data.store import load_daily, upsert_5m
from ..db import Database
from ..notify.feishu import FeishuNotifier, buyback_text, sell_text
from ..strategy.features import add_features
from ..strategy.strategies import Strategy, build_strategy
from .bar_builder import BarBuilder


def _clock(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _quote_dt(quote: Quote) -> datetime:
    try:
        return datetime.fromisoformat(f"{quote.date}T{quote.time}")
    except ValueError:
        return datetime.now()


@dataclass
class LiveEngine:
    cfg: AppConfig
    db: Database
    notifier: FeishuNotifier
    strategy: Strategy | None = None
    poll_seconds: float = 3.0
    quote_fn: Callable[[list[str]], dict[str, Quote]] = fetch_quotes
    now_fn: Callable[[], datetime] = datetime.now
    _builders: dict[str, BarBuilder] = field(default_factory=dict)
    _prev_close: dict[str, float] = field(default_factory=dict)
    _sell_signaled: set[str] = field(default_factory=set)
    _alerted: set[tuple[int, str]] = field(default_factory=set)
    _pending_reminder: set[str] = field(default_factory=set)
    _today: date = field(default_factory=date.today)

    def __post_init__(self) -> None:
        if self.strategy is None:
            self.strategy = build_strategy(self.cfg.strategy.name, self.cfg.strategy.params)

    def run(self, *, once: bool = False, until: time = time(15, 5)) -> int:
        self._today = self.now_fn().date()
        codes = [symbol.code for symbol in self.cfg.symbols.values()]
        while True:
            now = self.now_fn()
            if now.time() >= until:
                break
            if now.time() < time(9, 15):
                if once:
                    return 0
                sleep_time.sleep(30)
                continue
            quotes = self.quote_fn(codes)
            if quotes:
                for code, quote in quotes.items():
                    if code not in self._builders:
                        self._builders[code] = BarBuilder(code)
                        self._prev_close[code] = quote.prev_close
                    builder = self._builders[code]
                    finished = builder.update(
                        price=quote.price,
                        cumulative_volume=quote.volume,
                        cumulative_amount=quote.amount,
                        ts=_quote_dt(quote),
                    )
                    if finished is not None:
                        upsert_5m(self.db, [finished])
                        self._on_bar(code, finished, quote)
                self._monitor_lots(quotes)
            if once:
                break
            sleep_time.sleep(self.poll_seconds)
        self._flush()
        self._eod()
        return 0

    def _on_bar(self, code: str, bar: Bar5m, quote: Quote) -> None:
        if self.strategy is None or code in self._sell_signaled:
            return
        if bar.dt.time() > _clock(self.cfg.risk.no_new_sell_after):
            return
        if self.db.open_lots(self._today):
            return
        if self.db.realized_pnl(self._today) <= -self.cfg.risk.daily_loss_cap:
            return
        existing = [row for row in self.db.lots_for_day(self._today) if row["symbol"] == code]
        if existing:
            return
        frame = self._feature_frame(code)
        if frame is None or frame.empty:
            return
        signal = self.strategy.evaluate(frame.iloc[-1])
        if signal is None:
            return

        price = quote.price or bar.close
        qty = self.cfg.symbols[code].t_qty
        target = round(price * (1.0 - signal.target_pct), 2)
        stop = round(price * (1.0 + signal.stop_pct), 2)
        estimated_loss = (stop - price) * qty
        if estimated_loss > self.cfg.risk.daily_loss_cap:
            self.db.insert_signal(
                day=self._today,
                ts=bar.dt,
                symbol=code,
                setup=self.strategy.name,
                action="SKIP",
                price=price,
                qty=qty,
                target=target,
                stop=stop,
                score=signal.score,
                reason=f"estimated loss {estimated_loss:.0f} exceeds cap",
            )
            return

        self.db.insert_signal(
            day=self._today,
            ts=bar.dt,
            symbol=code,
            setup=self.strategy.name,
            action="SELL",
            price=price,
            qty=qty,
            target=target,
            stop=stop,
            score=signal.score,
            reason=signal.reason,
        )
        self.notifier.send(
            sell_text(
                symbol=code,
                name=self.cfg.symbols[code].name,
                price=price,
                qty=qty,
                target=target,
                stop=stop,
                deadline=self.cfg.risk.force_buyback,
                reason=signal.reason,
            )
        )
        self._sell_signaled.add(code)

    def _feature_frame(self, code: str) -> pd.DataFrame | None:
        builder = self._builders.get(code)
        if builder is None or not builder.bars:
            return None
        bars = pd.DataFrame(
            [
                {
                    "dt": bar.dt,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "amount": bar.amount,
                }
                for bar in builder.bars
            ]
        )
        daily = load_daily(
            self.db,
            code,
            self._today - timedelta(days=60),
            self._today - timedelta(days=1),
        )
        high = float(bars["high"].max())
        today_row = pd.DataFrame(
            [{"date": self._today, "high": high, "preclose": self._prev_close.get(code, 0.0)}]
        )
        daily = pd.concat([daily, today_row], ignore_index=True) if not daily.empty else today_row
        if not float(today_row["preclose"].iloc[0]):
            return None
        return add_features(bars, daily)

    def _monitor_lots(self, quotes: dict[str, Quote]) -> None:
        rows = self.db.open_lots(self._today)
        for row in rows:
            lot_id = int(row["id"])
            symbol = str(row["symbol"])
            quote = quotes.get(symbol)
            if quote is None:
                continue
            target = float(row["target_price"])
            stop = float(row["stop_price"])
            reason: tuple[str, str] | None = None
            if quote.price <= target:
                reason = ("target", f"目标价 {target:.2f}")
            elif quote.price >= stop:
                reason = ("stop", f"止损价 {stop:.2f}")
            elif _quote_dt(quote).time() >= _clock(self.cfg.risk.force_buyback):
                reason = ("deadline", f"{self.cfg.risk.force_buyback} 强平")
            if reason is None or (lot_id, reason[0]) in self._alerted:
                continue
            self._alerted.add((lot_id, reason[0]))
            qty = int(row["qty"])
            self.db.insert_signal(
                day=self._today,
                ts=_quote_dt(quote),
                symbol=symbol,
                setup="exit",
                action="BUY",
                price=quote.price,
                qty=qty,
                target=target,
                stop=stop,
                reason=reason[1],
            )
            self.notifier.send(
                buyback_text(
                    symbol=symbol,
                    name=self.cfg.symbols[symbol].name,
                    price=quote.price,
                    qty=qty,
                    target=target,
                    stop=stop,
                    why=reason[1],
                )
            )

        if not rows:
            for code in list(self._sell_signaled):
                if any(
                    row["symbol"] == code for row in self.db.lots_for_day(self._today)
                ):
                    self._sell_signaled.discard(code)
                    continue
                quote = quotes.get(code)
                if quote is None:
                    continue
                if _quote_dt(quote).time() < _clock(self.cfg.risk.force_buyback):
                    continue
                if code in self._pending_reminder:
                    continue
                self._pending_reminder.add(code)
                self.notifier.send(
                    f"【ZUOT 提醒】{code} 卖出信号后未记录成交。"
                    f"若已卖出，请立即买回并执行 zuot fill --side buy；若未卖出，请忽略。"
                )

    def _flush(self) -> None:
        for builder in self._builders.values():
            bar = builder.flush()
            if bar is not None:
                upsert_5m(self.db, [bar])

    def _eod(self) -> None:
        rows = self.db.open_lots(self._today)
        pnl = self.db.realized_pnl(self._today)
        if rows:
            detail = ", ".join(f"{row['symbol']}#{row['id']}x{row['qty']}" for row in rows)
            self.notifier.send(f"【ZUOT 收盘】仍有未平T：{detail}，请核对券商持仓并处理。")
            self.db.set_day_state(self._today, "OPEN_LOT", pnl, detail)
        else:
            self.db.set_day_state(self._today, "DONE", pnl)
