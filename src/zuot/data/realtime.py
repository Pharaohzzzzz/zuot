"""Level-1 realtime quotes from Sina (primary) and Tencent (fallback)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

import httpx

SINA_URL = "https://hq.sinajs.cn/list={codes}"
TENCENT_URL = "https://qt.gtimg.cn/q={codes}"
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}

_SINA_LINE = re.compile(r'hq_str_([a-z]{2}\d{6})="([^"]*)"')
_TENCENT_LINE = re.compile(r'v_([a-z]{2}\d{6})="([^"]*)"')


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    open: float
    prev_close: float
    price: float
    high: float
    low: float
    bid: float
    ask: float
    volume: int
    amount: float
    bids: tuple[tuple[float, int], ...]
    asks: tuple[tuple[float, int], ...]
    date: str
    time: str
    source: str

    @property
    def change_pct(self) -> float:
        if not self.prev_close:
            return 0.0
        return self.price / self.prev_close - 1.0


def _f(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _i(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def parse_sina(text: str) -> dict[str, Quote]:
    quotes: dict[str, Quote] = {}
    for full_code, payload in _SINA_LINE.findall(text):
        parts = payload.split(",")
        if len(parts) < 32:
            continue
        symbol = full_code[2:]
        bids = tuple(
            (_f(parts[11 + 2 * i]), _i(parts[10 + 2 * i])) for i in range(5)
        )
        asks = tuple(
            (_f(parts[21 + 2 * i]), _i(parts[20 + 2 * i])) for i in range(5)
        )
        quotes[symbol] = Quote(
            symbol=symbol,
            name=parts[0].strip(),
            open=_f(parts[1]),
            prev_close=_f(parts[2]),
            price=_f(parts[3]) or _f(parts[2]),
            high=_f(parts[4]),
            low=_f(parts[5]),
            bid=_f(parts[6]),
            ask=_f(parts[7]),
            volume=_i(parts[8]),
            amount=_f(parts[9]),
            bids=bids,
            asks=asks,
            date=parts[30],
            time=parts[31],
            source="sina",
        )
    return quotes


def parse_tencent(text: str) -> dict[str, Quote]:
    quotes: dict[str, Quote] = {}
    for full_code, payload in _TENCENT_LINE.findall(text):
        parts = payload.split("~")
        if len(parts) < 49:
            continue
        symbol = full_code[2:]
        price = _f(parts[3]) or _f(parts[4])
        bids = tuple((_f(parts[9 + 2 * i]), _i(parts[10 + 2 * i])) for i in range(5))
        asks = tuple((_f(parts[19 + 2 * i]), _i(parts[20 + 2 * i])) for i in range(5))
        quotes[symbol] = Quote(
            symbol=symbol,
            name=parts[1].strip(),
            open=_f(parts[5]),
            prev_close=_f(parts[4]),
            price=price,
            high=_f(parts[33]),
            low=_f(parts[34]),
            bid=bids[0][0],
            ask=asks[0][0],
            volume=_i(parts[36]) * 100,  # Tencent reports hands
            amount=_f(parts[37]) * 10_000,  # ten-thousand CNY
            bids=bids,
            asks=asks,
            date=parts[30][:10],
            time=parts[30][11:],
            source="tencent",
        )
    return quotes


def _codes(symbols: Iterable[str], exchange: str) -> str:
    return ",".join(f"{exchange}{code}" for code in symbols)


def fetch_sina(symbols: Iterable[str], exchange: str = "sz") -> dict[str, Quote]:
    codes = _codes(symbols, exchange)
    with httpx.Client(trust_env=False, timeout=8.0) as client:
        response = client.get(SINA_URL.format(codes=codes), headers=HEADERS)
    response.raise_for_status()
    return parse_sina(response.content.decode("gbk", errors="replace"))


def fetch_tencent(symbols: Iterable[str], exchange: str = "sz") -> dict[str, Quote]:
    codes = _codes(symbols, exchange)
    with httpx.Client(trust_env=False, timeout=8.0) as client:
        response = client.get(TENCENT_URL.format(codes=codes), headers=HEADERS)
    response.raise_for_status()
    return parse_tencent(response.content.decode("gbk", errors="replace"))


def fetch_quotes(symbols: Iterable[str], exchange: str = "sz") -> dict[str, Quote]:
    """Sina first, Tencent on any failure. Returns an empty dict if both fail."""
    try:
        quotes = fetch_sina(symbols, exchange)
        if quotes:
            return quotes
    except Exception:
        pass
    try:
        return fetch_tencent(symbols, exchange)
    except Exception:
        return {}
