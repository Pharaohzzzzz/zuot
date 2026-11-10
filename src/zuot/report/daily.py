"""End-of-day markdown report."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..config import AppConfig
from ..db import Database


def write_daily_report(
    db: Database,
    cfg: AppConfig,
    day: date,
    *,
    actual: dict[str, int] | None = None,
) -> Path:
    lots = db.lots_for_day(day)
    positions = db.positions()
    pnl = db.realized_pnl(day)

    lines = [
        f"# zuot 日报 {day.isoformat()}",
        "",
        "## 当日做T",
        "",
    ]
    if not lots:
        lines.append("今天没有做T。")
    else:
        lines += [
            "| id | symbol | qty | sell | buy | target | stop | status | net |",
            "|---:|---|---:|---:|---:|---:|---:|---|---:|",
        ]
        for row in lots:
            buy = f"{row['buy_price']:.2f}" if row["buy_price"] is not None else "-"
            lines.append(
                f"| {row['id']} | {row['symbol']} | {row['qty']} | "
                f"{row['sell_price']:.2f} | {buy} | {row['target_price']:.2f} | "
                f"{row['stop_price']:.2f} | {row['status']} | {row['net_pnl']:.2f} |"
            )
    lines += ["", f"已实现净盈亏：**{pnl:.2f} CNY**", "", "## 持仓核对", ""]
    mismatches = 0
    for code in sorted(cfg.symbols):
        symbol = cfg.symbol(code)
        base = positions.get(code, symbol.base_qty)
        seen = actual.get(code, base) if actual is not None else base
        mark = "OK" if seen == base else "MISMATCH"
        if mark != "OK":
            mismatches += 1
        lines.append(f"- {code} {symbol.name}: 基准 {base} 股，实际 {seen} 股 — {mark}")
    if actual is None:
        lines.append("- 未提供券商实际持仓；以基准持仓核对。")
    lines += ["", "## 风险", ""]
    open_lots = [row for row in lots if row["status"] == "OPEN"]
    if open_lots:
        lines.append(f"- **仍有 {len(open_lots)} 笔未平T，必须人工处理。**")
    else:
        lines.append("- 无未平T，底仓已恢复。")
    if mismatches:
        lines.append(f"- **{mismatches} 个标的中持仓不匹配，先停止交易。**")
    lines += ["", "## 备注", "", "- 人工下单；本报告不构成自动交易指令。"]

    cfg.ensure_dirs()
    path = cfg.report_dir / f"daily_{day.isoformat()}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
