"""Probe BaoStock 5-minute history for the two symbols.

Run: uv run python scripts/spike_baostock.py
"""

from __future__ import annotations

import baostock as bs

FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"


def probe(code: str, start: str, end: str, frequency: str = "5", adjustflag: str = "3") -> None:
    rs = bs.query_history_k_data_plus(
        code,
        FIELDS,
        start_date=start,
        end_date=end,
        frequency=frequency,
        adjustflag=adjustflag,
    )
    rows: list[list[str]] = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    print(
        f"{code} {frequency}m {start}..{end} adjust={adjustflag}: "
        f"rows={len(rows)} error={rs.error_code}"
    )
    if rows:
        print("  first:", rows[0])
        print("  last: ", rows[-1])
        times = sorted({row[1] for row in rows})
        print(f"  distinct times={len(times)} first={times[0]} last={times[-1]}")


def main() -> None:
    login = bs.login()
    print(f"login: error={login.error_code} msg={login.error_msg}")
    try:
        for code in ("sz.300308", "sz.300476"):
            probe(code, "2024-01-02", "2024-01-05")
        probe("sz.300308", "2018-01-02", "2018-01-05")
        probe("sz.300308", "2024-01-02", "2024-01-05", frequency="15")
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
