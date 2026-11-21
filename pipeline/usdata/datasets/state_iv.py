"""Parser for U.S. Department of State "Immigrant Visa Issuances by Foreign State of Chargeability or
Place of Birth and Visa Class" (monthly). These are consular visas issued abroad, not USCIS decisions.

Months up to September 2025 entered by hand (`usdata add`) or from the Internet Archive. Newer months enter through
`usdata fetch-state`, which asks State's file store for the file name State has used since FY2025.
The month is read from the title cell and cross-checked against the file name.
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote

from ..model import CellRef, Measure, Month, ParsedFile, RawObservation, RowKey, SourceFile, Window
from ._cells import clean_value, norm, read_grids

DATASET = "state-iv"
TITLE = "Monthly Immigrant Visa Issuances by Foreign State of Chargeability or Place of Birth and Visa Class"
LISTING = ("https://travel.state.gov/content/travel/en/legal/visa-law0/visa-statistics/"
           "immigrant-visa-statistics/monthly-immigrant-visa-issuances.html")
_MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER",
           "NOVEMBER", "DECEMBER"]
_TITLE_MONTH = re.compile(r"(" + "|".join(_MONTHS) + r")\s+(\d{4})\s*\(FY", re.I)
FILE_STORE = "https://travel.state.gov/content/dam/visas/Statistics/Immigrant-Statistics/MonthlyIVIssuances/Excel/"


def file_url(month: Month) -> str:
    fiscal_year = month.year + (month.month >= 10)
    name = f"{_MONTHS[month.month - 1]} {month.year} - IV Issuances by FSC or Place of Birth and Visa Class.xlsx"
    return f"{FILE_STORE}FY{fiscal_year}/{quote(name)}"


def matches(url: str) -> bool:
    return False  # never crawled


def archived_period(name: str) -> str | None:
    name = unquote(name)
    if "issuances by fsc" not in name.lower() or not name.lower().endswith((".xlsx", ".pdf")):
        return None
    m = month_from_name(name.replace("SEPT ", "SEPTEMBER "))
    return str(m) if m else None


def month_from_name(name: str) -> Month | None:
    m = re.search(r"(" + "|".join(_MONTHS) + r")(?:%20|\s|_|-)*(\d{4})", name.upper())
    return Month(int(m[2]), _MONTHS.index(m[1]) + 1) if m else None


def parse(file: SourceFile, data: bytes) -> ParsedFile:
    # A long month can spill onto a second sheet ("Table 2") that repeats the title and header rows.
    grids = read_grids(file, data)
    title = re.sub(r"\s+", " ", grids[0].cell(0, 0))
    m = _TITLE_MONTH.search(title)
    if not m:
        raise ValueError(f"{file.original_name}: no '<Month> <year> (FY' in title {title!r}")
    month = Month(int(m[2]), _MONTHS.index(m[1].upper()) + 1)
    named = month_from_name(file.original_name)
    if named is not None and named != month:
        raise ValueError(f"{file.original_name}: file name says {named}, title says {month}")

    # Some months print the same (country, visa class) on two consecutive rows, e.g. Egypt IR5 62 then 1
    # (Nov 2023). Both rows count toward GRAND TOTAL, so neither is a typo we may drop or merge.
    # The second keeps its own label "IR5 (row 2)" and the duplicate-rows check publishes every case.
    seen: dict[tuple[str, str], int] = {}
    obs = []
    for grid in grids:
        header = next((r for r in range(5) if norm(grid.cell(r, 1)) == "visa class"), -1)
        if header == -1 and grid is not grids[0]:
            header = 0  # spreadsheet continuation sheets may omit the header
        for r in range(header + 1, len(grid.rows)):
            country, vclass, raw = (grid.cell(r, c).strip() for c in (0, 1, 2))
            if not country and not vclass:
                continue
            if norm(country) == "grand total":
                vclass = "ALL"
            n = seen[(country, vclass)] = seen.get((country, vclass), 0) + 1
            if n > 1:
                vclass = f"{vclass} (row {n})"
            obs.append(RawObservation(RowKey(DATASET, (country, vclass)), month, Measure.ISSUANCES, Window.MONTH,
                                      clean_value(raw), CellRef(file.source_id, grid.sheet, r + 1, 3, raw)))
    return ParsedFile(month, tuple(obs), frozenset())
