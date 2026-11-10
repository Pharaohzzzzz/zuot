"""Command-line entry points.

Phase 0 exposes the scaffolding commands. Data/research/live commands are
registered now so the surface is stable, but they exit non-zero until the
corresponding phase lands.
"""

from __future__ import annotations

import importlib.util
import os
import time as sleep_time
from datetime import date, datetime, time, timedelta

import httpx
import typer
from rich.console import Console
from rich.table import Table

from .backtest.engine import run_backtest, trades_frame
from .calendar import TradingCalendar
from .config import AppConfig, load_config
from .costs import CostModel
from .data.baostock_source import chunk_ranges, fetch_5m, fetch_daily, session
from .data.realtime import fetch_quotes
from .data.store import upsert_5m, upsert_daily
from .db import Database
from .ledger import Ledger, Position, RiskError, TLot
from .live.engine import LiveEngine
from .notify.feishu import FeishuNotifier
from .report.daily import write_daily_report
from .research.gate import gate_status
from .research.runner import run_research
from .strategy.strategies import build_strategy

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="zuot: sell-first T research and signal system for 300308 / 300476.",
)
console = Console()

SINA_URL = "https://hq.sinajs.cn/list=sz300308,sz300476"
SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0",
}
TENCENT_URL = "https://qt.gtimg.cn/q=sz300308,sz300476"


def _open_db() -> Database:
    cfg = load_config()
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    db.init_schema()
    return db


def _not_implemented(command: str) -> None:
    console.print(
        f"[yellow]{command}[/yellow] is not implemented yet. "
        "Phase 0 only ships init/doctor/status."
    )
    raise typer.Exit(code=2)


def _parse_at(raw: str | None, day: date) -> datetime:
    if not raw:
        return datetime.now()
    if "T" in raw or " " in raw:
        return datetime.fromisoformat(raw)
    hour, minute = raw.split(":", 1)
    return datetime.combine(day, time(int(hour), int(minute)))


def _lot_from_row(row: object) -> TLot:
    values = dict(row)  # sqlite3.Row
    return TLot(
        id=int(values["id"]),
        date=date.fromisoformat(values["date"]),
        symbol=str(values["symbol"]),
        qty=int(values["qty"]),
        sell_price=float(values["sell_price"]),
        sell_time=datetime.fromisoformat(values["sell_time"]),
        target_price=float(values["target_price"]),
        stop_price=float(values["stop_price"]),
        deadline=datetime.fromisoformat(values["deadline"]),
        status=str(values["status"]),
        buy_price=float(values["buy_price"]) if values["buy_price"] is not None else None,
        buy_time=(
            datetime.fromisoformat(values["buy_time"]) if values["buy_time"] is not None else None
        ),
        fees=float(values["fees"]),
        net_pnl=float(values["net_pnl"]),
        note=str(values["note"]),
    )


def _ledger_for_day(db: Database, cfg: AppConfig, day: date) -> Ledger:
    positions = {
        code: Position(code, db.positions().get(code, cfg.symbol(code).base_qty))
        for code in cfg.symbols
    }
    rows = {int(row["id"]): row for row in db.lots_for_day(day)}
    for row in db.open_lots():
        rows.setdefault(int(row["id"]), row)
    lots = [_lot_from_row(row) for row in rows.values()]
    for lot in lots:
        if lot.status in {"OPEN", "CLOSED"}:
            positions[lot.symbol].sold_today += lot.qty
        if lot.status == "CLOSED":
            positions[lot.symbol].bought_today += lot.qty
    return Ledger(positions, lots, max_open_t=cfg.risk.max_open_t, max_round_trips=1)


def _parse_actual(raw: str | None) -> dict[str, int] | None:
    if not raw:
        return None
    out: dict[str, int] = {}
    for part in raw.split(","):
        code, qty = part.split(":", 1)
        out[code.strip()] = int(qty)
    return out


def _last_5m(db: Database, code: str) -> str | None:
    row = db.conn.execute(
        "SELECT MAX(dt) AS dt FROM bars_5m WHERE symbol=?", (code,)
    ).fetchone()
    return str(row["dt"]) if row and row["dt"] else None


@app.command()
def init() -> None:
    """Create directories, the SQLite schema and seed the two positions."""
    cfg = load_config()
    cfg.ensure_dirs()
    with Database(cfg.db_path) as db:
        db.init_schema()
        for symbol in cfg.symbols.values():
            db.seed_position(symbol.code, symbol.base_qty)
        positions = db.positions()
    console.print(f"config root: {cfg.root}")
    console.print(f"database:    {cfg.db_path}")
    for code, qty in sorted(positions.items()):
        console.print(f"  {code} {cfg.symbol(code).name}: base_qty={qty}")


@app.command()
def doctor() -> None:
    """Check config, dependencies, network sources and notification setup."""
    cfg = load_config()
    checks: list[tuple[str, str, str]] = []
    hard_failures = 0

    for name in ("base.toml", "symbols.toml", "costs.toml", "risk.toml", "strategy.toml"):
        path = cfg.root / "configs" / name
        ok = path.exists()
        checks.append(("config", "OK" if ok else "FAIL", str(path)))
        hard_failures += 0 if ok else 1

    try:
        cfg.ensure_dirs()
        checks.append(("data dirs", "OK", str(cfg.data_dir)))
    except OSError as exc:
        checks.append(("data dirs", "FAIL", str(exc)))
        hard_failures += 1

    baostock_ok = importlib.util.find_spec("baostock") is not None
    checks.append(
        ("baostock", "OK" if baostock_ok else "FAIL", "installed" if baostock_ok else "missing")
    )
    hard_failures += 0 if baostock_ok else 1

    for name, url in (("sina", SINA_URL), ("tencent", TENCENT_URL)):
        try:
            # Domestic quote endpoints are reachable directly. trust_env=False also
            # avoids the machine's NO_PROXY entry "[::1]", which httpx rejects.
            with httpx.Client(trust_env=False, timeout=8.0) as client:
                response = client.get(url, headers=SINA_HEADERS)
            ok = response.status_code == 200 and "=" in response.text
            checks.append((name, "OK" if ok else "FAIL", f"HTTP {response.status_code}"))
            hard_failures += 0 if ok else 1
        except Exception as exc:  # network errors are a doctor finding, not a crash
            checks.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))
            hard_failures += 1

    if cfg.notify.dry_run:
        checks.append(("feishu", "DRY-RUN", "notify.toml dry_run=true"))
    else:
        env_name = cfg.notify.webhook_env
        has_webhook = bool(os.getenv(env_name))
        detail = f"{env_name} {'set' if has_webhook else 'missing'}"
        checks.append(("feishu", "OK" if has_webhook else "FAIL", detail))
        hard_failures += 0 if has_webhook else 1

    checks.append(("calendar", "WARN", "holidays empty; live must load BaoStock trade calendar"))
    checks.append(
        (
            "local time",
            "INFO",
            datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        )
    )

    table = Table(title="zuot doctor")
    table.add_column("check")
    table.add_column("result")
    table.add_column("detail")
    for name, result, detail in checks:
        style = {"OK": "green", "FAIL": "red", "WARN": "yellow"}.get(result, "cyan")
        table.add_row(name, f"[{style}]{result}[/{style}]", detail)
    console.print(table)

    if hard_failures:
        console.print(f"[red]{hard_failures} hard check(s) failed[/red]")
        raise typer.Exit(code=1)
    console.print("[green]doctor passed[/green]")


@app.command()
def status() -> None:
    """Show configured positions, open lots and today's realized PnL."""
    cfg = load_config()
    if not cfg.db_path.exists():
        console.print(f"[yellow]no database at {cfg.db_path}; run `zuot init`[/yellow]")
        raise typer.Exit(code=1)

    with Database(cfg.db_path) as db:
        db.init_schema()
        positions = db.positions()
        open_lots = db.open_lots()
        today = date.today()
        pnl = db.realized_pnl(today)

    table = Table(title="positions")
    table.add_column("symbol")
    table.add_column("name")
    table.add_column("base_qty", justify="right")
    table.add_column("T qty", justify="right")
    for code in sorted(cfg.symbols):
        symbol = cfg.symbol(code)
        base_qty = positions.get(code, symbol.base_qty)
        table.add_row(code, symbol.name, str(base_qty), str(symbol.t_qty))
    console.print(table)

    if open_lots:
        lots = Table(title="open T lots")
        lots.add_column("id", justify="right")
        lots.add_column("symbol")
        lots.add_column("qty", justify="right")
        lots.add_column("sell")
        lots.add_column("target")
        lots.add_column("stop")
        for row in open_lots:
            lots.add_row(
                str(row["id"]),
                str(row["symbol"]),
                str(row["qty"]),
                f"{row['sell_price']:.2f}",
                f"{row['target_price']:.2f}",
                f"{row['stop_price']:.2f}",
            )
        console.print(lots)
    else:
        console.print("no open T lots")

    console.print(f"realized PnL {today.isoformat()}: {pnl:.2f} CNY")


@app.command("fetch")
def fetch_cmd(
    start: str | None = typer.Option(None, help="start date YYYY-MM-DD"),
    end: str | None = typer.Option(None, help="end date YYYY-MM-DD"),
    realtime: bool = typer.Option(False, "--realtime", help="only fetch a level-1 snapshot"),
    daily: bool = typer.Option(True, "--daily/--no-daily", help="also fetch daily bars"),
) -> None:
    """Fetch and persist market data."""
    cfg = load_config()
    if realtime:
        quotes = fetch_quotes([symbol.code for symbol in cfg.symbols.values()])
        if not quotes:
            console.print("[red]both Sina and Tencent failed[/red]")
            raise typer.Exit(code=1)
        table = Table(title="level-1 snapshot")
        for column in ("symbol", "name", "price", "prev", "chg", "bid", "ask", "time", "source"):
            table.add_column(column)
        for code, quote in sorted(quotes.items()):
            table.add_row(
                code,
                quote.name,
                f"{quote.price:.2f}",
                f"{quote.prev_close:.2f}",
                f"{quote.change_pct:+.2%}",
                f"{quote.bid:.2f}",
                f"{quote.ask:.2f}",
                quote.time,
                quote.source,
            )
        console.print(table)
        return

    end_day = date.fromisoformat(end) if end else date.today()
    start_day = date.fromisoformat(start) if start else end_day - timedelta(days=30)
    if start_day > end_day:
        raise typer.BadParameter("start must be <= end")

    db = _open_db()
    try:
        errors = 0
        for symbol in cfg.symbols.values():
            code = f"{symbol.exchange}.{symbol.code}"
            count_5m = 0
            for chunk_start, chunk_end in chunk_ranges(start_day, end_day):
                existing_max = _last_5m(db, symbol.code)
                if existing_max and date.fromisoformat(existing_max[:10]) >= (
                    chunk_end - timedelta(days=7)
                ):
                    console.print(
                        f"{code} {chunk_start.isoformat()}..{chunk_end.isoformat()}: "
                        "already stored, skip"
                    )
                    continue
                # A long-lived BaoStock session starts hanging after roughly 20
                # queries. Re-login for every quarter and retry transient
                # network errors so a backfill can resume.
                bars = None
                for attempt in range(3):
                    try:
                        with session():
                            bars = fetch_5m(code, chunk_start, chunk_end)
                        break
                    except Exception as exc:
                        console.print(
                            f"[yellow]{code} {chunk_start}..{chunk_end} "
                            f"attempt {attempt + 1} failed: {exc}[/yellow]"
                        )
                        sleep_time.sleep(3)
                if bars is None:
                    errors += 1
                    continue
                written = upsert_5m(db, bars)
                count_5m += written
                console.print(
                    f"{code} {chunk_start.isoformat()}..{chunk_end.isoformat()}: "
                    f"{written} bars"
                )
            count_daily = 0
            if daily:
                for attempt in range(3):
                    try:
                        with session():
                            count_daily = upsert_daily(
                                db, fetch_daily(code, start_day, end_day)
                            )
                        break
                    except Exception as exc:
                        console.print(
                            f"[yellow]{code} daily attempt {attempt + 1} failed: "
                            f"{exc}[/yellow]"
                        )
                        sleep_time.sleep(3)
            console.print(
                f"{code}: 5m={count_5m} daily={count_daily} "
                f"range={start_day.isoformat()}..{end_day.isoformat()}"
            )
        if errors:
            console.print(f"[yellow]{errors} chunk(s) failed after retries[/yellow]")
            raise typer.Exit(code=1)
    finally:
        db.close()


@app.command("research")
def research_cmd() -> None:
    """Run the strategy research and Gate evaluation."""
    cfg = load_config()
    db = _open_db()
    try:
        result = run_research(db, cfg)
    finally:
        db.close()

    table = Table(title="research gate")
    table.add_column("strategy")
    table.add_column("train n", justify="right")
    table.add_column("train exp", justify="right")
    table.add_column("win", justify="right")
    table.add_column("PF", justify="right")
    table.add_column("DD", justify="right")
    table.add_column("OOS n", justify="right")
    table.add_column("OOS exp", justify="right")
    table.add_column("gate")
    for report in result["strategies"]:
        train = report["train"]
        oos = report["oos"]
        gate = "PASS" if report["gate"]["passed"] else "FAIL"
        style = "green" if report["gate"]["passed"] else "red"
        table.add_row(
            report["name"],
            str(train["n_trades"]),
            f"{train['avg_net_pct']:+.3%}",
            f"{train['win_rate']:.1%}",
            f"{train['profit_factor']:.2f}",
            f"{train['max_drawdown']:.0f}",
            str(oos["n_trades"]),
            f"{oos['avg_net_pct']:+.3%}",
            f"[{style}]{gate}[/{style}]",
        )
    console.print(table)
    console.print(f"JSON: {result['json_path']}")
    console.print(f"HTML: {result['html_path']}")
    if not result["passed"]:
        console.print(
            "[yellow]Gate not passed; do not risk real money. "
            "Use the report to revise or keep monitoring only.[/yellow]"
        )


@app.command("plan")
def plan_cmd() -> None:
    """Print today's T plan and the current constraints."""
    cfg = load_config()
    if not cfg.db_path.exists():
        console.print(f"[yellow]no database at {cfg.db_path}; run `zuot init`[/yellow]")
        raise typer.Exit(code=1)
    today = date.today()
    with Database(cfg.db_path) as db:
        db.init_schema()
        positions = db.positions()
        open_lots = db.open_lots()
        table = Table(title=f"zuot plan {today.isoformat()}")
        table.add_column("symbol")
        table.add_column("name")
        table.add_column("base", justify="right")
        table.add_column("T qty", justify="right")
        table.add_column("last 5m")
        for code in sorted(cfg.symbols):
            symbol = cfg.symbol(code)
            table.add_row(
                code,
                symbol.name,
                str(positions.get(code, symbol.base_qty)),
                str(symbol.t_qty),
                _last_5m(db, code) or "-",
            )
    console.print(table)
    is_td = TradingCalendar().is_trading_day(today)
    console.print(f"trading day: {'yes' if is_td else 'no'}")
    console.print(
        f"rules: one open T globally, one round trip per symbol/day, "
        f"no new sell after {cfg.risk.no_new_sell_after}, "
        f"force buyback {cfg.risk.force_buyback}, daily loss cap "
        f"{cfg.risk.daily_loss_cap:.0f} CNY"
    )
    console.print(f"strategy: {cfg.strategy.name} {cfg.strategy.params}")
    if open_lots:
        console.print("[yellow]open T lot exists; only manage the exit today[/yellow]")
    else:
        console.print("no open T: wait for a 5-minute signal; no trade is required today")
    console.print(
        f"notify: {'DRY-RUN' if cfg.notify.dry_run else 'webhook'}; "
        "signals are advisory and require manual fills"
    )


@app.command("live")
def live_cmd(
    once: bool = typer.Option(False, "--once", help="one poll, then exit"),
    poll: float = typer.Option(3.0, "--poll", help="seconds between quote polls"),
    until: str = typer.Option("15:05", "--until", help="stop time HH:MM"),
) -> None:
    """Run the live signal loop."""
    cfg = load_config()
    cfg.ensure_dirs()
    if not TradingCalendar().is_trading_day(date.today()):
        console.print("[yellow]today is not a weekday trading day; live loop not started[/yellow]")
        raise typer.Exit(code=1)
    passed, detail = gate_status(cfg)
    if not passed:
        console.print(f"[red]research Gate not passed: {detail}[/red]")
        console.print("[red]live signals are disabled until a strategy passes the Gate[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Gate passed: {detail}[/green]")
    db = _open_db()
    notifier = FeishuNotifier(cfg.notify)
    if cfg.notify.dry_run:
        console.print(
            "[yellow]notify.toml dry_run=true: alerts print locally and are not pushed. "
            "Set dry_run=false and ZUOT_FEISHU_WEBHOOK before real use.[/yellow]"
        )
    engine = LiveEngine(cfg, db, notifier, poll_seconds=poll)
    console.print(f"live loop started; stop at {until}; poll={poll}s; Ctrl+C to stop")
    try:
        engine.run(once=once, until=time.fromisoformat(until))
    except KeyboardInterrupt:
        console.print("live loop interrupted")
    finally:
        db.close()


@app.command("fill")
def fill_cmd(
    side: str = typer.Option(..., "--side", help="sell or buy"),
    price: float = typer.Option(..., "--price", help="actual fill price"),
    symbol: str | None = typer.Option(None, "--symbol", help="stock code, e.g. 300308"),
    qty: int | None = typer.Option(None, "--qty", help="shares; defaults to T size / lot size"),
    lot_id: int | None = typer.Option(None, "--lot-id", help="open lot id for a buy"),
    at: str | None = typer.Option(None, "--at", help="fill time HH:MM or ISO; defaults to now"),
) -> None:
    """Record a manual fill. A sell creates an open T lot; a buy closes it."""
    cfg = load_config()
    day = date.today()
    filled_at = _parse_at(at, day)
    side = side.lower()
    if side not in {"sell", "buy"}:
        raise typer.BadParameter("side must be sell or buy")
    if price <= 0:
        raise typer.BadParameter("price must be positive")

    with Database(cfg.db_path) as db:
        db.init_schema()
        ledger = _ledger_for_day(db, cfg, day)
        cost = CostModel(cfg.costs)

        if side == "sell":
            if not symbol or symbol not in cfg.symbols:
                raise typer.BadParameter("sell requires --symbol 300308 or 300476")
            symbol_cfg = cfg.symbol(symbol)
            qty = qty or symbol_cfg.t_qty
            target_pct = cfg.risk.target_pct[symbol][0]
            stop_pct = cfg.risk.stop_pct[symbol]
            target = round(price * (1.0 - target_pct), 2)
            stop = round(price * (1.0 + stop_pct), 2)
            deadline = datetime.combine(
                day, time.fromisoformat(cfg.risk.force_buyback)
            )
            try:
                lot = ledger.open_lot(
                    symbol=symbol,
                    qty=qty,
                    sell_price=price,
                    sell_time=filled_at,
                    target_price=target,
                    stop_price=stop,
                    deadline=deadline,
                    fees=cost.sell_fees(price, qty).total,
                    note="manual sell fill",
                )
            except RiskError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
            lot.id = db.insert_lot(lot)
            db.insert_fill(
                ts=filled_at,
                symbol=symbol,
                side="sell",
                price=price,
                qty=qty,
                fees=lot.fees,
                lot_id=lot.id,
                note="manual sell fill",
            )
            db.insert_audit(
                "fill_sell",
                {"symbol": symbol, "qty": qty, "price": price, "lot_id": lot.id},
            )
            console.print(
                f"recorded sell {symbol} x{qty} @ {price:.2f}; "
                f"target {target:.2f}, stop {stop:.2f}, deadline {deadline:%H:%M}"
            )
            continue_text = (
                f"【ZUOT 已记录卖出】{symbol_cfg.name} {symbol} {qty}股 @ {price:.2f}\n"
                f"目标 {target:.2f} / 止损 {stop:.2f} / 最迟 "
                f"{cfg.risk.force_buyback} 买回"
            )
            FeishuNotifier(cfg.notify).send(continue_text)
        else:
            if lot_id is not None:
                lot = ledger.find_lot(lot_id)
            elif symbol:
                candidates = [lot for lot in ledger.open_lots() if lot.symbol == symbol]
                if len(candidates) != 1:
                    console.print(f"[red]expected exactly one open lot for {symbol}[/red]")
                    raise typer.Exit(code=1)
                lot = candidates[0]
            else:
                candidates = ledger.open_lots()
                if len(candidates) != 1:
                    console.print("[red]specify --lot-id or --symbol[/red]")
                    raise typer.Exit(code=1)
                lot = candidates[0]
            qty = qty or lot.qty
            if qty != lot.qty:
                console.print(
                    f"[red]buy qty {qty} must match open lot qty {lot.qty}[/red]"
                )
                raise typer.Exit(code=1)
            buy_fees = cost.buy_fees(price, qty).total
            try:
                closed = ledger.close_lot(
                    lot.id, buy_price=price, buy_time=filled_at, fees=buy_fees
                )
            except RiskError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
            db.close_lot(
                closed.id,
                buy_price=price,
                buy_time=filled_at,
                fees=closed.fees,
                net_pnl=closed.net_pnl,
            )
            db.insert_fill(
                ts=filled_at,
                symbol=closed.symbol,
                side="buy",
                price=price,
                qty=qty,
                fees=buy_fees,
                lot_id=closed.id,
                note="manual buy fill",
            )
            db.insert_audit(
                "fill_buy",
                {"symbol": closed.symbol, "qty": qty, "price": price, "lot_id": closed.id},
            )
            console.print(
                f"recorded buy {closed.symbol} x{qty} @ {price:.2f}; "
                f"net PnL {closed.net_pnl:+.2f} CNY"
            )
            FeishuNotifier(cfg.notify).send(
                f"【ZUOT 已买回】{cfg.symbol(closed.symbol).name} {closed.symbol} "
                f"{qty}股 @ {price:.2f}，本笔净盈亏 {closed.net_pnl:+.2f} CNY"
            )


@app.command("eod")
def eod_cmd(
    actual: str | None = typer.Option(
        None, "--actual", help="broker positions, e.g. 300308:500,300476:2000"
    ),
) -> None:
    """Write the end-of-day report and reconcile base positions."""
    cfg = load_config()
    day = date.today()
    actual_positions = _parse_actual(actual)
    if not cfg.db_path.exists():
        console.print(f"[yellow]no database at {cfg.db_path}; run `zuot init`[/yellow]")
        raise typer.Exit(code=1)
    with Database(cfg.db_path) as db:
        db.init_schema()
        path = write_daily_report(db, cfg, day, actual=actual_positions)
        open_lots = db.open_lots(day)
        pnl = db.realized_pnl(day)
    console.print(f"report: {path}")
    console.print(f"realized PnL: {pnl:+.2f} CNY")
    if open_lots:
        console.print("[red]open T lots remain; do not close the day until resolved[/red]")
        raise typer.Exit(code=1)
    if actual_positions is not None:
        mismatches = [
            code
            for code in cfg.symbols
            if actual_positions.get(code, 0) != cfg.symbol(code).base_qty
        ]
        if mismatches:
            console.print(f"[red]position mismatch: {mismatches}[/red]")
            raise typer.Exit(code=1)
    console.print("[green]day is flat and reconciled[/green]")


@app.command("guard")
def guard_cmd() -> None:
    """Run the pre-trade risk guard."""
    cfg = load_config()
    failures: list[str] = []
    today = date.today()
    if not TradingCalendar().is_trading_day(today):
        failures.append("today is not a weekday trading day")
    with Database(cfg.db_path) as db:
        db.init_schema()
        positions = db.positions()
        for code in cfg.symbols:
            if code not in positions:
                failures.append(f"{code}: no position seeded")
        for row in db.open_lots():
            lot_day = date.fromisoformat(str(row["date"]))
            if lot_day < today:
                failures.append(
                    f"{row['symbol']}#{row['id']}: open lot from {lot_day} must be resolved"
                )
    quotes = fetch_quotes([symbol.code for symbol in cfg.symbols.values()])
    if not quotes:
        failures.append("Sina and Tencent both failed")
    table = Table(title="zuot guard")
    table.add_column("check")
    table.add_column("detail")
    for code in sorted(cfg.symbols):
        symbol = cfg.symbol(code)
        quote = quotes.get(code)
        if quote is None or quote.price <= 0:
            failures.append(f"{code}: no valid quote")
            table.add_row(
                f"{code} loss budget",
                f"base={positions.get(code, symbol.base_qty)} quote=missing",
            )
            continue
        stop_loss = symbol.t_qty * quote.price * cfg.risk.stop_pct[code]
        cost = CostModel(cfg.costs).round_trip_cost(quote.price, symbol.t_qty, symbol.tick)
        table.add_row(
            f"{code} loss budget",
            f"stop≈{stop_loss + cost:.0f} CNY, cap={cfg.risk.daily_loss_cap:.0f}",
        )
        if stop_loss + cost > cfg.risk.daily_loss_cap:
            failures.append(f"{code}: stop + cost exceeds daily cap")
    for failure in failures:
        table.add_row("FAIL", failure)
    console.print(table)
    if failures:
        console.print(f"[red]{len(failures)} guard check(s) failed[/red]")
        raise typer.Exit(code=1)
    console.print("[green]guard passed[/green]")


@app.command("replay")
def replay_cmd(
    start: str = typer.Option(..., "--start", help="start date YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="end date YYYY-MM-DD"),
    strategy: str | None = typer.Option(None, "--strategy", help="override strategy name"),
) -> None:
    """Replay stored 5-minute bars through the backtest engine."""
    cfg = load_config()
    name = strategy or cfg.strategy.name
    with Database(cfg.db_path) as db:
        db.init_schema()
        result = run_backtest(
            db,
            cfg,
            date.fromisoformat(start),
            date.fromisoformat(end),
            build_strategy(name, cfg.strategy.params if name == cfg.strategy.name else {}),
        )
    console.print(result.metrics)
    console.print(trades_frame(result).to_string(index=False))


if __name__ == "__main__":
    app()
