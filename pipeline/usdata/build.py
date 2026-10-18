"""build(): archive/ + catalog/ -> Ledger. The only place pipeline stages are sequenced."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from . import archive, checks
from .checks import CheckResult
from .datasets import DATASETS
from .identity import Unmapped, country_name, load_catalog, resolve_bulletin, resolve_form, slug
from .model import Fact, FactKey, Ledger, ParsedFile, Revision, Series, SeriesId, SourceFile


class UnmappedRows(Exception):
    def __init__(self, rows: list[tuple[SourceFile, Unmapped]]):
        lines = [f"{f.original_name}: {u.reason}\n    fix: {u.suggestion}" for f, u in rows]
        super().__init__(f"{len(rows)} row(s) not in the catalog:\n" + "\n".join(lines))
        self.rows = rows


class ConflictingCells(Exception):
    pass


def build() -> tuple[Ledger, list[CheckResult]]:
    catalog = load_catalog()
    sources = archive.load_manifest()
    series: dict[SeriesId, Series] = {s.id: s for s in catalog.forms.series}
    unmapped: list[tuple[SourceFile, Unmapped]] = []
    per_file: list[tuple[SourceFile, ParsedFile, list[Fact]]] = []

    for f in sources:
        parsed = DATASETS[f.dataset].parse(f, archive.read_bytes(f))
        facts: dict[FactKey, Fact] = {}
        for o in parsed.observations:
            if f.dataset == "uscis-forms":
                sid = resolve_form(catalog.forms, o.row, parsed.footnotes)
                if isinstance(sid, Unmapped):
                    unmapped.append((f, sid))
                    continue
            elif f.dataset == "uscis-i130-awaiting":
                country, pref = o.row.labels
                name = country_name(catalog, country)
                sid = SeriesId(f"i130-awaiting/{slug(name)}/{pref.lower()}")
                series.setdefault(sid, Series(sid, f.dataset, "I-130", "Visa queue",
                                              f"Approved I-130s awaiting a visa: {name}, {pref}",
                                              {"country": name, "preference": pref}))
            elif f.dataset == "state-vb":
                resolved = resolve_bulletin(catalog.bulletin, o.row)
                if resolved is None:
                    continue
                if isinstance(resolved, Unmapped):
                    unmapped.append((f, resolved))
                    continue
                sid = resolved.id
                series.setdefault(sid, resolved)
            else:
                country, vclass = o.row.labels
                name = country_name(catalog, country)
                sid = SeriesId(f"state-iv/{slug(name)}/{slug(vclass)}")
                series.setdefault(sid, Series(sid, f.dataset, None, "Immigrant visas issued",
                                              f"Immigrant visas issued: {name}, {vclass}",
                                              {"country": name, "visa_class": vclass}))
            key = FactKey(sid, o.period, o.measure.value, o.window.value)
            fact = Fact(key, o.value, o.cell)
            if key in facts and facts[key].value != fact.value:
                raise ConflictingCells(f"{f.original_name}: {key} printed twice with different values "
                                       f"({facts[key].cell.address} vs {fact.cell.address})")
            facts.setdefault(key, fact)
        per_file.append((f, parsed, list(facts.values())))

    if unmapped:
        # One example per distinct problem keeps the message readable.
        uniq = {(u.reason): (f, u) for f, u in unmapped}
        raise UnmappedRows(sorted(uniq.values(), key=lambda x: x[0].original_name))

    current, revisions = select(per_file)
    used = {k.series for k in current}
    ledger = Ledger(current, tuple(revisions), {k: v for k, v in series.items() if k in used},
                    {f.source_id: f for f in sources})
    return ledger, checks.run(ledger, per_file)


def _precedence(item: tuple[SourceFile, ParsedFile, list[Fact]]) -> tuple:
    f, parsed, _ = item
    return (str(type(parsed.report_period).__name__), parsed.report_period, f.retrieved_at, f.sha256)


def select(per_file: Iterable[tuple[SourceFile, ParsedFile, list[Fact]]]) -> tuple[dict[FactKey, Fact], list[Revision]]:
    """Pure and order-independent: for each FactKey the latest report wins; every loser that differs is a Revision.

    "Latest" = later report period, then later retrieval, then hash (a total order, so rebuilds are byte-identical).
    """
    ordered = sorted(per_file, key=_precedence)
    current: dict[FactKey, Fact] = {}
    history: dict[FactKey, list[Fact]] = defaultdict(list)
    for _f, _p, facts in ordered:
        for fact in facts:
            history[fact.key].append(fact)
            current[fact.key] = fact
    revisions = [Revision(k, old, current[k]) for k, fs in history.items() for old in fs[:-1]
                 if old.value != current[k].value]
    return current, sorted(revisions, key=lambda r: (r.key.series, str(r.key.period), r.key.measure, r.key.window,
                                                      r.superseded.cell.source_id))
