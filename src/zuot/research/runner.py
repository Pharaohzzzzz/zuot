"""Research runner: train/OOS backtests, sensitivity and Gate evaluation."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

import pandas as pd
from jinja2 import Template

from ..backtest.engine import BacktestResult, run_backtest
from ..config import AppConfig
from ..db import Database
from ..strategy.strategies import (
    EntrySignal,
    Strategy,
    build_strategy,
)

TRAIN_START = date(2020, 1, 1)
TRAIN_END = date(2023, 12, 31)
OOS_START = date(2024, 1, 1)
OOS_END = date(2026, 11, 10)

CANDIDATES = ("gap_fade", "vwap_reversion", "prior_high_fade", "afternoon_pulse")
SENSITIVITY_KEYS = {
    "gap_fade": ("gap_min", "vwap_z", "rsi_min", "target_pct"),
    "vwap_reversion": ("vwap_z", "rsi_min", "volume_ratio_max", "target_pct"),
    "prior_high_fade": ("probe_pct", "fail_pct", "target_pct"),
    "afternoon_pulse": ("min_ret_from_open", "vwap_z", "target_pct"),
}


class RandomEntryStrategy:
    """Uniform random entry inside the strategy's time window.

    Used as the benchmark the real strategy must beat. Multiple seeds are
    averaged; it is deliberately simple, not a second optimizer.
    """

    name = "random_entry"

    def __init__(
        self,
        seed: int,
        entry_start: time,
        entry_end: time,
        target_pct: float,
        stop_pct: float,
        probability: float = 0.25,
    ) -> None:
        self.rng = random.Random(seed)
        self.entry_start = entry_start
        self.entry_end = entry_end
        self.target_pct = target_pct
        self.stop_pct = stop_pct
        self.probability = probability

    def evaluate(self, row: pd.Series) -> EntrySignal | None:
        if not (self.entry_start <= row["time"] <= self.entry_end):
            return None
        if self.rng.random() >= self.probability:
            return None
        return EntrySignal(0.0, "random_entry", self.target_pct, self.stop_pct)


@dataclass
class StrategyReport:
    name: str
    params: dict[str, Any]
    train: dict[str, Any]
    oos: dict[str, Any]
    random_avg_net_pct: float
    sensitivity: list[dict[str, Any]]
    gate: dict[str, Any]


def _metrics(result: BacktestResult) -> dict[str, Any]:
    return result.metrics


def _base_params(cfg: AppConfig, name: str) -> dict[str, Any]:
    return dict(cfg.strategy.params) if name == cfg.strategy.name else {}


def _random_benchmark(
    db: Database,
    cfg: AppConfig,
    base: Strategy,
    seeds: int = 10,
) -> float:
    start = getattr(base, "entry_start", time(9, 45))
    end = getattr(base, "entry_end", time(14, 0))
    target_pct = float(getattr(base, "target_pct", 0.015))
    stop_pct = float(getattr(base, "stop_pct", 0.015))
    values = []
    for seed in range(seeds):
        strategy = RandomEntryStrategy(seed, start, end, target_pct, stop_pct)
        result = run_backtest(db, cfg, TRAIN_START, TRAIN_END, strategy)
        if result.metrics["n_trades"]:
            values.append(float(result.metrics["avg_net_pct"]))
    return round(sum(values) / len(values), 6) if values else 0.0


def _sensitivity(
    db: Database,
    cfg: AppConfig,
    name: str,
) -> list[dict[str, Any]]:
    base_params = _base_params(cfg, name)
    base = build_strategy(name, base_params)
    out: list[dict[str, Any]] = []
    for key in SENSITIVITY_KEYS.get(name, ()):
        base_value = getattr(base, key, None)
        if not isinstance(base_value, (int, float)):
            continue
        for label, factor in (("low", 0.8), ("high", 1.2)):
            params = {**base_params, key: float(base_value) * factor}
            variant = build_strategy(name, params)
            result = run_backtest(db, cfg, TRAIN_START, TRAIN_END, variant)
            out.append(
                {
                    "param": key,
                    "variant": label,
                    "value": round(float(base_value) * factor, 6),
                    "n_trades": result.metrics["n_trades"],
                    "avg_net_pct": result.metrics["avg_net_pct"],
                    "total_pnl": result.metrics["total_pnl"],
                }
            )
    return out


def _gate(
    train: dict[str, Any],
    oos: dict[str, Any],
    random_avg: float,
    sensitivity: list[dict[str, Any]],
) -> dict[str, Any]:
    by_year = train.get("by_year", {})
    positive_years = sum(1 for bucket in by_year.values() if bucket["pnl"] > 0)
    years_total = len(by_year)
    by_symbol = train.get("by_symbol", {})
    both_symbols = bool(by_symbol) and all(bucket["pnl"] > 0 for bucket in by_symbol.values())
    sensitivity_ok = bool(sensitivity) and all(
        item["avg_net_pct"] > 0 for item in sensitivity
    )
    checks = {
        "train_trades_ge_80": train["n_trades"] >= 80,
        "train_expectancy_ge_0.15pct": train["avg_net_pct"] >= 0.0015,
        "train_win_rate_ge_55pct": train["win_rate"] >= 0.55,
        "train_profit_factor_ge_1.3": train["profit_factor"] >= 1.3,
        "train_positive_years_ge_60pct": years_total > 0
        and positive_years / years_total >= 0.60,
        "train_both_symbols_positive": both_symbols,
        "train_max_drawdown_le_4000": train["max_drawdown"] <= 4000.0,
        "sensitivity_positive": sensitivity_ok,
        "oos_trades_ge_30": oos["n_trades"] >= 30,
        "oos_positive": oos["total_pnl"] > 0 and oos["avg_net_pct"] > 0,
        "beat_random_ge_0.10pct": (train["avg_net_pct"] - random_avg) >= 0.001,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "positive_years": positive_years,
        "years_total": years_total,
    }


def _run(
    db: Database,
    cfg: AppConfig,
    name: str,
    start: date,
    end: date,
) -> BacktestResult:
    return run_backtest(db, cfg, start, end, build_strategy(name, _base_params(cfg, name)))


def run_research(db: Database, cfg: AppConfig) -> dict[str, Any]:
    cfg.ensure_dirs()
    reports: list[StrategyReport] = []
    for name in CANDIDATES:
        train_result = _run(db, cfg, name, TRAIN_START, TRAIN_END)
        oos_result = _run(db, cfg, name, OOS_START, OOS_END)
        base = build_strategy(name, _base_params(cfg, name))
        params = {
            key: value
            for key, value in vars(base).items()
            if isinstance(value, (int, float, str))
        }
        random_avg = _random_benchmark(db, cfg, base)
        sensitivity = _sensitivity(db, cfg, name)
        gate = _gate(train_result.metrics, oos_result.metrics, random_avg, sensitivity)
        reports.append(
            StrategyReport(
                name=name,
                params=params,
                train=train_result.metrics,
                oos=oos_result.metrics,
                random_avg_net_pct=random_avg,
                sensitivity=sensitivity,
                gate=gate,
            )
        )

    passed = [report.name for report in reports if report.gate["passed"]]
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "train": [TRAIN_START.isoformat(), TRAIN_END.isoformat()],
        "oos": [OOS_START.isoformat(), OOS_END.isoformat()],
        "passed": passed,
        "strategies": [
            {
                "name": report.name,
                "params": report.params,
                "train": report.train,
                "oos": report.oos,
                "random_avg_net_pct": report.random_avg_net_pct,
                "sensitivity": report.sensitivity,
                "gate": report.gate,
            }
            for report in reports
        ],
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = cfg.report_dir / f"research_{stamp}.json"
    html_path = cfg.report_dir / f"research_{stamp}.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_render_html(payload), encoding="utf-8")
    payload["json_path"] = str(json_path)
    payload["html_path"] = str(html_path)
    return payload


def _render_html(payload: dict[str, Any]) -> str:
    template = Template(
        """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>zuot research</title>
<style>
body{font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;margin:24px;color:#222}
table{border-collapse:collapse;margin:12px 0;width:100%}
th,td{border:1px solid #ccc;padding:6px 8px;text-align:right}
th:first-child,td:first-child{text-align:left}
.pass{color:#0a7a2f;font-weight:700}.fail{color:#b00020;font-weight:700}
h1,h2{font-weight:600}
</style></head><body>
<h1>zuot 研究 Gate</h1>
<p>生成时间 {{ payload.generated_at }}；训练 {{ payload.train[0] }}..{{ payload.train[1] }}；
样本外 {{ payload.oos[0] }}..{{ payload.oos[1] }}。通过策略：
<span class="{% if payload.passed %}pass{% else %}fail{% endif %}">
{{ payload.passed|join(', ') if payload.passed else '无' }}</span></p>
<table><tr><th>策略</th><th>训练笔数</th><th>训练期望</th><th>胜率</th>
<th>利润因子</th><th>回撤</th><th>OOS 笔数</th><th>OOS 期望</th><th>Gate</th></tr>
{% for s in payload.strategies %}
<tr><td>{{ s.name }}</td><td>{{ s.train.n_trades }}</td>
<td>{{ '%.3f%%'|format(s.train.avg_net_pct * 100) }}</td>
<td>{{ '%.1f%%'|format(s.train.win_rate * 100) }}</td>
<td>{{ '%.2f'|format(s.train.profit_factor) }}</td>
<td>{{ '%.0f'|format(s.train.max_drawdown) }}</td>
<td>{{ s.oos.n_trades }}</td>
<td>{{ '%.3f%%'|format(s.oos.avg_net_pct * 100) }}</td>
<td class="{% if s.gate.passed %}pass{% else %}fail{% endif %}">
{{ 'PASS' if s.gate.passed else 'FAIL' }}</td></tr>
{% endfor %}</table>
{% for s in payload.strategies %}
<h2>{{ s.name }} <span class="{% if s.gate.passed %}pass{% else %}fail{% endif %}">
{{ 'PASS' if s.gate.passed else 'FAIL' }}</span></h2>
<table><tr><th>检查</th><th>结果</th></tr>
{% for key, value in s.gate.checks.items() %}
<tr><td>{{ key }}</td><td class="{% if value %}pass{% else %}fail{% endif %}">
{{ 'OK' if value else 'NO' }}</td></tr>
{% endfor %}</table>
<p>随机入场基准：{{ '%.3f%%'|format(s.random_avg_net_pct * 100) }} / 笔</p>
<table><tr><th>参数</th><th>变体</th><th>值</th><th>笔数</th><th>期望</th><th>总盈亏</th></tr>
{% for item in s.sensitivity %}
<tr><td>{{ item.param }}</td><td>{{ item.variant }}</td><td>{{ item.value }}</td>
<td>{{ item.n_trades }}</td><td>{{ '%.3f%%'|format(item.avg_net_pct * 100) }}</td>
<td>{{ '%.0f'|format(item.total_pnl) }}</td></tr>
{% endfor %}</table>
{% endfor %}
<p style="color:#666">研究结果不构成投资建议；Gate 未通过不得投入真钱。</p>
</body></html>"""
    )
    return template.render(payload=payload)
