"""Month arithmetic shared by the books, the subledger, and the generator."""

from __future__ import annotations

import calendar
from dataclasses import dataclass


@dataclass(frozen=True)
class Month:
    period: str

    @property
    def year(self) -> int:
        return int(self.period[:4])

    @property
    def number(self) -> int:
        return int(self.period[5:7])

    @property
    def start(self) -> str:
        return f"{self.period}-01"

    @property
    def end(self) -> str:
        return f"{self.period}-{calendar.monthrange(self.year, self.number)[1]:02d}"

    @property
    def label(self) -> str:
        return f"{calendar.month_name[self.number]} {self.year}"

    @property
    def received_on(self) -> str:
        """Drops arrive on the 2nd of the following month."""
        year, month = divmod(self.year * 12 + self.number, 12)
        return f"{year:04d}-{month + 1:02d}-02"


def month_of(period: str) -> Month:
    year_text, _, month_text = period.partition("-")
    if len(year_text) != 4 or len(month_text) != 2 or not 1 <= int(month_text) <= 12:
        raise ValueError(f"Period must be YYYY-MM, got {period!r}")
    return Month(period)


def months_between(first_day: str, later_month: Month) -> int:
    year, month = int(first_day[:4]), int(first_day[5:7])
    return (later_month.year * 12 + later_month.number) - (year * 12 + month)


def add_months(iso_date: str, months: int) -> str:
    year, month, day = (int(part) for part in iso_date.split("-"))
    month_index = year * 12 + (month - 1) + months
    new_year, new_month = divmod(month_index, 12)
    return f"{new_year:04d}-{new_month + 1:02d}-{day:02d}"


def previous_period(period: str) -> str:
    return add_months(f"{period}-01", -1)[:7]


def iter_periods(start: str, end: str) -> list[str]:
    periods = []
    current = start
    while current <= end:
        periods.append(current)
        current = add_months(f"{current}-01", 1)[:7]
    return periods
