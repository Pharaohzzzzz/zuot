"""Probe the full 2018-2023 BaoStock 5-minute range in one request."""

from __future__ import annotations

import socket

import baostock as bs

socket.setdefaulttimeout(60)
FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"


def main() -> None:
    bs.login()
    try:
        for code in ("sz.300308", "sz.300476"):
            rs = bs.query_history_k_data_plus(
                code,
                FIELDS,
                start_date="2018-01-01",
                end_date="2023-12-31",
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
            print(f"{code}: rows={count} error={rs.error_code} msg={rs.error_msg}")
            if first:
                print(f"  first: {first}")
            if last:
                print(f"  last:  {last}")
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
