"""Backfill from the Internet Archive's Wayback Machine.

USCIS and the State Department remove old statistics files from their sites. The Wayback Machine kept copies.
We ask its CDX index which files it captured under each dataset's folders, pick the files our parsers know,
and download the byte-exact capture (the `id_` URL form returns the original bytes, not the archive's wrapper).
Each archived file records both the original government URL and the capture it came from (`via`).

This never contacts travel.state.gov or uscis.gov: every request goes to web.archive.org.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import unquote

import requests

from . import archive
from .crawl import USER_AGENT
from .datasets import DATASETS

CDX = "https://web.archive.org/cdx/search/cdx"
# Folders where each dataset's files have lived over the years.
PREFIXES: dict[str, list[str]] = {
    "uscis-forms": [
        "uscis.gov/sites/default/files/document/data/*",
        "uscis.gov/sites/default/files/document/reports/*",
        "uscis.gov/sites/default/files/USCIS/Resources/Reports%20and%20Studies/Immigration%20Forms%20Data/*",
    ],
    "uscis-i130-awaiting": [
        "uscis.gov/sites/default/files/document/data/*",
        "uscis.gov/sites/default/files/document/reports/*",
    ],
    "state-iv": ["travel.state.gov/content/dam/visas/Statistics/Immigrant-Statistics/MonthlyIVIssuances/*"],
}


@dataclass(frozen=True)
class Capture:
    timestamp: str
    original: str

    @property
    def name(self) -> str:
        return unquote(self.original.split("?", 1)[0].rsplit("/", 1)[-1])

    @property
    def raw_url(self) -> str:
        return f"https://web.archive.org/web/{self.timestamp}id_/{self.original}"


@dataclass
class BackfillReport:
    added: list[str] = field(default_factory=list)
    known: int = 0
    failed: list[str] = field(default_factory=list)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def captures(session: requests.Session, dataset: str, have: set[str]) -> list[Capture]:
    """One capture per period we don't already hold: a spreadsheet when one was ever captured, else the PDF.

    PDFs are only taken to fill gaps, because table extraction from PDF is the weakest link in the chain.
    """
    spec = DATASETS[dataset]
    best: dict[str, Capture] = {}

    def rank(c: Capture) -> tuple:
        return (not c.name.lower().endswith(".pdf"), c.timestamp)

    for prefix in PREFIXES[dataset]:
        resp = session.get(CDX, params={"url": prefix, "fl": "timestamp,original", "filter": "statuscode:200"},
                           timeout=300)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            ts, original = line.split(" ", 1)
            c = Capture(ts, original)
            period = spec.archived_period(c.name)
            if period is None or period in have:
                continue
            if period not in best or rank(c) > rank(best[period]):
                best[period] = c
        time.sleep(2)
    return [best[p] for p in sorted(best)]


def backfill(dataset: str) -> BackfillReport:
    session = _session()
    spec = DATASETS[dataset]
    have = {p for f in archive.load_manifest() if f.dataset == dataset
            if (p := spec.archived_period(f.original_name)) is not None}
    report = BackfillReport()
    for c in captures(session, dataset, have):
        for attempt in range(3):
            try:
                resp = session.get(c.raw_url, timeout=120)
                if resp.status_code == 200 and resp.content:
                    f, created = archive.add(resp.content, dataset=dataset, url=c.original, original_name=c.name,
                                             retrieved_by="wayback", via=c.raw_url)
                    if created:
                        report.added.append(f.archive_path)
                    else:
                        report.known += 1
                    break
            except requests.RequestException:
                pass
            time.sleep(5 * (attempt + 1))
        else:
            report.failed.append(c.raw_url)
        time.sleep(1.5)
    return report
