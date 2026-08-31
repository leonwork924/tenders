"""UNGM procurement notices API adapter.

UNGM's public notice pages are freely viewable, but the official Notice API
requires an authenticated user access token. This adapter deliberately does
not implement browser scraping. Put a valid access token in the environment
variable named by `access_token_env` and enable the source in config.yaml.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

from ..models import Tender
from ..active import has_future_deadline
from ..normalize import clean_html, parse_date
from .base import Source, SourceError

log = logging.getLogger(__name__)

ENDPOINT = "https://www.ungm.org/API/Notices"


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_text(v) for v in value.values())
    return str(value)


class UngmSource(Source):
    """Fetch UNGM notices using the official authenticated Notice API."""

    def fetch(self) -> list[Tender]:
        env_name = self.settings.get("access_token_env", "UNGM_ACCESS_TOKEN")
        token = os.environ.get(env_name, "").strip()
        if not token:
            raise SourceError(
                f"ungm: environment variable {env_name} is not set; "
                "create an UNGM API client/user token or disable the source"
            )

        page_size = int(self.settings.get("page_size", 100))
        max_pages = int(self.settings.get("max_pages", 10))
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        tenders: list[Tender] = []
        for page in range(max_pages):
            params = {
                "$top": page_size,
                "$skip": page * page_size,
                "$orderby": "DatePublished desc",
            }
            # OData filter is optional because UNGM's API permissions can vary
            # by client. We filter the lookback window locally for compatibility.
            data = self.get(ENDPOINT, params=params, headers=headers).json()
            rows = data.get("value") or data.get("Notices") or []
            if not isinstance(rows, list):
                raise SourceError("ungm: unexpected API response")

            kept = 0
            for row in rows:
                t = self._to_tender(row)
                if not t:
                    continue
                if t.published and t.published < self.since() and not has_future_deadline(t):
                    # Results are ordered newest first; once this happens we
                    # can stop paging safely.
                    continue
                tenders.append(t)
                kept += 1

            log.info("UNGM page %s: %s notices (%s in lookback)", page + 1, len(rows), kept)
            if not rows or len(rows) < page_size:
                break
            if any(t.published and t.published < self.since() for t in (self._to_tender(r) for r in rows)):
                break

        return tenders

    @staticmethod
    def _to_tender(row: dict) -> Tender | None:
        notice_id = row.get("Id") or row.get("id")
        title = clean_html(_text(row.get("Title") or row.get("title")))
        if not notice_id or not title:
            return None

        countries = row.get("CountryISO3Codes") or row.get("CountryIso3Codes") or []
        country = _text(countries[0] if isinstance(countries, list) and countries else countries)
        reference = _text(row.get("Reference") or row.get("reference"))
        description = clean_html(_text(row.get("Description") or row.get("description")))
        deadline = parse_date(row.get("Deadline") or row.get("deadline"))
        published = parse_date(row.get("DatePublished") or row.get("datePublished"))

        return Tender(
            source="ungm",
            source_id=str(notice_id),
            title=title,
            url=f"https://www.ungm.org/Public/Notice/{notice_id}",
            buyer=clean_html(_text(row.get("OrganizationName") or row.get("AgencyName"))),
            country=country,
            description=description[:8000],
            published=published,
            deadline=deadline,
            raw_ref=reference,
        )
