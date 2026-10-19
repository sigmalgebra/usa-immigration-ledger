"""Parser for the U.S. Department of State monthly Visa Bulletin (HTML pages).

travel.state.gov refuses automated clients, so bulletins enter only as Internet Archive captures (`usdata wayback`).
The bulletin month comes from the URL slug `visa-bulletin-for-<month>-<year>.html` and is cross-checked against
the page heading.

A cutoff table is any table whose header row names "All Chargeability Areas". Each one is classified by section
(family or employment, from its own label column) and by chart. Before October 2015 a bulletin prints one chart
per section, which was the final action chart. From October 2015 it prints "FINAL ACTION DATES" and "DATES FOR
FILING" charts, and the nearest preceding heading decides. A table we can't classify fails the parse.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser

from ..model import (Absent, CellRef, Current, Cutoff, Measure, Month, ParsedFile, RawObservation, RowKey,
                     SourceFile, Value, Window)
from ._cells import UnparseableCell, norm

DATASET = "state-vb"
TITLE = "Visa Bulletin: final action dates and dates for filing, by preference category and chargeability area"
LISTING = "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_SLUG = re.compile(r"^visa-bulletin-for-(" + "|".join(_MONTHS) + r")-(\d{4})\.html$")
TWO_CHARTS_FROM = Month(2015, 10)
MEASURE = {"final": Measure.FINAL_ACTION_DATE, "filing": Measure.FILING_DATE}


def matches(url: str) -> bool:
    return False  # never crawled


def bulletin_month(name: str) -> Month | None:
    m = _SLUG.match(name.lower())
    return Month(int(m[2]), _MONTHS.index(m[1]) + 1) if m else None


def archived_period(name: str) -> str | None:
    m = bulletin_month(name)
    return str(m) if m else None


@dataclass
class _Table:
    index: int  # 1-based, in document order
    before: str  # page text between the previous table and this one
    rows: list[list[str]] = field(default_factory=list)


class _Tables(HTMLParser):
    """Every <table> as rows of cell text, plus the text that precedes it. Nested tables are their own tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_Table] = []
        self.headings: list[str] = []
        self._open: list[_Table] = []
        self._cell: list[str] | None = None
        self._text: list[str] = []
        self._in_h1 = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            t = _Table(len(self.tables) + 1, " ".join(self._text))
            self._text = []
            self.tables.append(t)
            self._open.append(t)
        elif tag == "tr" and self._open:
            self._open[-1].rows.append([])
        elif tag in ("td", "th") and self._open:
            self._cell = []
        elif tag == "h1":
            self._in_h1 = True
            self.headings.append("")
        elif tag in ("br", "p", "div") and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._open:
            if not self._open[-1].rows:
                self._open[-1].rows.append([])
            self._open[-1].rows[-1].append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "table" and self._open:
            self._open.pop()
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif not self._open:
            self._text.append(data)
        if self._in_h1:
            self.headings[-1] += data


_DATE = re.compile(r"^(\d{1,2})([A-Z]{3})(\d{2})$")
_MON3 = [m[:3].upper() for m in _MONTHS]


def cell_value(raw: str, bulletin: Month) -> Value:
    """'C' -> Current, 'U' -> unavailable, '01MAR99' -> Cutoff(1999-03-01), '' -> blank. Anything else raises."""
    s = raw.strip().upper()
    if s == "":
        return Absent("blank")
    if s == "C":
        return Current()
    if s == "U":
        return Absent("unavailable")
    m = _DATE.match(s)
    if not m or m[2] not in _MON3:
        raise UnparseableCell(raw)
    # A cutoff is never after its bulletin month, which settles the century of a two-digit year.
    yy, mon, day = int(m[3]), _MON3.index(m[2]) + 1, int(m[1])
    for century in (2000, 1900):
        d = date(century + yy, mon, day)
        if d <= date(bulletin.year, bulletin.month, 28):
            return Cutoff(d)
    raise UnparseableCell(raw)


def _section(t: _Table) -> str | None:
    labels = norm(" ".join(r[0] for r in t.rows[:3] if r))
    if "family" in labels:
        return "family"
    if "employment" in labels:
        return "employment"
    return None


def _chart(t: _Table, month: Month) -> str | None:
    if month < TWO_CHARTS_FROM:
        return "final"
    final, filing = t.before.rfind("FINAL ACTION DATES"), t.before.rfind("DATES FOR FILING")
    if final == filing == -1:
        return None
    return "final" if final > filing else "filing"


class UnclassifiedTable(ValueError):
    pass


def parse(file: SourceFile, data: bytes) -> ParsedFile:
    month = bulletin_month(file.original_name)
    if month is None:
        raise ValueError(f"{file.original_name}: not a visa-bulletin-for-<month>-<year>.html page")
    doc = _Tables()
    doc.feed(data.decode("utf-8", errors="strict"))
    doc.close()
    if f"visa bulletin for {_MONTHS[month.month - 1]} {month.year}" not in map(norm, doc.headings):
        raise ValueError(f"{file.original_name}: no matching page heading in {doc.headings}")

    obs: list[RawObservation] = []
    warnings: list[tuple[str, str]] = []
    for t in doc.tables:
        header = next((i for i, row in enumerate(t.rows) if any("all chargeability" in norm(c) for c in row)), None)
        if header is None:
            continue
        section, chart = _section(t), _chart(t, month)
        if section is None or chart is None:
            raise UnclassifiedTable(f"{file.original_name} table {t.index}: section={section} chart={chart}; "
                                    f"header {t.rows[header]}, preceding text {t.before[-200:]!r}")
        areas = t.rows[header]
        for r, row in enumerate(t.rows[header + 1:], start=header + 2):
            if not any(c.strip() for c in row[1:]):
                continue  # a section label row ("Family", "Employment-Based")
            if len(row) != len(areas):
                raise ValueError(f"{file.original_name} table {t.index} row {r}: {len(row)} cells under "
                                 f"{len(areas)} headers")
            for c in range(1, len(row)):
                cell = CellRef(file.source_id, f"table {t.index}", r, c + 1, row[c])
                try:
                    value = cell_value(row[c], month)
                except UnparseableCell as e:
                    raise UnparseableCell(f"{row[c]} ({file.original_name} {cell.address})") from e
                if value == Absent("blank"):
                    warnings.append(("bulletin-blank-cell", f"{chart} chart, {section} {row[0]!r}, {areas[c]!r} "
                                                            f"is printed empty ({cell.address})"))
                obs.append(RawObservation(RowKey(DATASET, (chart, section, row[0], areas[c])), month,
                                          MEASURE[chart], Window.MONTH, value, cell))
    if not obs:
        raise ValueError(f"{file.original_name}: no cutoff tables found")
    return ParsedFile(month, tuple(obs), frozenset(), tuple(warnings))
