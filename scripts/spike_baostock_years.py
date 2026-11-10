"""Find the earliest year with BaoStock 5-minute data for the two symbols."""

from __future__ import annotations

import baostock as bs

FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"


def count(code: str, start: str, end: str) -> int:
    rs = bs.query_history_k_data_plus(
        code, FIELDS, start_date=start, end_date=end, frequency="5", adjustflag="3"
    )
    rows = 0
    while rs.error_code == "0" and rs.next():
        rows += 1
    return rows


def main() -> None:
    bs.login()
    try:
        for code in ("sz.300308", "sz.300476"):
            print(code)
            for year in range(2016, 2025):
                start = f"{year}-01-04"
                end = f"{year}-01-08"
                print(f"  {year}: {count(code, start, end)} rows")
    finally:
        bs.logout()


if __name__ == "__main__":
    main()
