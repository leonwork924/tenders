"""Switzerland - simap.ch, the national public procurement platform.

Read API is public and unauthenticated (no key needed), but requires an
initial request to pick up a session cookie before the search endpoint will
respond - the shared requests.Session() on Source handles that automatically
as long as we hit the homepage once first.

Source: https://www.simap.ch/api (read API v1.5.1)

NOTE ON CONFIDENCE: unlike the other adapters in this file, this one is built
from a detailed third-party reverse-engineering write-up (live-probed against
the real API on 2026-07-26: https://pypi.org/project/swiss-procurement-mcp/)
rather than an official parameter reference - simap.ch's own OpenAPI spec
sits behind the same cookie check this adapter works around, and it wasn't
fetchable to cross-check directly. The endpoint, session-cookie requirement,
and mandatory `lang` param are confirmed by that live probe; the exact set of
`newestPubTypes` values is not, so this deliberately does NOT filter by
publication type (it fetches all recent publications, tenders and awards
alike, and leaves the keyword/CPV scorer to do the filtering - consistent
with how this pipeline treats every other broad source). Run it once and
check the console output before trusting it fully; see corporate/README.md
in this repo for the same pattern applied to another source.
"""

from __future__ import annotations

import logging

from ..models import Tender
from ..normalize import parse_date
from .base import Source, SourceError

log = logging.getLogger(__name__)

BASE_URL = "https://www.simap.ch"
SEARCH_PATH = "/api/publications/v2/project/project-search"


class SimapSource(Source):
    def fetch(self) -> list[Tender]:
        # Prime the session cookie - the API rejects requests without one.
        self.get(BASE_URL + "/")

        lang = self.settings.get("lang", "en")
        page_size = int(self.settings.get("page_size", 50))
        max_pages = int(self.settings.get("max_pages", 20))

        tenders: list[Tender] = []
        for page in range(max_pages):
            params = {"lang": lang, "page": page, "size": page_size}
            try:
                resp = self.get(BASE_URL + SEARCH_PATH, params=params)
            except Exception as exc:
                if page == 0:
                    raise SourceError(f"simap: search request failed - {exc}") from exc
                break  # tolerate a late page failing rather than losing everything fetched so far

            data = resp.json()
            rows = data.get("content") or data.get("results") or data.get("items") or []
            if isinstance(data, list):
                rows = data
            if not rows:
                break

            for row in rows:
                tender = self._to_tender(row)
                if tender:
                    tenders.append(tender)

            if len(rows) < page_size:
                break

        log.info("simap.ch: %s publications", len(tenders))
        return tenders

    def _to_tender(self, row: dict) -> Tender | None:
        project_id = row.get("id") or row.get("projectId") or row.get("publicationId")
        title = row.get("title") or row.get("projectTitle") or ""
        if not project_id or not title:
            return None

        orgs = row.get("issuedByOrganizations") or []
        buyer = row.get("organisationName") or (orgs[0].get("name", "") if orgs else "")
        canton = row.get("canton") or ""

        return Tender(
            source=self.name,
            source_id=str(project_id),
            title=title,
            url=f"{BASE_URL}/en/project/{project_id}",
            buyer=buyer or "",
            country="CH",
            description=f"Canton: {canton}" if canton else "",
            published=parse_date(row.get("publicationDate") or row.get("date")),
            deadline=parse_date(row.get("submissionDeadline") or row.get("deadline")),
            currency="CHF",
            language=self.settings.get("lang", "en"),
        )
