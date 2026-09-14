"""SAM.gov - U.S. federal contract opportunities.

Official docs: https://open.gsa.gov/api/get-opportunities-public-api/
Get a free public API key from your SAM.gov account (Account Details page,
enter your password to reveal the key) and set it as the SAM_GOV_API_KEY
GitHub secret.
"""

from __future__ import annotations

import logging
import os
from datetime import date

from ..models import Tender
from ..normalize import parse_date
from .base import Source, SourceError

log = logging.getLogger(__name__)

# Open/actionable notice types only - deliberately excludes 'a' (Award
# Notice, already decided) and the retired/DoD-specific codes. See
# https://open.gsa.gov/api/get-opportunities-public-api/#get-opportunities-request-parameters
#   p = pre-solicitation, o = solicitation, k = combined synopsis/solicitation,
#   r = sources sought, s = special notice
DEFAULT_PTYPES = "p,o,k,r,s"


class SamGovSource(Source):
    """Get Opportunities API v2 (https://api.sam.gov/opportunities/v2/search)."""

    BASE_URL = "https://api.sam.gov/opportunities/v2/search"

    def fetch(self) -> list[Tender]:
        api_key = os.getenv(self.settings.get("api_key_env", "SAM_GOV_API_KEY"), "").strip()
        if not api_key:
            raise SourceError(
                "sam_gov: SAM_GOV_API_KEY missing. Get a free key from your SAM.gov "
                "account's Account Details page and set it as a GitHub secret."
            )

        since = self.since()
        today = date.today()
        limit = min(int(self.settings.get("page_size", 1000)), 1000)
        max_pages = int(self.settings.get("max_pages", 5))

        params = {
            "api_key": api_key,
            "postedFrom": since.strftime("%m/%d/%Y"),
            "postedTo": today.strftime("%m/%d/%Y"),
            "ptype": self.settings.get("ptype", DEFAULT_PTYPES),
            "limit": limit,
            "offset": 0,
        }

        tenders: list[Tender] = []
        for page in range(max_pages):
            params["offset"] = page * limit
            resp = self.get(self.BASE_URL, params=params)
            data = resp.json()
            rows = data.get("opportunitiesData") or []
            if not rows:
                break
            for row in rows:
                tender = self._to_tender(row)
                if tender:
                    tenders.append(tender)
            if len(rows) < limit:
                break  # last page reached
        log.info("SAM.gov: %s notices", len(tenders))
        return tenders

    def _to_tender(self, row: dict) -> Tender | None:
        notice_id = row.get("noticeId")
        title = row.get("title") or ""
        if not notice_id or not title:
            return None

        pop = row.get("placeOfPerformance") or {}
        state_code = ((pop.get("state") or {}).get("code")) or ""
        country_code = ((pop.get("country") or {}).get("code")) or "US"

        buyer = row.get("fullParentPathName") or row.get("department") or "SAM.gov"
        naics = row.get("naicsCode") or ""
        detail_bits = [f"NAICS {naics}" if naics else "", f"Place of performance: {state_code}" if state_code else ""]

        return Tender(
            source=self.name,
            source_id=str(notice_id),
            title=title,
            url=row.get("uiLink") or f"https://sam.gov/opp/{notice_id}/view",
            buyer=buyer,
            country=country_code,
            description=" · ".join(b for b in detail_bits if b),
            published=parse_date(row.get("postedDate")),
            deadline=parse_date(row.get("responseDeadLine")),
            currency="USD",
            raw_ref=row.get("solicitationNumber") or "",
            language="en",
        )
