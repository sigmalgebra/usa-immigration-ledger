"""Parser for USCIS "Number of Service-wide Forms by Quarter, Form Status, and Processing Time".

Owns every layout quirk of that report family (FY2019 through today):
- CSV (UTF-8 BOM or cp1252) and XLSX; header block anywhere in the first ~10 rows.
- FY2019 files: four quarter column groups side by side plus fiscal-year-to-date, form label in column B.
- Later files: one quarter group plus FYTD, form label in column A. Band labels above the
  measure row are unreliable (FY2023Q3 prints "3rd Quarter, 4th Quarter, 1st Quarter..."),
  so column groups are found from the measure row itself: each "Received" starts a group.
Labels are emitted verbatim ("I-6007"); splitting footnote digits is the catalog's job.
"""
from __future__ import annotations

import re

from ..model import (Absent, CellRef, FiscalQuarter, Measure, ParsedFile, RawObservation, RowKey, SourceFile, Value,
                     Window)
from ._cells import Grid, clean_value, norm, read_grid, read_grids

DATASET = "uscis-forms"
TITLE = "Number of Service-wide Forms by Quarter, Form Status, and Processing Time"

# Every naming scheme USCIS has used for this report since 2012:
#   all-form-types-performance-data_fy2012_qtr3 · allformtypes_performancedata_fy2013-qtr1 · AllForm-2013Q4 ·
#   AllForms-Q42013 · all_forms_performancedata_fy2014_qtr3 · ECN_1893_-_Quarterly_-_All_Forms_FY17Q1_Final ·
#   Quarterly_All_Forms_FY19Q1 · Quarterly_All_Forms_FY2022_Q2 · quarterly_all_forms_fy2026_q3_v1
_FILENAME = re.compile(r"FY[_-]?(?:20)?(\d{2})[_-]?Q(?:TR)?[_-]?([1-4])", re.I)
_YEAR_Q = re.compile(r"(?:^|[^0-9])(20\d{2})Q([1-4])|Q([1-4])(20\d{2})", re.I)
_NAME = re.compile(r"all.?form", re.I)

# Closed vocabulary for measure headers; trailing footnote digits are stripped from HEADERS only.
_MEASURES = [
    ("received", Measure.RECEIVED),
    ("approved", Measure.APPROVED),
    ("denied", Measure.DENIED),
    ("total", Measure.COMPLETED),
    ("pending", Measure.PENDING),
    ("time", Measure.MEDIAN_MONTHS),
]
_DIGIT = re.compile(r"\d")
_END_MARKERS = ("table key", "references", "notes", "source", "d  data withheld", "d data withheld")


def matches(url: str) -> bool:
    return "quarterly_all_forms" in url.lower()


def archived_period(name: str) -> str | None:
    """Period key for a historical file name, or None if it isn't this report."""
    if not _NAME.search(name) or not name.lower().endswith((".csv", ".xlsx", ".pdf")):
        return None
    try:
        return str(report_period_from_name(name))
    except ValueError:
        return None


def report_period_from_name(name: str) -> FiscalQuarter:
    if m := _FILENAME.search(name):
        return FiscalQuarter(2000 + int(m[1]), int(m[2]))
    if m := _YEAR_Q.search(name):  # "AllForm-2013Q4", "AllForms-Q42013": fiscal year and quarter, per the file's title
        return FiscalQuarter(int(m[1] or m[4]), int(m[2] or m[3]))
    raise ValueError(f"no fiscal quarter in file name {name!r}")


def parse(file: SourceFile, data: bytes) -> ParsedFile:
    period = report_period_from_name(file.original_name)
    # PDF-only quarters (FY2019Q3-FY2021Q3) come back as one grid per page; spreadsheets must have exactly one sheet.
    grids = read_grids(file, data) if file.original_name.lower().endswith(".pdf") else [read_grid(file, data)]
    if _is_receipts_only_report(grids[0]):
        # FY2012Q3-FY2013Q3: an older, different report (monthly receipts and approvals, no denials or pending).
        # FY2013's quarters are covered in full by the FY2013Q4 file, so these are archived but not parsed.
        return ParsedFile(period, (), frozenset(), (("older-report-not-used",
            "older 'Receipts and Approvals' report with a different design (monthly, no denials or pending); "
            "archived but not used"),))

    obs: list[RawObservation] = []
    warnings: list[tuple[str, str]] = []
    footnotes: set[str] = set()
    category = ""
    for grid in grids:
        category = _parse_grid(file, grid, period, category, obs, warnings)
        footnotes |= {m[1] for row in grid.rows if row and not any(_DIGIT.search(c) for c in row[2:])
                      if (m := re.match(r"^\s*(\d{1,2})(?:\s|\)|$|(?=[A-Z]))", row[0]))}
    return ParsedFile(period, tuple(obs), frozenset(footnotes), tuple(warnings))


def _is_receipts_only_report(grid: Grid) -> bool:
    head = " ".join(norm(c) for r in grid.rows[:12] for c in r)
    return "receipts" in head and "pending" not in head


def _parse_grid(file: SourceFile, grid: Grid, period: FiscalQuarter, category: str,
                obs: list[RawObservation], warnings: list[tuple[str, str]]) -> str:
    """Append this grid's observations to `obs`; returns the category in effect at the end (pages continue it)."""
    measure_row = _find_measure_row(grid)
    groups = _column_groups(grid, measure_row)
    label_col = _label_column(grid, measure_row)

    # FY2013-FY2020 files print quarter blocks Q1, Q2, ... then fiscal-year-to-date (FY2014Q2 prints only Q1, Q2
    # and FYTD); since FY2021 a file prints its own quarter then FYTD; FY2021Q1 prints a single block.
    if len(groups) == 1:
        windows, keep = [(period, Window.QUARTER)], [0]
    elif len(groups) == 2:
        windows, keep = [(period, Window.QUARTER), (period, Window.FYTD)], [0, 1]
    elif 3 <= len(groups) <= 5:
        n = len(groups) - 1
        windows = [(FiscalQuarter(period.fy, q), Window.QUARTER) for q in range(1, n + 1)] + [(period, Window.FYTD)]
        keep = [i for i in range(n) if i + 1 <= period.q] + [n]
    else:
        raise ValueError(f"{file.original_name} {grid.sheet or ''}: expected 1-5 column groups, found {len(groups)}")

    orphan: tuple[int, str, str] | None = None  # (row, label, category before it) of a label printed without numbers
    for r in range(measure_row + 1, len(grid.rows)):
        row = grid.rows[r]
        first = norm(row[0]) if row else ""
        if any(first.startswith(m) for m in _END_MARKERS):
            break
        label = grid.cell(r, label_col).strip()
        desc = grid.cell(r, label_col + 1).strip()
        has_number = any(_DIGIT.search(grid.cell(r, c)) for g in groups for c in g.values())
        if label_col == 1 and not label and row and row[0].strip():
            if not has_number:
                category = row[0].strip()  # four-quarter CSVs: categories in column A, form numbers in column B
                orphan = (r, category, category)
                continue
            label = row[0].strip()  # "Total - All Forms" also sits in column A
        if not has_number:
            # Category header. FY2023Q1's "Other" header carries stray dashes, so "no digits" is the test.
            if label and not desc:
                orphan = (r, label, category)
                category = label
            continue
        if not label and not desc:
            if orphan and r - orphan[0] <= 2 and orphan[1].lower().startswith("total"):
                # FY2015Q1 prints "Total - All Forms" on one row and its numbers on the next.
                _, label, category = orphan
            else:
                where = f"{grid.sheet}, row {r + 1}" if grid.sheet else f"line {r + 1}"
                printed = ", ".join(c.strip() for c in row[2:] if c.strip())
                warnings.append(("unlabeled-numbers", f"numbers with no form label ({printed}) at {where} were not used: they can't be "
                                f"identified"))
                continue
        orphan = None
        key = RowKey(DATASET, (category, label, desc))
        for gi in keep:
            p, w = windows[gi]
            for measure, c in groups[gi].items():
                raw = grid.cell(r, c)
                # Pending is a stock. The FYTD group reprints it; that copy keeps window=fytd so checks can compare.
                win = Window.SNAPSHOT if measure is Measure.PENDING and w is Window.QUARTER else w
                obs.append(RawObservation(key, p, measure, win, _value(raw),
                                          CellRef(file.source_id, grid.sheet, r + 1, c + 1, raw)))
    return category


def _value(raw: str) -> Value:
    # Table key: "D Disclosure standards not met", "H Replaces the value from which one could deduce the value of D".
    if raw.strip() in ("D", "H"):
        return Absent("withheld")
    if raw.strip() == "N":  # older table key: "N Not captured" (e.g. credible fear screenings)
        return Absent("not_captured")
    return clean_value(raw)


def _find_measure_row(grid: Grid) -> int:
    # The row naming "Approved" is the measure row; "Pending" may sit on it or on a band row up to 2 above (FY2021Q4).
    for r in range(min(12, len(grid.rows))):
        if not any("approved" in norm(c) for c in grid.rows[r]):
            continue
        if any("pending" in norm(c) for rr in range(max(0, r - 2), r + 1) for c in grid.rows[rr]):
            return r
    raise ValueError("no header row with Approved and Pending in the first 12 rows")


def _column_groups(grid: Grid, measure_row: int) -> list[dict[Measure, int]]:
    """Read each column's header text top-down (band rows + measure row) and split into groups."""
    top = max(0, measure_row - 3)
    groups: list[dict[Measure, int]] = []
    width = max(len(r) for r in grid.rows[top:measure_row + 1])
    for c in range(2, width):
        own = norm(grid.cell(measure_row, c))
        stacked = norm(" ".join(grid.cell(r, c) for r in range(top, measure_row + 1)))
        text = own or stacked
        text = re.sub(r"\s*\d+$", "", text)  # "pending5", "total 15"
        measure = next((m for k, m in _MEASURES if k in text), None)
        if measure is None and "received" in stacked:
            measure = Measure.RECEIVED  # FY2022Q2: "Forms Received" sits on the band row
        if measure is None:
            continue
        if measure is Measure.RECEIVED or not groups or measure in groups[-1]:
            groups.append({})
        groups[-1][measure] = c
    return [g for g in groups if Measure.APPROVED in g]


def _label_column(grid: Grid, measure_row: int) -> int:
    """FY2019 files put the form number in column B (column A holds categories)."""
    for r in range(measure_row + 1, min(measure_row + 15, len(grid.rows))):
        if re.match(r"^[IN]-\d", grid.cell(r, 1).strip()):
            return 1
        if re.match(r"^[IN]-\d", grid.cell(r, 0).strip()):
            return 0
    return 0
