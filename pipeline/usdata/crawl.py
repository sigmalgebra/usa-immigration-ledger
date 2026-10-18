"""Polite crawler for the open USCIS data listing. Never touches travel.state.gov or egov.uscis.gov.

Stops on any 401/403/429 instead of escalating: those are access-control signals.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import requests

from . import archive
from .datasets import DATASETS

LISTING = "https://www.uscis.gov/tools/reports-and-studies/immigration-and-citizenship-data"
USER_AGENT = "usa-immigration-ledger/1.0 (+https://github.com/sigmalgebra/usa-immigration-ledger)"
ALLOWED_HOSTS = {"www.uscis.gov"}
_LINK = re.compile(r'href="([^"]+\.(?:xlsx|csv))"', re.I)


class AccessDenied(RuntimeError):
    pass


@dataclass
class CrawlReport:
    added: list[str] = field(default_factory=list)
    skipped_known: int = 0
    unclassified: list[str] = field(default_factory=list)


def _get(session: requests.Session, url: str) -> requests.Response:
    if urlparse(url).hostname not in ALLOWED_HOSTS:
        raise ValueError(f"refusing to fetch outside {ALLOWED_HOSTS}: {url}")
    resp = session.get(url, timeout=60, allow_redirects=False)
    if resp.status_code in (401, 403, 429):
        raise AccessDenied(f"{resp.status_code} from {url}; stopping (do not evade access controls)")
    if resp.is_redirect:
        return _get(session, urljoin(url, resp.headers["Location"]))
    resp.raise_for_status()
    return resp


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
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
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
