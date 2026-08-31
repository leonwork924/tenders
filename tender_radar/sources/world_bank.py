"""World Bank project procurement notices API adapter.

Uses the World Bank's public procurement-notices search API. No API key is
required. The API exposes current procurement notices for projects financed by
the World Bank, including the notice description and submission deadline.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from ..models import Tender
from ..normalize import clean_html, parse_date
from .base import Source, SourceError

log = logging.getLogger(__name__)

ENDPOINT = "https://search.worldbank.org/api/v2/procnotices"


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(_text(v) for v in value if v is not None)
    if isinstance(value, dict):
        return " ".join(_text(v) for v in value.values())
    return str(value)


class WorldBankSource(Source):
    """Fetch current World Bank-financed procurement opportunities."""

    def fetch(self) -> list[Tender]:
        limit = int(self.settings.get("page_size", 100))
        max_pages = int(self.settings.get("max_pages", 10))
        notice_types = self.settings.get("notice_types") or [
            "Invitation for Bids",
            "Invitation for Prequalification",
            "Request for Expression of Interest",
        ]

        fields = [
            "id", "notice_type", "noticedate", "notice_status",
            "submission_deadline_date", "submission_date",
            "project_ctry_name", "project_ctry_code", "project_id",
            "project_name", "bid_reference_no", "bid_description",
            "contact_organization", "notice_text",
        ]

        tenders: list[Tender] = []
        for page in range(max_pages):
            params = {
                "format": "json",
                "apilang": "en",
                "fl": ",".join(fields),
                "srt": "submission_deadline_date",
                "order": "asc",
                "os": page * limit,
                "rows": limit,
                # Keep active/current opportunities. The endpoint accepts
                # multiple values separated by ^ for this filter.
                "notice_type_exact": "^".join(notice_types),
                "deadline_strdate": date.today().isoformat(),
            }
            data = self.get(ENDPOINT, params=params).json()
            rows = data.get("procnotices") or []
            if not isinstance(rows, list):
                raise SourceError("world_bank: unexpected procnotices response")

            for row in rows:
                t = self._to_tender(row)
                if t:
                    tenders.append(t)

            log.info("World Bank page %s: %s notices", page + 1, len(rows))
            if len(rows) < limit:
                break

        return tenders

    @staticmethod
    def _to_tender(row: dict) -> Tender | None:
        notice_id = _text(row.get("id")).strip()
        if not notice_id:
            return None

        bid_description = clean_html(_text(row.get("bid_description")))
        project_name = clean_html(_text(row.get("project_name")))
        title = bid_description or project_name
        if not title:
            return None
        if project_name and project_name.lower() not in title.lower():
            title = f"{title} — {project_name}"

        description = clean_html(_text(row.get("notice_text")))
        if not description:
            description = " ".join(
                x for x in [bid_description, project_name, _text(row.get("notice_type"))]
                if x
            )

        country = _text(row.get("project_ctry_code")).strip()
        if not country:
            country = _text(row.get("project_ctry_name")).strip()

        return Tender(
            source="world_bank",
            source_id=notice_id,
            title=title,
            url=f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{notice_id}",
            buyer=clean_html(_text(row.get("contact_organization"))),
            country=country,
            description=description[:8000],
            published=parse_date(row.get("noticedate") or row.get("submission_date")),
            deadline=parse_date(row.get("submission_deadline_date")),
            raw_ref=_text(row.get("bid_reference_no")),
        )
