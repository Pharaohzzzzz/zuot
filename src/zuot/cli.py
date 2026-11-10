"""Command-line entry points.

Phase 0 exposes the scaffolding commands. Data/research/live commands are
registered now so the surface is stable, but they exit non-zero until the
corresponding phase lands.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import date, datetime, timedelta

import httpx
import typer
from rich.console import Console
from rich.table import Table

from .config import load_config
from .data.baostock_source import chunk_ranges, fetch_5m, fetch_daily, session
from .data.realtime import fetch_quotes
from .data.store import upsert_5m, upsert_daily
from .db import Database
from .research.runner import run_research

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
        for symbol in cfg.symbols.values():
            code = f"{symbol.exchange}.{symbol.code}"
            count_5m = 0
            for chunk_start, chunk_end in chunk_ranges(start_day, end_day):
                # A long-lived BaoStock session starts hanging after roughly 20
                # queries. Re-login for every quarter so a backfill can finish.
                with session():
                    bars = fetch_5m(code, chunk_start, chunk_end)
                written = upsert_5m(db, bars)
                count_5m += written
                console.print(
                    f"{code} {chunk_start.isoformat()}..{chunk_end.isoformat()}: "
                    f"{written} bars"
                )
            count_daily = 0
            if daily:
                with session():
                    count_daily = upsert_daily(
                        db, fetch_daily(code, start_day, end_day)
                    )
            console.print(
                f"{code}: 5m={count_5m} daily={count_daily} "
                f"range={start_day.isoformat()}..{end_day.isoformat()}"
            )
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
    """Print today's T plan."""
    _not_implemented("plan")


@app.command("live")
def live_cmd() -> None:
    """Run the live signal loop."""
    _not_implemented("live")


@app.command("fill")
def fill_cmd() -> None:
    """Record a manual fill."""
    _not_implemented("fill")


@app.command("eod")
def eod_cmd() -> None:
    """Run the end-of-day report and reconciliation."""
    _not_implemented("eod")


@app.command("guard")
def guard_cmd() -> None:
    """Run the pre-trade risk guard."""
    _not_implemented("guard")


@app.command("replay")
def replay_cmd() -> None:
    """Replay a trading day from stored ticks/bars."""
    _not_implemented("replay")


if __name__ == "__main__":
    app()
