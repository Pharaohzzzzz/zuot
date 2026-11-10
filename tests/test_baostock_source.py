from __future__ import annotations

from datetime import date, datetime

from zuot.data import baostock_source


def test_parse_time() -> None:
    assert baostock_source.parse_time("20240102093500000") == datetime(2024, 1, 2, 9, 35)


def test_chunk_ranges_are_half_years() -> None:
    chunks = list(baostock_source.chunk_ranges(date(2024, 3, 1), date(2025, 8, 1)))
    assert chunks == [
        (date(2024, 3, 1), date(2024, 6, 30)),
        (date(2024, 7, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 6, 30)),
        (date(2025, 7, 1), date(2025, 8, 1)),
    ]


class FakeResultSet:
    error_code = "0"
    error_msg = "success"

    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows
        self.index = -1

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


def test_fetch_5m_parses_rows(monkeypatch) -> None:
    rows = [
        [
            "2024-01-02",
            "20240102093500000",
            "sz.300308",
            "112.85",
            "112.98",
            "110.70",
            "111.40",
            "2098800",
            "234821472.0000",
            "3",
        ]
    ]

    def fake_query(*args, **kwargs):
        return FakeResultSet(rows)

    monkeypatch.setattr(baostock_source.bs, "query_history_k_data_plus", fake_query)
    bars = baostock_source.fetch_5m("sz.300308", date(2024, 1, 2), date(2024, 1, 2))
    assert len(bars) == 1
    bar = bars[0]
    assert bar.symbol == "300308"
    assert bar.dt == datetime(2024, 1, 2, 9, 35)
    assert bar.open == 112.85
    assert bar.close == 111.4
    assert bar.volume == 2_098_800
