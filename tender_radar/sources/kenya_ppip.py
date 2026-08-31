from __future__ import annotations

import logging
from datetime import date

from ..models import Tender
from ..active import has_future_deadline
from ..normalize import clean_html, parse_date, parse_value
from .base import Source, SourceError
from .ocds import release_to_tender

log = logging.getLogger(__name__)


def _as_releases(data):
    if isinstance(data, dict):
        for key in ("releases", "data", "results", "records"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return data if isinstance(data, list) else []


class KenyaPpipSource(Source):
    """Kenya PPIP OCDS API. Public machine-readable data; no login required."""

    def fetch(self) -> list[Tender]:
        base = self.settings.get("base_url", "https://tenders.go.ke/api/ocds/tenders")
        fy = self.settings.get("financial_year", "")
        max_pages = int(self.settings.get("max_pages", 10))
        tenders: list[Tender] = []

        for page in range(1, max_pages + 1):
            params = {"page": page}
            if fy:
                params["fy"] = fy
            data = self.get(base, params=params).json()
            rows = _as_releases(data)
            if not rows:
                break
            for row in rows:
                if isinstance(row, dict) and "tender" in row:
                    t = release_to_tender(row, self.name, default_country="KE")
                    if t:
                        if t.published and t.published < self.since() and not has_future_deadline(t):
                            continue
                        if not t.url:
                            t.url = f"https://tenders.go.ke/tenders/{t.raw_ref or t.source_id}"
                        tenders.append(t)
                    continue
                # Some PPIP responses are flattened rather than OCDS releases.
                ref = str(row.get("tender_no") or row.get("tenderNumber") or row.get("reference") or row.get("id") or "")
                title = clean_html(str(row.get("title") or row.get("description") or row.get("tender_description") or ""))
                if not ref or not title:
                    continue
                published = parse_date(row.get("published_date") or row.get("publishedDate") or row.get("date"))
                if published and published < self.since():
                    continue
                deadline = parse_date(row.get("closing_date") or row.get("closingDate") or row.get("deadline"))
                value = parse_value(row.get("amount") or row.get("value"))
                tenders.append(Tender(
                    source=self.name, source_id=ref, title=title,
                    url=str(row.get("url") or f"https://tenders.go.ke/tenders/{row.get('id', ref)}"),
                    buyer=clean_html(str(row.get("procuring_entity") or row.get("procuringEntity") or row.get("buyer") or "")),
                    country="KE", description=clean_html(str(row.get("description") or ""))[:8000],
                    published=published, deadline=deadline, value=value, currency="KES", raw_ref=ref,
                ))
            log.info("Kenya PPIP page %s: %s records", page, len(rows))
            if len(rows) < int(self.settings.get("page_size", 100)):
                break
        return tenders
