from datetime import datetime

from zuot.live.bar_builder import BarBuilder, bar_end


def test_bar_end_boundaries():
    assert bar_end(datetime(2026, 11, 10, 9, 30)) == datetime(2026, 11, 10, 9, 35)
    assert bar_end(datetime(2026, 11, 10, 9, 35)) == datetime(2026, 11, 10, 9, 40)
    assert bar_end(datetime(2026, 11, 10, 11, 30)) == datetime(2026, 11, 10, 11, 35)
    assert bar_end(datetime(2026, 11, 10, 13, 0)) == datetime(2026, 11, 10, 13, 5)
    assert bar_end(datetime(2026, 11, 10, 15, 0)) == datetime(2026, 11, 10, 15, 0)


def test_builder_finalizes_on_next_bar():
    builder = BarBuilder("300308")
    assert (
        builder.update(
            price=100.0,
            cumulative_volume=1000,
            cumulative_amount=100_000,
            ts=datetime(2026, 11, 10, 9, 30),
        )
        is None
    )
    assert (
        builder.update(
            price=101.0,
            cumulative_volume=2000,
            cumulative_amount=201_000,
            ts=datetime(2026, 11, 10, 9, 34),
        )
        is None
    )
    bar = builder.update(
        price=102.0,
        cumulative_volume=3000,
        cumulative_amount=303_000,
        ts=datetime(2026, 11, 10, 9, 35),
    )
    assert bar is not None
    assert bar.dt == datetime(2026, 11, 10, 9, 35)
    assert bar.open == 100.0
    assert bar.high == 101.0
    assert bar.low == 100.0
    assert bar.close == 101.0
    assert bar.volume == 2000.0

    last = builder.flush()
    assert last is not None
    assert last.dt == datetime(2026, 11, 10, 9, 40)
    assert last.volume == 1000.0


def test_builder_ignores_preopen_and_zero_price():
    builder = BarBuilder("300308")
    assert (
        builder.update(
            price=100.0,
            cumulative_volume=1,
            cumulative_amount=100,
            ts=datetime(2026, 11, 10, 9, 25),
        )
        is None
    )
    assert builder.bars == []
    assert (
        builder.update(
            price=0.0,
            cumulative_volume=1,
            cumulative_amount=100,
            ts=datetime(2026, 11, 10, 9, 31),
        )
        is None
    )
