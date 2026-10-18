"""Parser for USCIS "Form I-130 Approved Family-Based Petitions Awaiting Visa Availability".

One snapshot per file: approved petitions waiting for a visa number, by beneficiary country x preference.
"""
from __future__ import annotations

import re

from ..model import CellRef, FiscalQuarter, Measure, ParsedFile, RawObservation, RowKey, SourceFile, Window
from ._cells import clean_value, norm, read_grid

DATASET = "uscis-i130-awaiting"
TITLE = "Form I-130 Approved Family-Based Petitions Awaiting Visa Availability"
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], 1)}


def matches(url: str) -> bool:
    u = url.lower()
    return "i130_awaiting" in u or "i-130_awaiting" in u


def archived_period(name: str) -> str | None:
    n = name.lower()
    if "awaiting" not in n or not n.endswith((".csv", ".xlsx", ".pdf")):
        return None
    q = _quarter_from_name(name)
    if q:
        return str(q)
    try:
        return str(snapshot_quarter(name.replace("_", " ")))
    except ValueError:
        return None


def snapshot_quarter(text: str) -> FiscalQuarter:
    """'As of June 30, 2026' / 'As of March 2022' -> fiscal quarter that ends that month."""
    m = re.search(r"as of\s+([a-z]+)\.?\s+(?:\d{1,2},\s*)?(\d{4})", text.lower())
    if not m or m[1][:3] not in {k[:3] for k in _MONTHS}:
        raise ValueError(f"no 'As of <month> <year>' in {text!r}")
    month = next(v for k, v in _MONTHS.items() if k.startswith(m[1][:3]))
    year = int(m[2])
    if month not in (3, 6, 9, 12):
        raise ValueError(f"snapshot month {month} is not a quarter end")
    fy, q = (year + 1, 1) if month == 12 else (year, {3: 2, 6: 3, 9: 4}[month])
    return FiscalQuarter(fy, q)


def parse(file: SourceFile, data: bytes) -> ParsedFile:
    grid = read_grid(file, data)
    title = " ".join(c for r in grid.rows[:8] for c in r if c)
    titled = snapshot_quarter(title)
    # The file name wins when it names a quarter: USCIS reused stale titles at least twice (the FY2024Q2 file says
    # "As of December 2023", the FY2026Q2 file "As of March 31, 2025") while the numbers are new.
    named = _quarter_from_name(file.original_name)
    period = named or titled
    printed = re.search(r"as of [a-z]+\.?\s+(?:\d{1,2},\s*)?\d{4}", title, re.I)
    warnings = () if titled == period else (("source-title-mismatch",
        f"title reads \"{printed[0].strip() if printed else title}\" ({titled}) but the file name says {period}; "
        f"we use the file name"),)

    header = next(r for r in range(min(15, len(grid.rows)))
                  if any(c.strip().upper().startswith("F2A") for c in grid.rows[r]))
    cols: dict[int, str] = {}
    for c, text in enumerate(grid.rows[header]):
        code = re.split(r"[\s\n]", text.strip(), maxsplit=1)[0].upper() if text.strip() else ""
        band = norm(grid.cell(header - 1, c)) if header else ""
        if code in ("F1", "F2A", "F2B", "F3", "F4", "TOTAL"):
            cols[c] = code
        elif "total" in norm(text) or (not text.strip() and "total" in band):
            cols[c] = "TOTAL"  # older files print "Grand Total" on the band row above
    if set(cols.values()) != {"F1", "F2A", "F2B", "F3", "F4", "TOTAL"}:
        raise ValueError(f"{file.original_name}: unexpected preference columns {sorted(cols.values())}")

    obs = []
    for r in range(header + 1, len(grid.rows)):
        country = grid.cell(r, 0).strip()
        if norm(country).startswith(("table key", "notes", "source", "-")):
            break
        if not country or not any(grid.cell(r, c).strip() for c in cols):
            continue
        for c, pref in cols.items():
            raw = grid.cell(r, c)
            obs.append(RawObservation(RowKey(DATASET, (country, pref)), period, Measure.AWAITING_VISA,
                                      Window.SNAPSHOT, clean_value(raw),
                                      CellRef(file.source_id, grid.sheet, r + 1, c + 1, raw)))
    return ParsedFile(period, tuple(obs), frozenset(), warnings)


def _quarter_from_name(name: str) -> FiscalQuarter | None:
    m = re.search(r"fy(\d{4})_q([1-4])", name.lower())
    return FiscalQuarter(int(m[1]), int(m[2])) if m else None
