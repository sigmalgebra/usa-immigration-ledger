"""Consistency checks over the ledger. Pure. Results are published (checks.json): doubts stay in view.

Severity: error blocks the build; warn is published and shown on /methodology; info is published only.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .model import Fact, FiscalQuarter, Ledger, ParsedFile, Reported, SourceFile


@dataclass(frozen=True)
class CheckResult:
    check: str
    severity: str  # "error" | "warn" | "info"
    subject: str
    detail: str
    cells: tuple[str, ...] = ()  # "source_id:address" for every cell involved


def _n(f: Fact | None) -> Decimal | None:
    return f.value.number if f is not None and isinstance(f.value, Reported) else None


def _cite(*facts: Fact) -> tuple[str, ...]:
    return tuple(f"{f.cell.source_id}:{f.cell.address}" for f in facts)


def run(ledger: Ledger, per_file: list[tuple[SourceFile, ParsedFile, list[Fact]]]) -> list[CheckResult]:
    out: list[CheckResult] = [CheckResult(check, "warn", f.original_name, detail)
                              for f, parsed, _ in per_file for check, detail in parsed.warnings]
    out += _pending_printed_twice(per_file)
    out += _completions_add_up(ledger)
    out += _repeated_processing_times(ledger)
    out += _large_revisions(ledger)
    out += _estimates(ledger)
    out += _state_grand_total(ledger)
    out += _state_duplicate_rows(ledger)
    return out


def _pending_printed_twice(per_file) -> list[CheckResult]:
    """Pending is a stock, printed in both the quarter and the FYTD block of one file. They must agree."""
    out = []
    for f, _p, facts in per_file:
        if f.dataset != "uscis-forms":
            continue
        by = {(x.key.series, x.key.period, x.key.window): x for x in facts if x.key.measure == "pending"}
        for (s, p, w), snap in by.items():
            if w != "snapshot":
                continue
            fytd = by.get((s, p, "fytd"))
            a, b = _n(snap), _n(fytd)
            if a is not None and b is not None and a != b:
                out.append(CheckResult("pending-printed-twice", "warn", f"{s} {p}",
                                       f"{f.original_name} prints pending as {a:,} in the quarter block and {b:,} "
                                       f"in the fiscal-year-to-date block", _cite(snap, fytd)))
    return out


def _completions_add_up(ledger: Ledger) -> list[CheckResult]:
    """approved + denied should equal total completions (USCIS footnote lists exceptions, e.g. N-648)."""
    out = []
    keys = defaultdict(dict)
    for k, f in ledger.facts.items():
        if k.measure in ("approved", "denied", "completed") and k.window == "quarter":
            keys[(k.series, k.period)][k.measure] = f
    for (s, p), m in sorted(keys.items(), key=lambda x: (x[0][0], str(x[0][1]))):
        a, d, c = (_n(m.get(x)) for x in ("approved", "denied", "completed"))
        if None in (a, d, c):
            continue
        if abs(a + d - c) > max(Decimal(1), c * Decimal("0.001")):
            out.append(CheckResult("completions-add-up", "info", f"{s} {p}",
                                   f"approved {a:,} + denied {d:,} = {a + d:,}, but completions printed as {c:,}",
                                   _cite(*m.values())))
    return out


def _repeated_processing_times(ledger: Ledger) -> list[CheckResult]:
    """A median processing time identical to 7+ significant digits in consecutive quarters is a copy, not a measurement."""
    out = []
    by_series = defaultdict(dict)
    for k, f in ledger.facts.items():
        if k.measure == "median_processing_months" and k.window == "quarter" and isinstance(k.period, FiscalQuarter):
            by_series[k.series][k.period] = f
    for s, quarters in sorted(by_series.items()):
        for p, f in sorted(quarters.items()):
            prev = quarters.get(_prev_q(p))
            a, b = _n(f), _n(prev)
            if a is None or b is None or a != b:
                continue
            if len(a.normalize().as_tuple().digits) >= 6:
                out.append(CheckResult("repeated-processing-time", "warn", f"{s} {p}",
                                       f"median processing time {a} months is identical, digit for digit, to "
                                       f"{_prev_q(p)}; the source likely repeated last quarter's value",
                                       _cite(prev, f)))
    return out


def _large_revisions(ledger: Ledger) -> list[CheckResult]:
    out = []
    for r in ledger.revisions:
        a, b = _n(r.superseded), _n(r.current)
        if a is None or b is None or a == 0:
            continue
        change = (b - a) / a
        if abs(change) >= Decimal("0.05") and max(a, b) >= 1000:
            out.append(CheckResult("large-revision", "warn", f"{r.key.series} {r.key.period} {r.key.measure}",
                                   f"first printed as {a:,}, later revised to {b:,} ({change:+.1%})",
                                   _cite(r.superseded, r.current)))
    return out


def _estimates(ledger: Ledger) -> list[CheckResult]:
    est = [f for f in ledger.facts.values() if isinstance(f.value, Reported) and f.value.is_estimate
           and f.key.measure != "median_processing_months"]
    if not est:
        return []
    by_source = defaultdict(list)
    for f in est:
        by_source[f.cell.source_id].append(f)
    return [CheckResult("fractional-count", "warn", ledger.sources[sid].original_name,
                        f"{len(fs)} counts are printed with fractions (e.g. {fs[0].value.number}); USCIS estimated "
                        f"them rather than counting, so we publish them as estimates", _cite(*fs[:5]))
            for sid, fs in sorted(by_source.items())]


def _state_grand_total(ledger: Ledger) -> list[CheckResult]:
    """State Dept GRAND TOTAL must equal the sum of its rows."""
    out = []
    sums: dict = defaultdict(Decimal)
    totals: dict = {}
    for k, f in ledger.facts.items():
        if not k.series.startswith("state-iv/"):
            continue
        if k.series == "state-iv/grand-total/all":
            totals[k.period] = f
        elif (n := _n(f)) is not None:
            sums[k.period] += n
    for p, t in sorted(totals.items()):
        if _n(t) != sums[p]:
            out.append(CheckResult("state-grand-total", "warn", str(p),
                                   f"GRAND TOTAL printed as {_n(t):,} but rows sum to {sums[p]:,}", _cite(t)))
    return out


def _state_duplicate_rows(ledger: Ledger) -> list[CheckResult]:
    out = []
    for k, f in sorted(ledger.facts.items(), key=lambda kv: (str(kv[0].period), kv[0].series)):
        s = ledger.series[k.series]
        if s.dataset == "state-iv" and "(row " in s.dims["visa_class"]:
            out.append(CheckResult("state-duplicate-rows", "warn", f"{k.period} {s.dims['country']}",
                                   f"{s.dims['country']} / {s.dims['visa_class'].split(' (')[0]} is printed on more than "
                                   f"one row; this row reads {_n(f)}. Both rows count toward GRAND TOTAL, so we keep "
                                   f"them apart instead of guessing", _cite(f)))
    return out


def _prev_q(p: FiscalQuarter) -> FiscalQuarter:
    return FiscalQuarter(p.fy - 1, 4) if p.q == 1 else FiscalQuarter(p.fy, p.q - 1)
