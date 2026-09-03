"""OCDS adapters.

Two portals, one data standard:

  * UK Find a Tender Service (FTS) — free, no key, Open Government Licence.
    GET /api/1.0/ocdsReleasePackages?updatedFrom=...&updatedTo=...&stages=tender
  * South Africa eTenders (National Treasury) — free OCDS API.
    GET /api/OCDSReleases?PageNumber=1&PageSize=50&dateFrom=...&dateTo=...

Both return OCDS release packages, so the release-to-Tender mapping is shared.
If the ZA endpoint moves, change base_url in config.yaml; nothing else changes.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from ..models import Tender
from ..normalize import clean_html, parse_date, parse_value
from .base import Source

log = logging.getLogger(__name__)


def release_to_tender(release: dict, source: str, default_country: str = "",
                       default_currency: str = "") -> Tender | None:
    tender = release.get("tender") or {}
    title = clean_html(tender.get("title") or release.get("title") or "")
    if not title:
        return None

    ocid = release.get("ocid") or release.get("id") or tender.get("id")
    if not ocid:
        return None

    buyer = (release.get("buyer") or {}).get("name", "")
    parties = release.get("parties") or []
    if not buyer and parties:
        for p in parties:
            roles = [r.lower() for r in (p.get("roles") or [])]
            if "buyer" in roles or "procuringentity" in roles:
                buyer = p.get("name", "")
                break

    country = default_country
    for p in parties:
        addr = p.get("address") or {}
        if addr.get("countryName") or addr.get("country"):
            country = (addr.get("country") or addr.get("countryName") or "")[:3].upper()
            break

    period = tender.get("tenderPeriod") or {}
    deadline = parse_date(period.get("endDate"))

    # Estimated contract duration, when the buyer states it upfront (a
    # standard OCDS field, not something every publisher fills in). Powers
    # the "contract ends soon, expect a re-tender" alert on the site.
    contract_period = tender.get("contractPeriod") or {}
    contract_end = parse_date(contract_period.get("endDate"))
    if not contract_end and contract_period.get("startDate") and contract_period.get("durationInDays"):
        try:
            start = parse_date(contract_period["startDate"])
            if start:
                contract_end = start + timedelta(days=int(contract_period["durationInDays"]))
        except (TypeError, ValueError):
            pass

    value_block = tender.get("value") or tender.get("minValue") or {}
    value = parse_value(value_block.get("amount"))
    currency = value_block.get("currency") or ""
    # Many national OCDS publishers report an amount without a currency
    # code (their own currency is implied). Never invent a currency when
    # there is no amount to attach it to; only fill the gap when we
    # actually have a number and the publisher left the field blank.
    if value is not None and not currency and default_currency:
        currency = default_currency

    cpv = ""
    classification = tender.get("classification") or {}
    if str(classification.get("scheme", "")).upper().startswith("CPV"):
        cpv = str(classification.get("id", ""))
    extra = [str(i.get("id", "")) for i in (tender.get("additionalClassifications") or [])
             if str(i.get("scheme", "")).upper().startswith("CPV")]
    cpv = " ".join([c for c in [cpv] + extra if c])

    url = ""
    for doc in tender.get("documents") or []:
        if doc.get("url"):
            url = doc["url"]
            break
    if not url:
        url = (release.get("uri") or tender.get("url")
               or (release.get("buyer") or {}).get("uri") or "")

    return Tender(
        source=source,
        source_id=str(ocid),
        title=title,
        url=url,
        buyer=clean_html(buyer),
        country=country,
        description=clean_html(tender.get("description", ""))[:8000],
        cpv=cpv,
        published=parse_date(release.get("date")),
        deadline=deadline,
        contract_end=contract_end,
        value=value,
        currency=currency,
        raw_ref=str(tender.get("id") or ""),
    )


class UkFtsSource(Source):
    """UK Find a Tender Service. Cursor-paginated."""

    def fetch(self) -> list[Tender]:
        base = self.settings.get(
            "base_url", "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages")
        limit = int(self.settings.get("limit", 100))
        max_pages = int(self.settings.get("max_pages", 20))

        params = {
            "updatedFrom": f"{self.since().isoformat()}T00:00:00",
            "updatedTo": f"{(date.today() + timedelta(days=1)).isoformat()}T00:00:00",
            "limit": limit,
            "stages": "tender",
        }

        tenders: list[Tender] = []
        cursor = None
        for page in range(max_pages):
            if cursor:
                params["cursor"] = cursor
            data = self.get(base, params=params).json()
            releases = data.get("releases") or []
            for r in releases:
                t = release_to_tender(r, "uk_fts", default_country="GB", default_currency="GBP")
                if t:
                    if not t.url:
                        t.url = ("https://www.find-tender.service.gov.uk/Notice/"
                                 f"{t.raw_ref or t.source_id}")
                    tenders.append(t)
            log.info("FTS page %s: %s releases", page + 1, len(releases))

            next_link = (data.get("links") or {}).get("next")
            if not releases or not next_link:
                break
            cursor = next_link.split("cursor=")[-1].split("&")[0] if "cursor=" in next_link else None
            if not cursor:
                break
        return tenders


class ZaEtendersSource(Source):
    """South Africa National Treasury eTenders OCDS API. Page-numbered."""

    def fetch(self) -> list[Tender]:
        base = self.settings.get("base_url", "https://ocds-api.etenders.gov.za/api/OCDSReleases")
        page_size = int(self.settings.get("page_size", 50))
        max_pages = int(self.settings.get("max_pages", 20))

        tenders: list[Tender] = []
        for page in range(1, max_pages + 1):
            params = {
                "PageNumber": page,
                "PageSize": page_size,
                "dateFrom": self.since().isoformat(),
                "dateTo": date.today().isoformat(),
            }
            data = self.get(base, params=params).json()
            releases = data.get("releases") or data.get("Releases") or []
            for r in releases:
                t = release_to_tender(r, "za_etenders", default_country="ZA", default_currency="ZAR")
                if t:
                    if not t.url:
                        t.url = "https://www.etenders.gov.za/Home/opportunities?id=1"
                    tenders.append(t)
            log.info("ZA eTenders page %s: %s releases", page, len(releases))
            if len(releases) < page_size:
                break
        return tenders
