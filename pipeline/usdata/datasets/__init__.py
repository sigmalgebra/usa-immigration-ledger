"""The one registry of dataset families. Crawl, build, publish and the site's nav all derive from it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..model import DatasetId, ParsedFile, SourceFile
from . import i130_awaiting, qaf, state_iv, visa_bulletin


@dataclass(frozen=True)
class DatasetSpec:
    id: DatasetId
    publisher: str
    title: str
    listing: str
    matches: Callable[[str], bool]
    parse: Callable[[SourceFile, bytes], ParsedFile]
    archived_period: Callable[[str], str | None]  # file name -> period key, for choosing Wayback captures


_USCIS_LISTING = "https://www.uscis.gov/tools/reports-and-studies/immigration-and-citizenship-data"

DATASETS: dict[str, DatasetSpec] = {
    s.id: s for s in (
        DatasetSpec(qaf.DATASET, "USCIS", qaf.TITLE, _USCIS_LISTING, qaf.matches, qaf.parse, qaf.archived_period),
        DatasetSpec(i130_awaiting.DATASET, "USCIS", i130_awaiting.TITLE, _USCIS_LISTING, i130_awaiting.matches,
                    i130_awaiting.parse, i130_awaiting.archived_period),
        DatasetSpec(state_iv.DATASET, "U.S. Department of State", state_iv.TITLE, state_iv.LISTING,
                    state_iv.matches, state_iv.parse, state_iv.archived_period),
        DatasetSpec(visa_bulletin.DATASET, "U.S. Department of State", visa_bulletin.TITLE, visa_bulletin.LISTING,
                    visa_bulletin.matches, visa_bulletin.parse, visa_bulletin.archived_period),
    )
}
