"""Polite fetchers. Never touches egov.uscis.gov or travel.state.gov's HTML pages.

`crawl` follows the open USCIS data listing. `fetch_state` asks State's file store (travel.state.gov/content/dam/)
for the next Visa Bulletin PDFs and monthly issuance spreadsheets by their published file names. State refuses
GitHub's runners, so `fetch_state` runs on a maintainer's machine (scripts/fetch-state.ps1).

Both identify themselves and stop on any 401/403/429 instead of escalating: those are access-control signals.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import unquote, urljoin, urlparse

import requests

from . import archive
from .datasets import DATASETS, state_iv, visa_bulletin
from .model import Month

LISTING = "https://www.uscis.gov/tools/reports-and-studies/immigration-and-citizenship-data"
USER_AGENT = "usa-immigration-ledger/1.0 (+https://github.com/sigmalgebra/usa-immigration-ledger)"
_LINK = re.compile(r'href="([^"]+\.(?:xlsx|csv))"', re.I)


class AccessDenied(RuntimeError):
    pass


class NotPublished(RuntimeError):
    pass


@dataclass
class CrawlReport:
    added: list[str] = field(default_factory=list)
    skipped_known: int = 0
    unclassified: list[str] = field(default_factory=list)


def _allowed(url: str) -> bool:
    u = urlparse(url)
    return u.hostname == "www.uscis.gov" or (u.hostname == "travel.state.gov" and u.path.startswith("/content/dam/"))


def _get(session: requests.Session, url: str) -> requests.Response:
    if not _allowed(url):
        raise ValueError(f"refusing to fetch outside www.uscis.gov and travel.state.gov/content/dam/: {url}")
    resp = session.get(url, timeout=60, allow_redirects=False)
    if resp.status_code in (401, 403, 429):
        raise AccessDenied(f"{resp.status_code} from {url}; stopping (do not evade access controls)")
    if resp.status_code == 404:
        raise NotPublished(url)
    if resp.is_redirect:
        return _get(session, urljoin(url, resp.headers["Location"]))
    resp.raise_for_status()
    return resp


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def listing_links(session: requests.Session, max_pages: int = 100) -> list[str]:
    # Every page repeats a "featured" block, so one page with nothing new (page 1 today) is not the end.
    seen: list[str] = []
    empty_streak = 0
    for page in range(max_pages):
        html = _get(session, f"{LISTING}?page={page}").text
        links = [urljoin(LISTING, h.replace("&amp;", "&")) for h in _LINK.findall(html)]
        new = [u for u in dict.fromkeys(links) if u not in seen]
        empty_streak = 0 if new else empty_streak + 1
        if empty_streak == 3:
            break
        seen.extend(new)
        time.sleep(1)
    return seen


def crawl(max_pages: int = 100) -> CrawlReport:
    session = _session()
    known = {f.url for f in archive.load_manifest()}
    report = CrawlReport()
    for url in listing_links(session, max_pages):
        spec = next((d for d in DATASETS.values() if d.matches(url)), None)
        if spec is None:
            report.unclassified.append(url)
            continue
        if url in known:
            report.skipped_known += 1
            continue
        data = _get(session, url).content
        f, created = archive.add(data, dataset=spec.id, url=url, original_name=url.rsplit("/", 1)[-1],
                                 retrieved_by="crawler")
        if created:
            report.added.append(f.archive_path)
        time.sleep(1)
    return report


def _months(after: Month, through: Month) -> list[Month]:
    out, m = [], after
    while True:
        m = Month(m.year + m.month // 12, m.month % 12 + 1)
        if m > through:
            return out
        out.append(m)


def latest_month(dataset: str) -> Month:
    month_of = {visa_bulletin.DATASET: visa_bulletin.bulletin_month, state_iv.DATASET: state_iv.month_from_name}
    return max(m for f in archive.load_manifest() if f.dataset == dataset
               and (m := month_of[dataset](unquote(f.original_name))) is not None)


def state_candidates(today: date) -> list[tuple[str, str]]:
    """(dataset, url) for every month after the newest archived one that State may have published by `today`.

    A bulletin is published around the middle of the month before it takes effect, so next month's is tried too.
    """
    ahead = today + timedelta(days=31)
    return ([(visa_bulletin.DATASET, visa_bulletin.PDF_STORE + visa_bulletin.pdf_name(m))
             for m in _months(latest_month(visa_bulletin.DATASET), Month(ahead.year, ahead.month))]
            + [(state_iv.DATASET, state_iv.file_url(m))
               for m in _months(latest_month(state_iv.DATASET), Month(today.year, today.month))])


def fetch_state(today: date) -> list[str]:
    session = _session()
    added = []
    for dataset, url in state_candidates(today):
        try:
            data = _get(session, url).content
        except NotPublished:
            continue
        finally:
            time.sleep(1)
        f, created = archive.add(data, dataset=dataset, url=url, original_name=unquote(url.rsplit("/", 1)[-1]),
                                 retrieved_by="crawler")
        if created:
            added.append(f.archive_path)
    return added


def stale_bulletin(today: date) -> str | None:
    """Why the archive is behind, or None. The bulletin for a month is out well before the month starts."""
    due = today - timedelta(days=10)
    latest = latest_month(visa_bulletin.DATASET)
    if latest < Month(due.year, due.month):
        return (f"newest Visa Bulletin archived is {latest}, but the {Month(due.year, due.month)} bulletin is out; "
                f"the State Dept fetch (scripts/fetch-state.ps1) has stopped, or a data/state pull request "
                f"is waiting for a fix")
    return None
