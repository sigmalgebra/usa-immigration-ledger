# Architecture

## Problem

Publish a verifiable investigation of U.S. immigration-benefit throughput from about 80 government spreadsheets
whose layout drifts every year. The data includes CSV and XLSX files, mojibake, header rows anywhere in rows 3–8,
FY2019 files with four quarters side by side, footnote digits glued onto form numbers, rows split and renamed,
dashes and privacy codes instead of numbers, fractional "counts", republished quarters, and old files that vanish
from uscis.gov. travel.state.gov returns 403 to automated clients, and we don't evade it. The site is hosted for
free on Vercel: static, no database. Every published number must trace to a source cell.

## Usage

```bash
PYTHONPATH=pipeline python -m usdata crawl            # new USCIS files -> archive/ (idempotent)
PYTHONPATH=pipeline python -m usdata add FILES --dataset state-iv
PYTHONPATH=pipeline python -m usdata build [--check]   # archive + catalog -> site/public/data/v1
```

```astro
<Fig s="total" p="FY2026Q3" m="pending" />   <!-- renders 12,807,907 linked to FY26Q3_All_Forms!G6; a missing fact fails the build -->
```

## Shape: the fact ledger

- `archive/` (owned by `archive.py`): content-addressed, append-only. `add()` hashes first, so it is a no-op for known bytes.
- `datasets/*.py`, one parser per report family behind a single registry (`datasets/__init__.py`). Parsers emit
  `RawObservation(row labels verbatim, period, measure, window, Value, CellRef)`. No DataFrame crosses a boundary.
- `catalog/uscis-forms.toml` is the single source of truth for identity. Labels split into code + footnote only when
  the code is listed and the digits are a footnote declared in the same file. Multi-row codes need an explicit
  description match. Unknown rows fail the build with a suggested snippet.
- `build.py` sequences parse → resolve → `select` (pure, order-independent precedence: later report, then later
  retrieval, then hash) → `checks.run` → `Ledger`.
- `publish.py` renders the ledger to sorted, timestamp-free files, so rebuilds are byte-identical and `--check` can
  compare them in CI.
- `site/src/lib/ledger.ts` is the only site module that reads the data. `fact()` throws on a miss, which is how the
  build guarantees that prose numbers exist.

`Absent(reason)` is a type, so a dash is never 0. `Reported.is_estimate` marks fractional counts.

## Synthesis decision (arena)

Two independent design candidates were produced: A ("fact ledger", Astro) and B ("manifest + dialects",
Observable Framework + DuckDB-WASM). They converged on everything except the frontend. A is the base because
B bound prose numbers by hand-typing them into spans and then syncing them to SQL (a hand-synced list), while A's
`<Fig>` derives each number at build time. A also yields one static page per form, which search engines can index.

Grafted from B:
- Footnote splitting by membership in a closed code list, instead of an alias for every glued label.
- The wide CSV for spreadsheet users.

Rejected from B:
- The DuckDB-WASM explorer: multiple MB per page for a dataset of a few hundred KB.
- The per-file manifest of overrides, which would be a second home for layout knowledge.

## Tradeoffs accepted

- We accept a human-curated catalog in exchange for no silently merged or invented series.
- We commit derived data (`site/public/data/v1`) in exchange for reviewable data diffs and a Vercel build that needs no Python.
- We accept a cell reference on every row, which doubles the CSV size, in exchange for per-number provenance.
- State Department refresh is manual forever.

## Implementation reconciliation

Each change below was accepted during implementation, with its reason and why the shared contract still holds.

- **Output path** is `site/public/data/v1/`, not `data/v1/`. The site both serves and reads it, so there is one location and no copy step.
- **The file name decides the period when it disagrees with the title** (`ParsedFile.warnings`). Real data forced this: the FY2024Q2 and FY2026Q2 I-130 files carry stale "As of" titles while their numbers are new. Each case is published as a `source-title-mismatch` check.
- **State Dept duplicate rows** are kept as `"<class> (row N)"` and flagged by the `state-duplicate-rows` check, rather than failing the build or using a per-cell anomalies file. Twelve cases follow one pattern (Egypt and Jordan, immediate-relative classes, Nov 2023 to Jul 2024), and every row counts toward the grand total.
- **Multi-sheet workbooks.** `read_grids` reads every sheet. The State parser treats extra sheets as continuations (the April 2022 file has a "Table 2"); every other parser still requires exactly one sheet.
- **Category is display-only**, not part of identity: FY2023Q1 misfiles the "Other" forms under "Citizenship".
- **Crawler pagination** stops after 3 consecutive pages with no new links, because page 1 of the listing repeats only featured links.
- **Historical backfill** (accepted on user request: "go back all the years possible"). `wayback.py` recovers deleted files from the Internet Archive's CDX index. It prefers a spreadsheet per period and takes a PDF only to fill a gap. `SourceFile.via` records the capture. Old manifest lines are unchanged (the field is omitted when empty).
- **PDF reader (`_pdf.py`).** Tables are rebuilt from word positions: columns from header words, right-edge matching, digit fragments joined, offset value lines merged. It feeds the same Grid the spreadsheet parsers use, so the quarterly parser changed only to accept 1 to 5 column blocks and multiple pages. Cross-validation against neighbouring reports is a test.
- **Grid cache keyed by reader source.** Changing `_cells.py` or `_pdf.py` invalidates cached grids. A stale cache once hid a reader fix.
- **Parser warnings carry their own check id** (`older-report-not-used`, `unlabeled-numbers`, `source-title-mismatch`).
- **Deferred:** the net backlog/frontlog dataset (v1.1), and the monthly "receipts and approvals" report of FY2012Q3–FY2013Q3.

## Open questions

- What does a dash mean before FY2023, when the table key wasn't printed?
- Can uscis.gov start refusing GitHub Actions IPs? The fallback is `usdata add` with the same flow.
