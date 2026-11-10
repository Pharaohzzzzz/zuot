"""Event-driven 5-minute backtest for sell-first intraday T trades.

Execution model
---------------
* A signal is evaluated on the close of bar *i*.
* The sell is filled on the open of bar *i+1* (minus one tick).
* Stop/target are checked on every following bar; if both are touched in the
  same bar, the stop wins (conservative).
* Any open lot is bought back at the 14:50 bar close (plus one tick).
* At most one lot is open globally, and one round trip per symbol per day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd

from ..config import AppConfig
from ..costs import CostModel
from ..data.store import load_5m, load_daily
from ..db import Database
from ..strategy.features import add_features
from ..strategy.strategies import Strategy


@dataclass(frozen=True)
class Trade:
    date: date
    symbol: str
    qty: int
    sell_time: datetime
    sell_price: float
    buy_time: datetime
    buy_price: float
    fees: float
    net_pnl: float
    entry_reason: str
    exit_reason: str
    holding_bars: int


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    equity: list[tuple[date, float]] = field(default_factory=list)


@dataclass
class _OpenLot:
    symbol: str
    qty: int
    sell_time: datetime
    sell_price: float
    target: float
    stop: float
    reason: str
    fees_sell: float


@dataclass
class _Pending:
    symbol: str
    signal_time: datetime
    score: float
    reason: str
    target_pct: float
    stop_pct: float


def build_features(db: Database, cfg: AppConfig, start: date, end: date) -> dict[str, pd.DataFrame]:
    """Load and enrich 5-minute frames for every configured symbol."""
    frames: dict[str, pd.DataFrame] = {}
    warmup = start - timedelta(days=30)
    for code in cfg.symbols:
        daily = load_daily(db, code, warmup, end)
        bars = load_5m(db, code, start, end)
        if bars.empty or daily.empty:
            continue
        enriched = add_features(bars, daily)
        if not enriched.empty:
            frames[code] = enriched
    return frames


def _clock(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _metrics(trades: list[Trade]) -> dict[str, Any]:
    if not trades:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "avg_net_pct": 0.0,
            "total_pnl": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "avg_hold_bars": 0.0,
            "by_symbol": {},
            "by_year": {},
        }
    pnls = [trade.net_pnl for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = 999.0 if gross_profit > 0 else 0.0

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    pct_returns = [trade.net_pnl / (trade.sell_price * trade.qty) for trade in trades]
    by_symbol: dict[str, dict[str, float]] = {}
    for trade in trades:
        bucket = by_symbol.setdefault(trade.symbol, {"n": 0, "pnl": 0.0, "wins": 0})
        bucket["n"] += 1
        bucket["pnl"] += trade.net_pnl
        bucket["wins"] += int(trade.net_pnl > 0)
    by_year: dict[int, dict[str, float]] = {}
    for trade in trades:
        year = trade.date.year
        bucket = by_year.setdefault(year, {"n": 0, "pnl": 0.0, "wins": 0})
        bucket["n"] += 1
        bucket["pnl"] += trade.net_pnl
        bucket["wins"] += int(trade.net_pnl > 0)

    return {
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4),
        "avg_net_pct": round(sum(pct_returns) / len(pct_returns), 6),
        "total_pnl": round(sum(pnls), 2),
        "profit_factor": round(profit_factor, 4),
        "max_drawdown": round(max_drawdown, 2),
        "avg_hold_bars": round(
            sum(trade.holding_bars for trade in trades) / len(trades), 2
        ),
        "by_symbol": by_symbol,
        "by_year": by_year,
    }


def run_backtest(
    db: Database,
    cfg: AppConfig,
    start: date,
    end: date,
    strategy: Strategy,
    symbols: list[str] | None = None,
    frames: dict[str, pd.DataFrame] | None = None,
) -> BacktestResult:
    # Research sweeps reuse one enriched frame set across many parameter
    # variants; building features is by far the expensive part.
    if frames is None:
        frames = build_features(db, cfg, start, end)
    selected = symbols or list(cfg.symbols)
    frames = {code: frame for code, frame in frames.items() if code in selected}
    if not frames:
        return BacktestResult(metrics=_metrics([]))

    cost = CostModel(cfg.costs)
    force_time = _clock(cfg.risk.force_buyback)
    no_new_sell = _clock(cfg.risk.no_new_sell_after)
    all_days = sorted(set().union(*(set(frame["date"]) for frame in frames.values())))

    trades: list[Trade] = []
    equity: list[tuple[date, float]] = []
    cumulative = 0.0

    def close_lot(
        lot: _OpenLot,
        buy_time: datetime,
        raw_buy: float,
        reason: str,
        holding_bars: int,
    ) -> None:
        nonlocal cumulative, day_pnl
        tick = cfg.symbol(lot.symbol).tick
        buy_price = cost.buy_fill_price(raw_buy, tick)
        fees_buy = cost.buy_fees(buy_price, lot.qty).total
        net = round((lot.sell_price - buy_price) * lot.qty - lot.fees_sell - fees_buy, 2)
        trades.append(
            Trade(
                date=buy_time.date(),
                symbol=lot.symbol,
                qty=lot.qty,
                sell_time=lot.sell_time,
                sell_price=lot.sell_price,
                buy_time=buy_time,
                buy_price=buy_price,
                fees=round(lot.fees_sell + fees_buy, 2),
                net_pnl=net,
                entry_reason=lot.reason,
                exit_reason=reason,
                holding_bars=holding_bars,
            )
        )
        cumulative = round(cumulative + net, 2)
        day_pnl = round(day_pnl + net, 2)
        equity.append((buy_time.date(), cumulative))

    for day in all_days:
        day_frames: dict[str, pd.DataFrame] = {}
        bar_maps: dict[str, dict[datetime, pd.Series]] = {}
        for code, frame in frames.items():
            sub = frame[frame["date"] == day]
            if sub.empty:
                continue
            sub = sub.sort_values("dt").reset_index(drop=True)
            day_frames[code] = sub
            bar_maps[code] = {row["dt"]: row for _, row in sub.iterrows()}
        if not day_frames:
            continue
        timestamps = sorted(set().union(*(set(mapping) for mapping in bar_maps.values())))

        open_lot: _OpenLot | None = None
        pending: _Pending | None = None
        done: set[str] = set()
        halted = False
        day_pnl = 0.0

        for ts in timestamps:
            # 1. Fill a pending sell at this bar's open.
            if pending is not None and open_lot is None and not halted:
                pending_bar = bar_maps.get(pending.symbol, {}).get(ts)
                if pending_bar is not None and ts > pending.signal_time:
                    if ts.time() > no_new_sell or pending.symbol in done:
                        pending = None
                    else:
                        tick = cfg.symbol(pending.symbol).tick
                        raw_sell = float(pending_bar["open"])
                        if raw_sell > 0:
                            sell_price = cost.sell_fill_price(raw_sell, tick)
                            qty = cfg.symbol(pending.symbol).t_qty
                            open_lot = _OpenLot(
                                symbol=pending.symbol,
                                qty=qty,
                                sell_time=ts,
                                sell_price=sell_price,
                                target=round(sell_price * (1.0 - pending.target_pct), 2),
                                stop=round(sell_price * (1.0 + pending.stop_pct), 2),
                                reason=pending.reason,
                                fees_sell=cost.sell_fees(sell_price, qty).total,
                            )
                        pending = None

            # 2. Manage the open lot on this bar.
            if open_lot is not None:
                bar = bar_maps.get(open_lot.symbol, {}).get(ts)
                if bar is not None:
                    if ts.time() >= force_time:
                        holding = max(1, int((ts - open_lot.sell_time).total_seconds() // 300))
                        close_lot(open_lot, ts, float(bar["close"]), "force_1450", holding)
                        done.add(open_lot.symbol)
                        open_lot = None
                        if day_pnl <= -cfg.risk.daily_loss_cap:
                            halted = True
                    elif float(bar["high"]) >= open_lot.stop:
                        holding = max(1, int((ts - open_lot.sell_time).total_seconds() // 300))
                        raw = max(float(bar["open"]), open_lot.stop)
                        close_lot(open_lot, ts, raw, "stop", holding)
                        done.add(open_lot.symbol)
                        open_lot = None
                        if day_pnl <= -cfg.risk.daily_loss_cap:
                            halted = True
                    elif float(bar["low"]) <= open_lot.target:
                        holding = max(1, int((ts - open_lot.sell_time).total_seconds() // 300))
                        raw = min(float(bar["open"]), open_lot.target)
                        close_lot(open_lot, ts, raw, "target", holding)
                        done.add(open_lot.symbol)
                        open_lot = None
                        if day_pnl <= -cfg.risk.daily_loss_cap:
                            halted = True

            # 3. Evaluate new signals at this bar's close.
            if open_lot is None and pending is None and not halted and ts.time() <= no_new_sell:
                best: _Pending | None = None
                for code, bar in ((code, bar_maps[code].get(ts)) for code in day_frames):
                    if bar is None or code in done:
                        continue
                    signal = strategy.evaluate(bar)
                    if signal is None:
                        continue
                    candidate = _Pending(
                        symbol=code,
                        signal_time=ts,
                        score=signal.score,
                        reason=signal.reason,
                        target_pct=signal.target_pct,
                        stop_pct=signal.stop_pct,
                    )
                    if best is None or candidate.score > best.score:
                        best = candidate
                pending = best

        # 4. End of day: force anything still open; drop an unfilled signal.
        if open_lot is not None:
            sub = day_frames[open_lot.symbol]
            last = sub.iloc[-1]
            holding = max(1, int((last["dt"] - open_lot.sell_time).total_seconds() // 300))
            close_lot(open_lot, last["dt"].to_pydatetime(), float(last["close"]), "eod", holding)
        if pending is not None:
            pending = None

    return BacktestResult(trades=trades, metrics=_metrics(trades), equity=equity)


def trades_frame(result: BacktestResult) -> pd.DataFrame:
    if not result.trades:
        return pd.DataFrame()
    return pd.DataFrame([trade.__dict__ for trade in result.trades])
