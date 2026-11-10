"""Probe one calendar year of BaoStock 5-minute data.

Usage: uv run python scripts/spike_baostock_year.py 2023
"""

from __future__ import annotations

import socket
import sys

import baostock as bs

socket.setdefaulttimeout(20)
FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"


def main() -> None:
    year = sys.argv[1]
    code = sys.argv[2] if len(sys.argv) > 2 else "sz.300308"
    bs.login()
    try:
        rs = bs.query_history_k_data_plus(
            code,
            FIELDS,
            start_date=f"{year}-01-01",
            end_date=f"{year}-12-31",
            frequency="5",
            adjustflag="3",
        )
        count = 0
        first = None
        last = None
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if first is None:
                first = row
            last = row
            count += 1
        print(f"{code} {year}: rows={count} error={rs.error_code} msg={rs.error_msg}")
        if first:
            print(f"  first: {first[0]} {first[1]}")
        if last:
            print(f"  last:  {last[0]} {last[1]}")
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
