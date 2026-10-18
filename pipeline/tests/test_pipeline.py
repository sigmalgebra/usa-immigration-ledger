"""Run from the repo root:  python -m pytest pipeline/tests"""
from __future__ import annotations

import csv
import io
import random
import tomllib
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from usdata import archive
from usdata.build import build, select
from usdata.datasets._cells import UnparseableCell, clean_value
from usdata.identity import load_catalog, split_label
from usdata.model import Absent, FactKey, Reported, parse_period
from usdata.publish import render

PINS = tomllib.loads((Path(__file__).parent / "pins.toml").read_text(encoding="utf-8"))["pin"]


@pytest.fixture(scope="session")
def built():
    return build()


def _raw_cell(pin: dict) -> str:
    """Read the pinned cell straight from the archived file, bypassing every parser."""
    [f] = [f for f in archive.load_manifest() if f.archive_path.endswith(pin["file"])]
    data = archive.read_bytes(f)
    if "pdf_text" in pin:
        import pdfplumber
        page = int(pin["cell"].split("!")[0].removeprefix("page "))
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = pdf.pages[page - 1].extract_text() or ""
        # The PDF's text layer splits digits ("7 8,008"), so compare with spaces removed.
        assert pin["pdf_text"] in text.replace(" ", ""), "pinned value not on that PDF page"
        return pin["pdf_text"]
    if "cell" in pin:
        sheet, ref = pin["cell"].split("!")
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        v = wb[sheet][ref].value
        return repr(v) if isinstance(v, float) else str(v)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    return rows[pin["line"] - 1][ord(pin["col"]) - ord("A")]


@pytest.mark.parametrize("pin", PINS, ids=[p["what"] for p in PINS])
def test_pin(pin, built):
    ledger, _ = built
    expected = Decimal(pin["value"])
    assert Decimal(_raw_cell(pin).strip().replace(",", "")) == expected, "pin disagrees with the raw file"

    fact = ledger.get(FactKey(pin["series"], parse_period(pin["period"]), pin["measure"], pin["window"]))
    assert isinstance(fact.value, Reported) and fact.value.number == expected
    src = ledger.sources[fact.cell.source_id]
    assert src.archive_path.endswith(pin["file"])
    want = pin["cell"] if "cell" in pin else f"line {pin['line']}, column {pin['col']}"
    assert fact.cell.address == want


def test_fractional_counts_are_estimates(built):
    ledger, _ = built
    f = ledger.get(FactKey("total", parse_period("FY2024Q1"), "approved", "quarter"))
    assert f.value.is_estimate


def test_known_source_errors_are_flagged(built):
    _, checks = built
    kinds = {c.check for c in checks}
    assert {"repeated-processing-time", "source-title-mismatch", "pending-printed-twice", "fractional-count",
            "state-duplicate-rows"} <= kinds
    assert not [c for c in checks if c.severity == "error"]


def test_i130_split_is_not_joined(built):
    """USCIS split I-130 into two rows in FY2025Q1; the combined series must stop, not continue as a sum."""
    ledger, _ = built
    periods = {str(k.period) for k in ledger.facts if k.series == "i-130"}
    assert max(periods) == "FY2024Q4"
    assert "FY2025Q1" in {str(k.period) for k in ledger.facts if k.series == "i-130-immediate-relative"}


def test_build_is_deterministic_and_order_independent(built):
    ledger, checks = built
    first = render(ledger, checks)
    again = render(*build())
    assert first == again


def test_select_ignores_input_order(built):
    ledger, _ = built
    by_source: dict = {}
    for f in ledger.facts.values():
        by_source.setdefault(f.cell.source_id, []).append(f)
    # Rebuild the per-file view for current facts only and shuffle it; the winner set must not change.
    from usdata.model import ParsedFile
    items = [(ledger.sources[sid], ParsedFile(parse_period("FY2026Q3"), (), frozenset()), fs)
             for sid, fs in by_source.items()]
    a, _ = select(items)
    random.Random(7).shuffle(items)
    b, _ = select(items)
    assert a == b


def test_archive_add_is_idempotent(tmp_path):
    f1, created1 = archive.add(b"abc", dataset="state-iv", url="u", original_name="x.xlsx", retrieved_by="human", root=tmp_path)
    f2, created2 = archive.add(b"abc", dataset="state-iv", url="u", original_name="x.xlsx", retrieved_by="human", root=tmp_path)
    assert created1 and not created2 and f1 == f2
    assert len((tmp_path / "manifest.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("label,footnotes,expected", [
    ("I-6007", {"7"}, "I-600"),
    ("I-13122", {"22"}, "I-131"),
    ("N-40020", {"20"}, "N-400"),
    ("DS-23022", {"22"}, "DS-230"),
    ("Waivers 7", {"7"}, "Waivers"),
    ("I-526E", set(), "I-526E"),       # a real code, never split
    ("I-6007", set(), None),          # 7 is not a footnote in this file: refuse to guess
    ("I-9999", {"9"}, None),
])
def test_split_label(label, footnotes, expected):
    cat = load_catalog().forms
    assert split_label(label, cat.codes, cat.label_aliases, frozenset(footnotes)) == expected


@pytest.mark.parametrize("raw,expected", [
    (" 2,132,619 ", Reported(Decimal("2132619"))),
    ("2475577.752151262", Reported(Decimal("2475577.752151262"))),
    (" -   ", Absent("dash")),
    (" N/A ", Absent("na")),
    ("null", Absent("null")),
    ("", Absent("blank")),
])
def test_clean_value(raw, expected):
    assert clean_value(raw) == expected


def test_clean_value_refuses_to_guess():
    with pytest.raises(UnparseableCell):
        clean_value("12k")


# Every quarterly file reprints earlier quarters, and USCIS revises some of them, so two files never agree 100%.
# Spreadsheet-to-spreadsheet pairs agree on 64-96% of shared values; a PDF misread (shifted column, split digits)
# would collapse that. These pairs include the PDF-only years, so a regression in the PDF reader fails here.
OVERLAP_PAIRS = [
    ("Quarterly_All_Forms_FY19Q2.csv", "Quarterly_All_Forms_FY19Q3.pdf"),
    ("Quarterly_All_Forms_FY2020Q1.pdf", "Quarterly_All_Forms_FY2020Q2.pdf"),
    ("Quarterly_All_Forms_FY2020Q2.pdf", "Quarterly_All_Forms_FY2020Q3.pdf"),
    ("Quarterly_All_Forms_FY2020Q3.pdf", "Quarterly_All_Forms_FY2020Q4.pdf"),
]


@pytest.mark.parametrize("a,b", OVERLAP_PAIRS)
def test_pdf_reader_agrees_with_neighbouring_reports(a, b, built):
    ledger, _ = built
    from usdata.datasets import DATASETS
    from usdata.identity import resolve_form
    cat = load_catalog().forms

    def values(name: str) -> dict:
        [f] = [f for f in archive.load_manifest() if f.original_name == name]
        parsed = DATASETS[f.dataset].parse(f, archive.read_bytes(f))
        out = {}
        for o in parsed.observations:
            sid = resolve_form(cat, o.row, parsed.footnotes)
            if isinstance(sid, str) and isinstance(o.value, Reported) and o.window.value != "fytd":
                out[(sid, str(o.period), o.measure.value)] = o.value.number
        return out

    va, vb = values(a), values(b)
    shared = [k for k in va if k in vb]
    assert len(shared) >= 150
    agree = sum(va[k] == vb[k] for k in shared) / len(shared)
    assert agree >= 0.75, f"only {agree:.0%} of {len(shared)} shared values agree"
    # Revisions are small; a misread column would produce wild ratios on many values at once.
    wild = [k for k in shared if va[k] and not (Decimal("0.5") <= vb[k] / va[k] <= Decimal("2"))]
    assert len(wild) <= len(shared) * 0.03, f"{len(wild)} values changed by more than 2x: {wild[:5]}"
