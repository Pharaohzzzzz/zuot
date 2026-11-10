from __future__ import annotations

from pathlib import Path

import pytest

from zuot.config import load_config

ROOT = Path(__file__).resolve().parents[1]


def test_loads_both_symbols() -> None:
    cfg = load_config(ROOT)
    assert set(cfg.symbols) == {"300308", "300476"}
    assert cfg.symbols["300308"].name == "中际旭创"
    assert cfg.symbols["300308"].base_qty == 500
    assert cfg.symbols["300308"].t_qty == 200
    assert cfg.symbols["300476"].base_qty == 2000
    assert cfg.symbols["300476"].t_qty == 1000


def test_risk_and_cost_values() -> None:
    cfg = load_config(ROOT)
    assert cfg.costs.commission_rate == pytest.approx(0.000085)
    assert cfg.costs.min_commission == pytest.approx(5.0)
    assert cfg.costs.stamp_sell_rate == pytest.approx(0.0005)
    assert cfg.risk.daily_loss_cap == pytest.approx(2000.0)
    assert cfg.risk.max_open_t == 1
    assert cfg.risk.max_round_trips_per_symbol_per_day == 1
    assert cfg.risk.stop_pct["300308"] == pytest.approx(0.015)
    assert cfg.risk.stop_pct["300476"] == pytest.approx(0.014)


def test_unknown_symbol_raises() -> None:
    cfg = load_config(ROOT)
    with pytest.raises(KeyError):
        cfg.symbol("000001")


def test_missing_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path)
