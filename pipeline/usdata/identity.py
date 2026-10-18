"""Resolve a parser's verbatim RowKey to a stable SeriesId using catalog/*.toml.

The catalog is the only place that decides which printed rows are the same series across years.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .archive import REPO
from .datasets._cells import norm
from .model import RowKey, Series, SeriesId

CATALOG = REPO / "catalog"


class Unmapped(NamedTuple):
    row: RowKey
    reason: str
    suggestion: str


@dataclass(frozen=True)
class FormsCatalog:
    codes: frozenset[str]
    label_aliases: dict[str, str]
    by_code: dict[str, list[tuple[Series, frozenset[str]]]]  # code -> [(series, normalized descriptions)]

    @property
    def series(self) -> list[Series]:
        return [s for entries in self.by_code.values() for s, _ in entries]


@dataclass(frozen=True)
class Catalog:
    forms: FormsCatalog
    countries: dict[str, str]  # normalized printed name -> canonical display name


def load_catalog(root: Path = CATALOG) -> Catalog:
    raw = tomllib.loads((root / "uscis-forms.toml").read_text(encoding="utf-8"))
    codes = frozenset(raw["codes"])
    by_code: dict[str, list[tuple[Series, frozenset[str]]]] = {}
    ids: set[str] = set()
    for s in raw["series"]:
        if s["code"] not in codes:
            raise ValueError(f"catalog series {s['id']} uses unknown code {s['code']}")
        if s["id"] in ids:
            raise ValueError(f"duplicate series id {s['id']}")
        ids.add(s["id"])
        series = Series(SeriesId(s["id"]), "uscis-forms", None if s["code"] == "TOTAL" else s["code"],
                        s["category"], s["title"], {}, s.get("note"))
        by_code.setdefault(s["code"], []).append((series, frozenset(norm(d) for d in s.get("descriptions", []))))
    for code, entries in by_code.items():
        if len(entries) > 1 and any(not d for _, d in entries):
            raise ValueError(f"code {code} has several series, so every one needs `descriptions`")
        seen: set[str] = set()
        for _, descs in entries:
            if seen & descs:
                raise ValueError(f"code {code}: description listed under two series: {seen & descs}")
            seen |= descs

    countries_path = root / "countries.toml"
    countries = {}
    if countries_path.exists():
        countries = {norm(k): v for k, v in tomllib.loads(countries_path.read_text(encoding="utf-8"))["aliases"].items()}
    return Catalog(FormsCatalog(codes, raw.get("label_aliases", {}), by_code), countries)


def split_label(label: str, codes: frozenset[str], aliases: dict[str, str], footnotes: frozenset[str]) -> str | None:
    """'I-6007' -> 'I-600' when 7 is a footnote declared in this file. None when the label can't be placed."""
    label = label.strip()
    for candidate in (label, aliases.get(label)):
        if candidate in codes:
            return candidate
    m = re.match(r"^(.*?)\s*(\d{1,2})$", label)
    if m:
        for cut in range(1, 3):
            head, digits = label[:-cut].rstrip(), label[-cut:]
            if not digits.isdigit() or digits not in footnotes:
                continue
            for candidate in (head, aliases.get(head)):
                if candidate in codes:
                    return candidate
    return None


def resolve_form(cat: FormsCatalog, row: RowKey, footnotes: frozenset[str]) -> SeriesId | Unmapped:
    _category, label, desc = row.labels
    code = split_label(label, cat.codes, cat.label_aliases, footnotes)
    if code is None:
        return Unmapped(row, f"label {label!r} is not a known code (footnotes in file: {sorted(footnotes, key=int)})",
                        f'add the code to `codes`, or to [label_aliases]: "{label}" = "<code>"')
    entries = cat.by_code.get(code)
    if not entries:
        return Unmapped(row, f"no series for code {code}",
                        f'[[series]]\nid = "{code.lower()}"\ncode = "{code}"\ncategory = "{_category}"\ntitle = "{code} {desc}"')
    if len(entries) == 1 and not entries[0][1]:
        return entries[0][0].id
    for series, descs in entries:
        if norm(desc) in descs:
            return series.id
    options = ", ".join(s.id for s, _ in entries)
    return Unmapped(row, f"{code} description {desc!r} matches none of: {options}",
                    f'add "{desc}" to the `descriptions` of the right {code} series, or add a new [[series]]')


def country_name(cat: Catalog, printed: str) -> str:
    return cat.countries.get(norm(printed), printed.strip())


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
