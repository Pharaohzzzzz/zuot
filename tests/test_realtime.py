from __future__ import annotations

from zuot.data.realtime import parse_sina, parse_tencent

SINA_TEXT = (
    'var hq_str_sz300308="中际旭创,480.000,471.000,471.000,488.000,469.580,'
    "471.000,471.010,12345678,5800000000.000,"
    "100,471.000,200,470.990,300,470.980,400,470.970,500,470.960,"
    '100,471.010,200,471.020,300,471.030,400,471.040,500,471.050,'
    '2026-11-10,15:00:00,00";\n'
)

TENCENT_TEXT = (
    'v_sz300308="51~中际旭创~300308~471.00~470.00~480.00~123456~1~1~'
    "470.99~100~470.98~200~470.97~300~470.96~400~470.95~500~"
    "471.01~100~471.02~200~471.03~300~471.04~400~471.05~500~"
    '~20261110150000~1.00~0.21~488.00~469.58~471.00/123456/5800000000~'
    '123456~580000~1.2~30~0~488.00~469.58~3.9~100~100~5.0~517.00~423.00";\n'
)


def test_parse_sina_full_quote() -> None:
    quotes = parse_sina(SINA_TEXT)
    quote = quotes["300308"]
    assert quote.name == "中际旭创"
    assert quote.price == 471.0
    assert quote.prev_close == 471.0
    assert quote.change_pct == 0.0
    assert quote.bids[0] == (471.0, 100)
    assert quote.asks[0] == (471.01, 100)
    assert quote.source == "sina"
    assert quote.time == "15:00:00"


def test_parse_sina_suspended_falls_back_to_prev_close() -> None:
    text = SINA_TEXT.replace(",471.000,471.000,488.000,", ",0.000,471.000,0.000,")
    quote = parse_sina(text)["300308"]
    assert quote.price == 471.0


def test_parse_tencent() -> None:
    quote = parse_tencent(TENCENT_TEXT)["300308"]
    assert quote.name == "中际旭创"
    assert quote.price == 471.0
    assert quote.high == 488.0
    assert quote.low == 469.58
    assert quote.volume == 12_345_600
    assert quote.amount == 5_800_000_000
    assert quote.source == "tencent"


def test_parse_empty_text() -> None:
    assert parse_sina("") == {}
    assert parse_tencent("") == {}
