"""Parser for the U.S. Department of State monthly Visa Bulletin, as an HTML page or as the PDF edition.

Older bulletins enter as Internet Archive captures of the HTML page (`usdata wayback`). New ones enter as the PDF
from State's file store (`usdata fetch-state`), because the HTML pages refuse automated clients. The bulletin month
comes from the file name (`visa-bulletin-for-<month>-<year>.html` or `visabulletin_<Month><year>.pdf`) and is
cross-checked against the page heading or the PDF's first page.

A cutoff table is any table whose header row names "All Chargeability Areas". Each one is classified by section
(family or employment, from its own label column) and by chart. Before October 2015 a bulletin prints one chart
per section, which was the final action chart. From October 2015 it prints "FINAL ACTION DATES" and "DATES FOR
FILING" charts, and the nearest preceding heading decides. A table we can't classify fails the parse.
"""
from __future__ import annotations

import io
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
_PDF_NAME = re.compile(r"^visabulletin_(" + "|".join(_MONTHS) + r")(\d{4})\.pdf$")
TWO_CHARTS_FROM = Month(2015, 10)
MEASURE = {"final": Measure.FINAL_ACTION_DATE, "filing": Measure.FILING_DATE}
PDF_STORE = "https://travel.state.gov/content/dam/visas/Bulletins/"


def matches(url: str) -> bool:
    return False  # never crawled


def bulletin_month(name: str) -> Month | None:
    m = _SLUG.match(name.lower()) or _PDF_NAME.match(name.lower())
    return Month(int(m[2]), _MONTHS.index(m[1]) + 1) if m else None


def pdf_name(month: Month) -> str:
    return f"visabulletin_{_MONTHS[month.month - 1].capitalize()}{month.year}.pdf"


def archived_period(name: str) -> str | None:
    m = bulletin_month(name)
    return str(m) if m else None


@dataclass
class _Table:
    index: int  # 1-based, in document order
    before: str  # document text between the previous table and this one
    rows: list[list[str]] = field(default_factory=list)
    origins: list[tuple[str, int]] = field(default_factory=list)  # (sheet, 1-based row) each row is cited by


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
            t = self._open[-1]
            t.rows.append([])
            t.origins.append((f"table {t.index}", len(t.rows)))
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
                self._open[-1].origins.append((f"table {self._open[-1].index}", 1))
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


def _html_tables(file: SourceFile, data: bytes, month: Month) -> list[_Table]:
    doc = _Tables()
    doc.feed(data.decode("utf-8", errors="strict"))
    doc.close()
    if f"visa bulletin for {_MONTHS[month.month - 1]} {month.year}" not in map(norm, doc.headings):
        raise ValueError(f"{file.original_name}: no matching page heading in {doc.headings}")
    return doc.tables


def _pdf_cell(text: str | None) -> str:
    # PDF cells wrap mid-label ("CHINA-\nmainland\nborn"); the HTML edition prints "CHINA-mainland born".
    return re.sub(r"\s+", " ", re.sub(r"-\n", "-", text or "")).strip()


def _is_header(row: list[str]) -> bool:
    return any("all chargeability" in norm(c).replace("charge-ability", "chargeability") for c in row)


def _pdf_tables(file: SourceFile, data: bytes, month: Month) -> list[_Table]:
    import pdfplumber

    tables: list[_Table] = []
    text: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        first = norm(pdf.pages[0].extract_text() or "")
        if f"{_MONTHS[month.month - 1]} {month.year}" not in first or "visa bulletin" not in first:
            raise ValueError(f"{file.original_name}: first page does not read '<Month> <year> Visa Bulletin'")
        for p, page in enumerate(pdf.pages, 1):
            top = 0.0
            for k, found in enumerate(sorted(page.find_tables(), key=lambda f: f.bbox[1]), 1):
                if found.bbox[1] > top:
                    text.append(page.crop((0, top, page.width, found.bbox[1])).extract_text() or "")
                top = max(top, found.bbox[3])
                rows = [[_pdf_cell(c) for c in r] for r in found.extract()]
                if len(rows[0]) < 3:
                    continue  # the running page title is laid out as a one-column table
                origins = [(f"page {p} table {k}", i) for i in range(1, len(rows) + 1)]
                prev = tables[-1] if tables else None
                between = " ".join(text).upper()  # the PDF titles its charts "A. Final Action Dates for ..."
                # A chart that runs past a page break continues on the next page without its header row.
                if (prev and not _is_header(rows[0]) and any(_is_header(r) for r in prev.rows)
                        and len(rows[0]) == len(prev.rows[-1])
                        and "FINAL ACTION DATES" not in between and "DATES FOR FILING" not in between):
                    prev.rows.extend(rows)
                    prev.origins.extend(origins)
                else:
                    tables.append(_Table(len(tables) + 1, between, rows, origins))
                text = []
            if top < page.height:
                text.append(page.crop((0, top, page.width, page.height)).extract_text() or "")
    return tables


def parse(file: SourceFile, data: bytes) -> ParsedFile:
    month = bulletin_month(file.original_name)
    if month is None:
        raise ValueError(f"{file.original_name}: not a visa-bulletin-for-<month>-<year>.html page "
                         f"or visabulletin_<Month><year>.pdf")
    read = _pdf_tables if file.original_name.lower().endswith(".pdf") else _html_tables
    obs: list[RawObservation] = []
    warnings: list[tuple[str, str]] = []
    for t in read(file, data, month):
        header = next((i for i, row in enumerate(t.rows) if _is_header(row)), None)
        if header is None:
            continue
        section, chart = _section(t), _chart(t, month)
        if section is None or chart is None:
            raise UnclassifiedTable(f"{file.original_name} table {t.index}: section={section} chart={chart}; "
                                    f"header {t.rows[header]}, preceding text {t.before[-200:]!r}")
        areas = t.rows[header]
        for row, (sheet, r) in zip(t.rows[header + 1:], t.origins[header + 1:]):
            if not any(c.strip() for c in row[1:]):
                continue  # a section label row ("Family", "Employment-Based")
            if len(row) != len(areas):
                raise ValueError(f"{file.original_name} {sheet} row {r}: {len(row)} cells under "
                                 f"{len(areas)} headers")
            for c in range(1, len(row)):
                cell = CellRef(file.source_id, sheet, r, c + 1, row[c])
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
    charts = {(o.row.labels[0], o.row.labels[1]) for o in obs}
    expected = {(c, s) for c in MEASURE for s in ("family", "employment")}
    if month >= TWO_CHARTS_FROM and charts != expected:
        raise ValueError(f"{file.original_name}: found charts {sorted(charts)}, expected {sorted(expected)}")
    return ParsedFile(month, tuple(obs), frozenset(), tuple(warnings))
