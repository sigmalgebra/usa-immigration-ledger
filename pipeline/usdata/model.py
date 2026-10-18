"""Domain types for the fact ledger. No I/O lives here.

Every published number is a `Fact`: a value keyed by (series, period, measure, window)
that carries a `CellRef` back to the exact cell of an archived source file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Mapping, NewType, Sequence

DatasetId = Literal["uscis-forms", "uscis-i130-awaiting", "state-iv"]
DATASET_IDS: tuple[DatasetId, ...] = ("uscis-forms", "uscis-i130-awaiting", "state-iv")


@dataclass(frozen=True, order=True)
class FiscalQuarter:
    """U.S. federal fiscal quarter. FY2026 runs Oct 1 2025 - Sep 30 2026; Q1 = Oct-Dec."""

    fy: int
    q: int

    def __post_init__(self) -> None:
        if self.q not in (1, 2, 3, 4) or not 2000 <= self.fy <= 2100:
            raise ValueError(f"bad fiscal quarter {self.fy}Q{self.q}")

    def __str__(self) -> str:
        return f"FY{self.fy}Q{self.q}"

    @property
    def end_label(self) -> str:
        """Calendar date the quarter ends, e.g. FY2026Q3 -> 'June 30, 2026'."""
        year = self.fy - 1 if self.q == 1 else self.fy
        return {1: f"December 31, {year}", 2: f"March 31, {year}", 3: f"June 30, {year}", 4: f"September 30, {year}"}[self.q]


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"bad month {self.year}-{self.month}")

    def __str__(self) -> str:
        return f"{self.year}-{self.month:02d}"


Period = FiscalQuarter | Month

_FQ = re.compile(r"^FY(\d{4})Q([1-4])$")
_MO = re.compile(r"^(\d{4})-(\d{2})$")


def parse_period(s: str) -> Period:
    if m := _FQ.match(s):
        return FiscalQuarter(int(m[1]), int(m[2]))
    if m := _MO.match(s):
        return Month(int(m[1]), int(m[2]))
    raise ValueError(f"unparseable period {s!r}")


class Measure(StrEnum):
    RECEIVED = "received"
    APPROVED = "approved"
    DENIED = "denied"
    COMPLETED = "completed"
    PENDING = "pending"
    MEDIAN_MONTHS = "median_processing_months"
    AWAITING_VISA = "awaiting_visa"
    ISSUANCES = "issuances"


class Window(StrEnum):
    """What span a value covers relative to its period. Pending/awaiting are stocks (snapshot)."""

    QUARTER = "quarter"
    FYTD = "fytd"
    MONTH = "month"
    SNAPSHOT = "snapshot"


@dataclass(frozen=True)
class SourceFile:
    """One archived, byte-exact file. Identity is its content hash."""

    source_id: str  # sha256[:12]
    dataset: DatasetId
    url: str
    original_name: str
    sha256: str
    retrieved_at: str  # ISO-8601 UTC
    retrieved_by: Literal["crawler", "human", "wayback"]
    archive_path: str  # posix path relative to repo root
    via: str | None = None  # Internet Archive capture we fetched from, when the publisher no longer serves the file


@dataclass(frozen=True)
class CellRef:
    source_id: str
    sheet: str | None  # None for CSV
    row: int  # 1-based, as a spreadsheet (or a CSV line number) shows it
    col: int  # 1-based
    raw: str  # exact cell text before parsing

    @property
    def address(self) -> str:
        col = _col_letters(self.col)
        return f"{self.sheet}!{col}{self.row}" if self.sheet else f"line {self.row}, column {col}"


def _col_letters(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


@dataclass(frozen=True)
class Reported:
    number: Decimal

    @property
    def is_estimate(self) -> bool:
        """USCIS published a non-integral count (e.g. FY2024Q1 approvals 2,475,577.75): an estimate, not a tally."""
        return self.number != self.number.to_integral_value()


@dataclass(frozen=True)
class Absent:
    """The source printed no number. A dash is "zero or rounds to 0.0" per the USCIS table key, but we keep
    what was printed and let readers decide; "withheld" is USCIS's D/H privacy suppression."""

    reason: Literal["na", "null", "dash", "blank", "withheld", "not_captured"]


Value = Reported | Absent

SeriesId = NewType("SeriesId", str)


@dataclass(frozen=True)
class RowKey:
    """A parser's verbatim view of a row's identity, before the catalog resolves it."""

    dataset: DatasetId
    labels: tuple[str, ...]


@dataclass(frozen=True)
class RawObservation:
    row: RowKey
    period: Period
    measure: Measure
    window: Window
    value: Value
    cell: CellRef


@dataclass(frozen=True)
class ParsedFile:
    report_period: Period
    observations: tuple[RawObservation, ...]
    footnotes: frozenset[str]  # footnote numbers declared in this file, e.g. {"1", ..., "9"}
    warnings: tuple[tuple[str, str], ...] = ()  # (check id, detail): source problems handled by a documented rule


@dataclass(frozen=True)
class Series:
    id: SeriesId
    dataset: DatasetId
    form: str | None  # canonical, footnote-free code: "I-485"
    category: str
    title: str
    dims: Mapping[str, str]
    note: str | None = None


@dataclass(frozen=True, order=True)
class FactKey:
    series: str
    period: Period
    measure: str
    window: str


@dataclass(frozen=True)
class Fact:
    key: FactKey
    value: Value
    cell: CellRef


@dataclass(frozen=True)
class Revision:
    """The same FactKey reported differently by two source files. Feeds /corrections."""

    key: FactKey
    superseded: Fact
    current: Fact


@dataclass(frozen=True)
class Ledger:
    facts: Mapping[FactKey, Fact]
    revisions: tuple[Revision, ...]
    series: Mapping[SeriesId, Series]
    sources: Mapping[str, SourceFile]

    def get(self, key: FactKey) -> Fact:
        return self.facts[key]

    def series_facts(self, s: SeriesId) -> Sequence[Fact]:
        return sorted((f for f in self.facts.values() if f.key.series == s), key=lambda f: (f.key.measure, f.key.window, f.key.period))


def now_utc() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
