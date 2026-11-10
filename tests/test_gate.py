from __future__ import annotations

import json
from dataclasses import replace

from zuot.config import load_config
from zuot.research.gate import gate_status


def _cfg(tmp_path):
    return replace(load_config(), report_dir=tmp_path)


def test_missing_report_blocks_live(tmp_path):
    ok, detail = gate_status(_cfg(tmp_path))
    assert not ok
    assert "no research report" in detail


def test_failed_report_blocks_live(tmp_path):
    (tmp_path / "research_20260101_000000.json").write_text(
        json.dumps({"generated_at": "2026-01-01T00:00:00", "passed": []}),
        encoding="utf-8",
    )
    ok, detail = gate_status(_cfg(tmp_path))
    assert not ok
    assert "no strategy passed" in detail


def test_passed_report_allows_live(tmp_path):
    (tmp_path / "research_20260101_000000.json").write_text(
        json.dumps({"generated_at": "2026-01-01T00:00:00", "passed": ["vwap_reversion"]}),
        encoding="utf-8",
    )
    ok, detail = gate_status(_cfg(tmp_path))
    assert ok
    assert "vwap_reversion" in detail
