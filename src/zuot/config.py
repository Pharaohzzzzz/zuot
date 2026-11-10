"""Configuration loading for zuot.

All money values are CNY. Percentages are decimals (0.015 == 1.5%).
Secrets never live in these files: the Feishu webhook is read from an env var.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SymbolConfig:
    code: str
    name: str
    exchange: str
    base_qty: int
    t_qty: int
    tick: float = 0.01
    limit_pct: float = 0.20

    @property
    def full_code(self) -> str:
        return f"{self.exchange}{self.code}"


@dataclass(frozen=True)
class CostConfig:
    commission_rate: float = 0.000085
    min_commission: float = 5.0
    stamp_sell_rate: float = 0.0005
    transfer_rate: float = 0.00001
    slippage_ticks: int = 1


@dataclass(frozen=True)
class RiskConfig:
    daily_loss_cap: float = 2000.0
    max_open_t: int = 1
    max_round_trips_per_symbol_per_day: int = 1
    no_new_sell_after: str = "14:00"
    force_buyback: str = "14:50"
    limit_buffer_pct: float = 0.02
    min_gap_pct: float = -0.03
    max_gap_pct: float = 0.07
    min_atr_pct: float = 0.035
    max_atr_pct: float = 0.18
    stop_pct: dict[str, float] = field(
        default_factory=lambda: {"300308": 0.015, "300476": 0.014}
    )
    target_pct: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "300308": (0.015, 0.025),
            "300476": (0.014, 0.025),
        }
    )


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "gap_fade"
    params: dict[str, Any] = field(default_factory=dict)
    lock_path: str = "strategy.lock.json"


@dataclass(frozen=True)
class NotifyConfig:
    transport: str = "webhook"
    webhook_env: str = "ZUOT_FEISHU_WEBHOOK"
    chat_id: str | None = None
    dry_run: bool = True


@dataclass(frozen=True)
class AppConfig:
    root: Path
    timezone: str
    data_dir: Path
    log_dir: Path
    report_dir: Path
    db_path: Path
    symbols: dict[str, SymbolConfig]
    costs: CostConfig
    risk: RiskConfig
    strategy: StrategyConfig
    notify: NotifyConfig

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.log_dir, self.report_dir, self.db_path.parent):
            path.mkdir(parents=True, exist_ok=True)

    def symbol(self, code: str) -> SymbolConfig:
        try:
            return self.symbols[code]
        except KeyError as exc:
            raise KeyError(f"unknown symbol {code!r}; configured: {sorted(self.symbols)}") from exc


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing config file: {path}")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _symbols(raw: dict[str, Any]) -> dict[str, SymbolConfig]:
    out: dict[str, SymbolConfig] = {}
    for code, item in raw.get("symbols", {}).items():
        out[str(code)] = SymbolConfig(
            code=str(code),
            name=str(item["name"]),
            exchange=str(item.get("exchange", "sz")),
            base_qty=int(item["base_qty"]),
            t_qty=int(item["t_qty"]),
            tick=float(item.get("tick", 0.01)),
            limit_pct=float(item.get("limit_pct", 0.20)),
        )
    if not out:
        raise ValueError("no symbols configured under [symbols.*]")
    return out


def _costs(raw: dict[str, Any]) -> CostConfig:
    section = raw.get("costs", {})
    return CostConfig(
        commission_rate=float(section.get("commission_rate", 0.000085)),
        min_commission=float(section.get("min_commission", 5.0)),
        stamp_sell_rate=float(section.get("stamp_sell_rate", 0.0005)),
        transfer_rate=float(section.get("transfer_rate", 0.00001)),
        slippage_ticks=int(section.get("slippage_ticks", 1)),
    )


def _risk(raw: dict[str, Any]) -> RiskConfig:
    section = raw.get("risk", {})
    stop_pct = {str(k): float(v) for k, v in section.get("stop_pct", {}).items()}
    target_pct = {
        str(k): (float(v[0]), float(v[1])) for k, v in section.get("target_pct", {}).items()
    }
    defaults = RiskConfig()
    return RiskConfig(
        daily_loss_cap=float(section.get("daily_loss_cap", defaults.daily_loss_cap)),
        max_open_t=int(section.get("max_open_t", defaults.max_open_t)),
        max_round_trips_per_symbol_per_day=int(
            section.get(
                "max_round_trips_per_symbol_per_day",
                defaults.max_round_trips_per_symbol_per_day,
            )
        ),
        no_new_sell_after=str(section.get("no_new_sell_after", defaults.no_new_sell_after)),
        force_buyback=str(section.get("force_buyback", defaults.force_buyback)),
        limit_buffer_pct=float(section.get("limit_buffer_pct", defaults.limit_buffer_pct)),
        min_gap_pct=float(section.get("min_gap_pct", defaults.min_gap_pct)),
        max_gap_pct=float(section.get("max_gap_pct", defaults.max_gap_pct)),
        min_atr_pct=float(section.get("min_atr_pct", defaults.min_atr_pct)),
        max_atr_pct=float(section.get("max_atr_pct", defaults.max_atr_pct)),
        stop_pct=stop_pct or defaults.stop_pct,
        target_pct=target_pct or defaults.target_pct,
    )


def _strategy(raw: dict[str, Any]) -> StrategyConfig:
    section = raw.get("strategy", {})
    return StrategyConfig(
        name=str(section.get("name", "gap_fade")),
        params=dict(section.get("params", {})),
        lock_path=str(section.get("lock_path", "strategy.lock.json")),
    )


def _notify(raw: dict[str, Any]) -> NotifyConfig:
    section = raw.get("notify", {})
    chat_id = section.get("chat_id")
    return NotifyConfig(
        transport=str(section.get("transport", "webhook")),
        webhook_env=str(section.get("webhook_env", "ZUOT_FEISHU_WEBHOOK")),
        chat_id=str(chat_id) if chat_id else None,
        dry_run=bool(section.get("dry_run", True)),
    )


def load_config(root: str | Path | None = None) -> AppConfig:
    root_path = Path(root or DEFAULT_ROOT).resolve()
    base = _read(root_path / "configs" / "base.toml")
    app = base.get("app", {})
    data_dir = (root_path / str(app.get("data_dir", "data"))).resolve()
    log_dir = (root_path / str(app.get("log_dir", "logs"))).resolve()
    report_dir = (root_path / str(app.get("report_dir", "reports"))).resolve()
    db_path = (root_path / str(app.get("db_path", "data/zuot.db"))).resolve()
    return AppConfig(
        root=root_path,
        timezone=str(app.get("timezone", "Asia/Shanghai")),
        data_dir=data_dir,
        log_dir=log_dir,
        report_dir=report_dir,
        db_path=db_path,
        symbols=_symbols(_read(root_path / "configs" / "symbols.toml")),
        costs=_costs(_read(root_path / "configs" / "costs.toml")),
        risk=_risk(_read(root_path / "configs" / "risk.toml")),
        strategy=_strategy(_read(root_path / "configs" / "strategy.toml")),
        notify=_notify(_read(root_path / "configs" / "notify.toml")),
    )
