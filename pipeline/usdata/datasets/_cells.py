"""Shared cell plumbing for parsers: bytes -> Grid, raw cell text -> Value."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..model import Absent, Reported, SourceFile, Value

CACHE = Path(__file__).resolve().parents[3] / ".cache" / "grids"
# Cached grids are keyed by the file's hash AND the readers' source, so changing a reader invalidates the cache.
_READERS = hashlib.sha256(b"".join((Path(__file__).parent / n).read_bytes() for n in ("_cells.py", "_pdf.py")))
READER_VERSION = _READERS.hexdigest()[:10]


@dataclass(frozen=True)
class Grid:
    """A rectangular sheet of verbatim cell text. rows[r][c] is 0-based; CellRef adds 1."""

    sheet: str | None
    rows: tuple[tuple[str, ...], ...]

    def cell(self, r: int, c: int) -> str:
        row = self.rows[r]
        return row[c] if c < len(row) else ""


def read_grid(file: SourceFile, data: bytes) -> Grid:
    """The file's only non-empty sheet. More than one is an error: data would be silently skipped."""
    grids = read_grids(file, data)
    if len(grids) != 1:
        raise ValueError(f"{file.original_name}: expected exactly one non-empty sheet, found {[g.sheet for g in grids]}")
    return grids[0]


def read_grids(file: SourceFile, data: bytes) -> list[Grid]:
    """CSV: utf-8-sig, falling back to cp1252. XLSX: every non-empty sheet, in workbook order.

    openpyxl is slow on these workbooks (several seconds each), so grids are cached by content hash.
    """
    cached = CACHE / f"{file.sha256}-{READER_VERSION}.json"
    if cached.exists():
        return [Grid(g["sheet"], tuple(tuple(r) for r in g["rows"])) for g in json.loads(cached.read_text(encoding="utf-8"))]

    name = file.original_name.lower()
    if name.endswith(".csv"):
        grids = [Grid(None, _read_csv(data))]
    elif name.endswith(".pdf"):
        from ._pdf import pdf_grids, state_pdf_grids
        grids = state_pdf_grids(data) if file.dataset == "state-iv" else pdf_grids(data)
    else:
        grids = _read_xlsx(data)
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps([{"sheet": g.sheet, "rows": g.rows} for g in grids]), encoding="utf-8")
    return grids


def _read_csv(data: bytes) -> tuple[tuple[str, ...], ...]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    # Some files were saved as UTF-8 and then re-read as cp1252 by the publisher ("FiancÃ©").
    text = text.lstrip("﻿").replace("ï»¿", "")
    return tuple(tuple(row) for row in csv.reader(io.StringIO(text, newline="")))


def _read_xlsx(data: bytes) -> list[Grid]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    found = []
    for ws in wb.worksheets:
        rows = tuple(tuple(_xl_text(v) for v in row) for row in ws.iter_rows(values_only=True))
        if any(any(c.strip() for c in r) for r in rows):
            found.append(Grid(ws.title, rows))
    wb.close()
    return found


def _xl_text(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return repr(v) if v != int(v) else str(int(v))
    return str(v)


_NUM = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$|^-?\d+(\.\d+)?$")


def clean_value(raw: str) -> Value:
    """' 2,132,619 ' -> Reported(2132619); '2475577.75' -> Reported (an estimate); 'N/A', 'null', '-', '' -> Absent.

    Anything else raises: we never guess what a cell means.
    """
    s = raw.strip().replace(" ", "")
    low = s.lower()
    if s == "":
        return Absent("blank")
    if low in ("n/a", "na", "n/a*"):
        return Absent("na")
    if low == "null":
        return Absent("null")
    if re.fullmatch(r"-+", s) or s in ("–", "—"):
        return Absent("dash")
    if _NUM.match(s):
        try:
            return Reported(Decimal(s.replace(",", "")))
        except InvalidOperation:
            pass
    raise UnparseableCell(raw)


class UnparseableCell(ValueError):
    def __init__(self, raw: str):
        super().__init__(f"cannot parse cell text {raw!r}")
        self.raw = raw


def norm(s: str) -> str:
    """Whitespace- and case-insensitive label used for matching headers (never for identity)."""
    return re.sub(r"\s+", " ", s).strip().lower()
