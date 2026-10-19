"""Ledger -> site/public/data/v1/. Output is sorted and timestamp-free so rebuilds are byte-identical
and git diffs show only real data changes. The site reads these files at build time and serves them for download.
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import forecast as fc
from .archive import REPO
from .checks import CheckResult
from .datasets import DATASETS
from .model import Absent, Current, Cutoff, Fact, Ledger, Reported

SCHEMA_VERSION = "v1"
DATA = REPO / "site" / "public" / "data" / SCHEMA_VERSION
GITHUB_BLOB = "https://github.com/sigmalgebra/usa-immigration-ledger/blob/main/"

FACT_COLUMNS = ["series_id", "period", "measure", "window", "value", "status", "source_id", "cell", "raw"]


def status(f: Fact) -> str:
    if isinstance(f.value, Absent):
        return f.value.reason
    if isinstance(f.value, Cutoff):
        return "date"
    if isinstance(f.value, Current):
        return "current"
    return "estimate" if f.value.is_estimate and f.key.measure != "median_processing_months" else "reported"


def number(f: Fact) -> int | float | None:
    if not isinstance(f.value, Reported):
        return None
    n: Decimal = f.value.number
    return int(n) if n == n.to_integral_value() else float(n)


def text(f: Fact) -> str:
    """The CSV `value` column: a number, an ISO date for a bulletin cutoff, or empty."""
    return f.value.date.isoformat() if isinstance(f.value, Cutoff) else _fmt(number(f))


def _sort_key(f: Fact):
    return (f.key.series, str(f.key.period), f.key.measure, f.key.window)


def render(ledger: Ledger, checks: list[CheckResult]) -> dict[str, str]:
    """Every output file as {relative path: text}. Pure, so `--check` can compare without writing."""
    out: dict[str, str] = {}
    facts_by_ds: dict[str, list[Fact]] = defaultdict(list)
    for f in ledger.facts.values():
        facts_by_ds[ledger.series[f.key.series].dataset].append(f)

    for ds, facts in sorted(facts_by_ds.items()):
        facts.sort(key=_sort_key)
        out[f"{ds}/facts.csv"] = _csv(FACT_COLUMNS, ([f.key.series, str(f.key.period), f.key.measure, f.key.window,
                                                     text(f), status(f), f.cell.source_id, f.cell.address,
                                                     f.cell.raw] for f in facts))

    # Site-facing compact JSON for the forms report: one object per series, values keyed for direct lookup.
    forms = []
    for sid, s in sorted(ledger.series.items()):
        if s.dataset != "uscis-forms":
            continue
        rows = [[str(f.key.period), f.key.measure, f.key.window, number(f), status(f), f.cell.source_id,
                 f.cell.address, f.cell.raw] for f in sorted(ledger.series_facts(sid), key=_sort_key)]
        forms.append({"id": sid, "code": s.form, "category": s.category, "title": s.title, "note": s.note,
                      "facts": rows})
    out["uscis-forms/series.json"] = _json(forms)
    out["uscis-forms/wide.csv"] = _wide(ledger)

    awaiting = [[s.dims["country"], s.dims["preference"], str(f.key.period), number(f), status(f), f.cell.source_id,
                 f.cell.address] for f in sorted(facts_by_ds.get("uscis-i130-awaiting", []), key=_sort_key)
                for s in [ledger.series[f.key.series]]]
    out["uscis-i130-awaiting/compact.json"] = _json(awaiting)

    state = [[str(f.key.period), s.dims["country"], s.dims["visa_class"], number(f), f.cell.source_id, f.cell.address]
             for f in sorted(facts_by_ds.get("state-iv", []), key=_sort_key) for s in [ledger.series[f.key.series]]]
    out["state-iv/compact.json"] = _json(state)

    out.update(_visa_bulletin(ledger, facts_by_ds.get("state-vb", [])))

    sources = sorted(ledger.sources.values(), key=lambda f: (f.dataset, f.original_name))
    out["sources.json"] = _json([{**asdict(f), "archive_url": GITHUB_BLOB + f.archive_path,
                                  "publisher": DATASETS[f.dataset].publisher} for f in sources])
    out["sources.csv"] = _csv(["source_id", "dataset", "original_name", "url", "sha256", "retrieved_at",
                               "retrieved_by", "archive_path"],
                              ([f.source_id, f.dataset, f.original_name, f.url, f.sha256, f.retrieved_at,
                                f.retrieved_by, f.archive_path] for f in sources))
    out["revisions.csv"] = _csv(
        ["series_id", "period", "measure", "window", "old_value", "old_source_id", "old_cell", "new_value",
         "new_source_id", "new_cell"],
        ([r.key.series, str(r.key.period), r.key.measure, r.key.window, text(r.superseded),
          r.superseded.cell.source_id, r.superseded.cell.address, text(r.current), r.current.cell.source_id,
          r.current.cell.address] for r in ledger.revisions))
    out["checks.json"] = _json([asdict(c) for c in checks])
    out["datapackage.json"] = _json(_datapackage(sorted(facts_by_ds)))
    return out


HISTORY_MONTHS = 120


def _visa_bulletin(ledger: Ledger, facts: list[Fact]) -> dict[str, str]:
    """visa-bulletin/forecast.json indexes every series with its backtest; each series still printed in the
    latest bulletin gets visa-bulletin/forecast/<chart>/<category>/<area>.json with history, bands and crossings."""
    if not facts:
        return {}
    by_series: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        by_series[f.key.series].append(f)
    histories = {sid: fc.History.of([(f.key.period, f.value if isinstance(f.value, (Cutoff, Current)) else None)
                                     for f in fs]) for sid, fs in by_series.items()}
    results = fc.run(histories)
    latest = max(f.key.period for f in facts)
    out: dict[str, str] = {}
    index = []
    for sid, fs in sorted(by_series.items()):
        s = ledger.series[sid]
        fs = sorted(fs, key=lambda f: f.key.period)
        r = results.series[sid]
        fcst = r.forecast if fs[-1].key.period == latest else None
        path = f"forecast/{sid.removeprefix('vb/')}.json" if fcst else None
        index.append({"id": sid, **s.dims, "title": s.title, "description": s.note, "first": str(fs[0].key.period),
                      "latest": _vb_row(fs[-1]), "path": path,
                      "backtest": {m: asdict(fit) for m, fit in r.fits.items()}})
        if path and fcst:
            out[f"visa-bulletin/{path}"] = _json({
                "id": sid,
                "history": [_vb_row(f) for f in fs if fc.months_between(f.key.period, latest) < HISTORY_MONTHS],
                "forecast": {"model": fcst.model, "spread": fcst.spread, "origin": str(fcst.origin),
                             "cutoff": _iso(fcst.cutoff), "bands": fcst.bands,
                             "crossings": [[p, *q] for p, q in fcst.crossings]},
            })
    out["visa-bulletin/forecast.json"] = _json({
        "latest": str(latest), "model": results.model, "spread": results.pooled[results.model].spread,
        "method": {"horizon_months": fc.HORIZON, "bootstrap_paths": fc.N_PATHS, "pace_levels": fc.PACE_LEVELS,
                   "backtest_bootstrap_paths": fc.BACKTEST_PATHS, "band_levels": fc.BAND_LEVELS,
                   "backtest_from": str(fc.BACKTEST_FROM), "backtest_leads_months": fc.BACKTEST_LEADS,
                   "spreads": fc.SPREADS, "target_coverage": fc.TARGET_COVERAGE,
                   "calibration_before": str(fc.CALIBRATE_BEFORE), "min_judged": fc.MIN_JUDGED,
                   "dates": "days since 1970-01-01"},
        "pooled": {m: asdict(fit) for m, fit in results.pooled.items()},
        "calibration_curve": {m: [list(pt) for pt in curve] for m, curve in results.calibration_curve.items()},
        "series": index,
    })
    return out


def _vb_row(f: Fact) -> list:
    return [str(f.key.period), text(f) or None, status(f), f.cell.source_id, f.cell.address, f.cell.raw]


def _iso(d: int) -> str:
    return date.fromordinal(d + date(1970, 1, 1).toordinal()).isoformat()


def write(ledger: Ledger, checks: list[CheckResult], out_dir: Path = DATA) -> None:
    files = render(ledger, checks)
    tmp = out_dir.with_name(out_dir.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.replace(tmp, out_dir)


def differs(ledger: Ledger, checks: list[CheckResult], out_dir: Path = DATA) -> bool:
    files = render(ledger, checks)
    on_disk = {p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*") if p.is_file()} if out_dir.exists() else set()
    if on_disk != set(files):
        return True
    return any((out_dir / rel).read_text(encoding="utf-8") != text for rel, text in files.items())


def _fmt(n: int | float | None) -> str:
    return "" if n is None else repr(n) if isinstance(n, float) else str(n)


def _csv(header: list[str], rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n"


def _wide(ledger: Ledger) -> str:
    """One row per (series, quarter), one column per measure. For spreadsheet users; no provenance columns."""
    measures = ["received", "approved", "denied", "completed", "pending", "median_processing_months"]
    rows: dict = defaultdict(dict)
    for f in ledger.facts.values():
        s = ledger.series[f.key.series]
        if s.dataset != "uscis-forms" or f.key.window not in ("quarter", "snapshot"):
            continue
        rows[(f.key.series, f.key.period)][f.key.measure] = number(f)
    return _csv(["series_id", "title", "category", "fiscal_quarter", *measures],
                ([sid, ledger.series[sid].title, ledger.series[sid].category, str(p),
                  *(_fmt(m.get(x)) for x in measures)] for (sid, p), m in sorted(rows.items(), key=lambda x: (x[0][0], str(x[0][1])))))


def _fields(ds: str) -> list[dict]:
    if ds == "state-vb":
        value = {"name": "value", "type": "date", "description": "The printed cutoff date; empty when status is "
                                                                  "current (C) or unavailable (U)"}
    else:
        value = {"name": "value", "type": "number", "description": "Empty when the source printed no number; see status"}
    return [
        {"name": "series_id", "type": "string", "description": "Stable series identifier (see uscis-forms/series.json)"},
        {"name": "period", "type": "string", "description": "FY2026Q3 (federal fiscal quarter) or 2024-09 (month)"},
        {"name": "measure", "type": "string"},
        {"name": "window", "type": "string", "description": "quarter | fytd | month | snapshot (stocks such as pending)"},
        value,
        {"name": "status", "type": "string",
         "description": "reported | estimate (fractional count) | dash (USCIS: zero or rounds to 0) | na | null | "
                        "blank | withheld (USCIS D/H privacy suppression) | date | current (Visa Bulletin C) | "
                        "unavailable (Visa Bulletin U)"},
        {"name": "source_id", "type": "string", "description": "Join to sources.csv"},
        {"name": "cell", "type": "string", "description": "Exact cell in the archived source file"},
        {"name": "raw", "type": "string", "description": "Cell text exactly as printed"},
    ]


def _datapackage(datasets: list[str]) -> dict:
    return {
        "name": "us-immigration-ledger",
        "title": "US immigration ledger: USCIS caseload and State Department visa issuances, cell-level provenance",
        "licenses": [{"name": "CC0-1.0", "title": "Public domain dedication (source data are U.S. government works)"}],
        "resources": [{"name": ds, "path": f"{ds}/facts.csv", "format": "csv",
                       "title": DATASETS[ds].title, "sources": [{"title": DATASETS[ds].publisher, "path": DATASETS[ds].listing}],
                       "schema": {"fields": _fields(ds)}} for ds in datasets]
               + [{"name": "sources", "path": "sources.csv", "format": "csv"},
                  {"name": "revisions", "path": "revisions.csv", "format": "csv"}],
    }
