"""Rebuild the tables in USCIS's PDF-only quarterly reports (FY2019Q3–FY2021Q3) as Grids.

Table extraction tools get these PDFs wrong: they merge a whole section's form numbers into one cell and split
"1,926,497" into "1 ,926,497". So we work from word positions instead:
- Header words ("Received", "Approved3", ...) define the columns. Numbers are right-aligned under their header,
  so a number belongs to the column whose header's right edge is nearest to the number's right edge.
- Fragments of one number ("1" + ",926,497") touch each other and are joined before assignment.
- Only words that look like numbers or table codes (N/A, D, N, -) can be values; descriptions that run long
  stay descriptions.
- Lines are clustered by vertical position; labels sometimes sit ~2pt off their numbers, so label words attach
  to the nearest numeric line within 4pt.
- Grid row index = the line's distance from the top of the page in points, so a citation such as
  "page 1!C128" points at column C, 128pt down page 1.
- Section headers ("Family Based") carry a duplicate of the total row's numbers in the text layer of some files;
  their numbers are dropped.
"""
from __future__ import annotations

import io
import re
from collections import defaultdict

from ._cells import Grid

_MEASURES = ("Received", "Approved", "Denied", "Total", "Pending", "Time")
_SECTIONS = {"family based", "employment based", "humanitarian", "citizenship and nationality", "other",
             "supplemental processing", "lawful permanent residence"}
_VALUE = re.compile(r"^[\d,.]+$|^(N/A|D|N|H|-+)$")


def pdf_grids(data: bytes) -> list[Grid]:
    import pdfplumber

    grids: list[Grid] = []
    columns: list[tuple[float, float, str]] | None = None
    desc_x = 95.0
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
            header, header_bottom = _header(words)
            if header:
                columns = header
                desc_x = _description_x(words, header_bottom, min(x0 for x0, _, _ in header) - 12, desc_x)
            if not columns:
                continue
            body = [w for w in words if w["top"] > header_bottom + 1]
            grids.append(_page_grid(f"page {n}", body, columns, desc_x, int(page.height)))
    return grids


def _description_x(words, header_bottom: float, table_x: float, fallback: float) -> float:
    """Where descriptions start, measured from the data: the word after the widest gap in each label line.

    The "Description" header can't be trusted (FY2019Q3 centers it 175pt right of where descriptions begin).
    """
    lines: dict[int, list] = defaultdict(list)
    for w in words:
        if w["top"] > header_bottom and w["x1"] < table_x:
            lines[round(w["top"])].append(w)
    starts = []
    for ws in lines.values():
        ws.sort(key=lambda w: w["x0"])
        gaps = [(b["x0"] - a["x1"], b["x0"]) for a, b in zip(ws, ws[1:])]
        if gaps and max(gaps)[0] > 12:
            starts.append(round(max(gaps)[1]))
    if not starts:
        return fallback
    return max(set(starts), key=starts.count) - 3


def _measure(text: str) -> str | None:
    t = text.rstrip("0123456789")  # header footnotes: "Approved3", "Received2"
    return t if t in _MEASURES else None


def _header(words) -> tuple[list[tuple[float, float, str]] | None, float]:
    approved = [w for w in words if _measure(w["text"]) == "Approved"]
    if not approved:
        return None, 0.0
    y = min(w["top"] for w in approved)
    hdr = [w for w in words if _measure(w["text"]) and abs(w["top"] - y) <= 14]
    if not {"Received", "Approved", "Pending"} <= {_measure(w["text"]) for w in hdr}:
        return None, 0.0  # measure words inside footnote prose (FY2020Q3 page 2) are not a header
    if max(w["x0"] for w in hdr) - min(w["x0"] for w in hdr) < 120:
        return None, 0.0  # a definitions list ("Forms Received are...") stacks them at the margin; headers spread out
    return sorted((w["x0"], w["x1"], _measure(w["text"])) for w in hdr), max(w["bottom"] for w in hdr)


def _join_fragments(ws: list[dict]) -> list[dict]:
    """'1' + ',926,497' printed as touching fragments become one token."""
    out: list[dict] = []
    for w in sorted(ws, key=lambda w: w["x0"]):
        if out and w["x0"] - out[-1]["x1"] <= 1.2:
            out[-1] = {**out[-1], "text": out[-1]["text"] + w["text"], "x1": w["x1"]}
        else:
            out.append(dict(w))
    return out


def _page_grid(sheet: str, words, columns, desc_x: float, height: int) -> Grid:
    table_x = min(x0 for x0, _, _ in columns) - 12

    # Cluster value-like words right of the label area into lines.
    value_lines: dict[float, list[dict]] = defaultdict(list)
    labels = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if w["x0"] >= table_x and _VALUE.match(w["text"]):
            key = next((k for k in value_lines if abs(k - w["top"]) <= 2.5), w["top"])
            value_lines[key].append(w)
        else:
            labels.append(w)

    # A row's values are sometimes printed up to ~3pt below the rest (FY2021 PDFs). A value line whose columns are
    # all empty in its nearest neighbour within 4pt belongs to that neighbour.
    def cols_of(ws):
        return {min(range(len(columns)), key=lambda k: abs(columns[k][1] - t["x1"])) for t in _join_fragments(ws)}
    for y in sorted(value_lines, key=lambda y: len(value_lines[y])):
        if y not in value_lines:
            continue
        near = [k for k in value_lines if k != y and abs(k - y) <= 4 and not cols_of(value_lines[k]) & cols_of(value_lines[y])]
        if near:
            target = min(near, key=lambda k: abs(k - y))
            if len(value_lines[target]) >= len(value_lines[y]):
                value_lines[target].extend(value_lines.pop(y))

    lines: dict[float, dict] = {}
    for y, ws in value_lines.items():
        cells: dict[int, str] = {}
        for tok in _join_fragments(ws):
            i = min(range(len(columns)), key=lambda k: abs(columns[k][1] - tok["x1"]))
            cells[i] = cells.get(i, "") + tok["text"]
        lines[y] = {"label": [], "desc": [], "cells": cells}
    for w in labels:
        near = min(lines, key=lambda y: abs(y - w["top"]), default=None)
        y = near if near is not None and abs(near - w["top"]) <= 4 else w["top"]
        line = lines.setdefault(y, {"label": [], "desc": [], "cells": {}})
        line["label" if w["x0"] < desc_x else "desc"].append(w)

    rows: list[list[str]] = [[] for _ in range(height + 2)]
    # Synthetic measure row at the top of every page so the quarterly parser finds the same columns on each page.
    rows[0] = ["", "", *[f"Forms {name}" if name == "Received" else name for _, _, name in columns]]
    for y, line in sorted(lines.items()):
        label = " ".join(w["text"] for w in sorted(line["label"], key=lambda w: w["x0"]))
        desc = " ".join(w["text"] for w in sorted(line["desc"], key=lambda w: w["x0"]))
        cells = [line["cells"].get(i, "") for i in range(len(columns))]
        if label.lower() in _SECTIONS and not desc:
            cells = [""] * len(columns)
        r = max(1, min(height, round(y)) - 1)  # CellRef adds 1, so the cited row equals the point position
        while rows[r]:  # two lines can't share a row index; nudge down a point
            r += 1
        rows[r] = [label, desc, *cells]
    return Grid(sheet, tuple(tuple(r) for r in rows))


# ---------------------------------------------------------------- State Department monthly issuance PDFs
_CLASS = re.compile(r"^[A-Z][A-Z0-9]{0,4}$")


def state_pdf_grids(data: bytes) -> list[Grid]:
    """Three columns: country, visa class, issuances. Rebuilt per page with the same row = points rule.

    Row 0 of each page carries the report title and row 1 the column header, so the State parser reads PDF and
    spreadsheet grids the same way. The October 2019 file prints classes left of the "Visa" header and 1pt off
    their row, so classes are recognized by shape near that column and lines cluster within 2.5pt.
    """
    import pdfplumber

    grids: list[Grid] = []
    title = ""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
            if not title:
                title = " ".join(w["text"] for w in words if re.search(r"\(FY", w["text"]) or w["top"] < 150)
            visa = next((w for w in words if w["text"] == "Visa"
                         and any(v["text"] == "Class" and abs(v["top"] - w["top"]) < 3 for v in words)), None)
            if visa is None:
                continue
            klass = next(v for v in words if v["text"] == "Class" and abs(v["top"] - visa["top"]) < 3)
            lines: dict[float, dict[str, list]] = {}
            for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
                if w["top"] <= visa["bottom"] + 1 or w["text"] in ("Page", "of"):
                    continue
                y = next((k for k in lines if abs(k - w["top"]) <= 2.5), w["top"])
                line = lines.setdefault(y, {"country": [], "class": [], "value": []})
                if w["x0"] > klass["x1"] and re.match(r"^[\d,]+$", w["text"]):
                    line["value"].append(w)
                elif visa["x0"] - 60 <= w["x0"] < klass["x1"] and _CLASS.match(w["text"]):
                    line["class"].append(w)
                else:
                    line["country"].append(w)
            rows: list[list[str]] = [[] for _ in range(int(page.height) + 2)]
            rows[0] = [title, "", ""]
            rows[1] = ["Foreign State of Chargeability or Place of Birth", "Visa Class", "Issuances"]
            for y, line in sorted(lines.items()):
                if not line["value"]:
                    continue  # header continuations, page numbers, footnotes ("*Formerly known as Swaziland")
                text = lambda ws: " ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"]))
                r = max(2, round(y) - 1)
                while rows[r]:
                    r += 1
                rows[r] = [text(line["country"]), text(line["class"]), "".join(w["text"] for w in line["value"])]
            grids.append(Grid(f"page {n}", tuple(tuple(r) for r in rows)))
    return grids
