from __future__ import annotations

import logging
from datetime import date

from ..models import Tender
from ..active import has_future_deadline
from ..normalize import clean_html, parse_date, parse_value
from .base import Source
from .ocds import release_to_tender

log = logging.getLogger(__name__)


class AusTenderSource(Source):
    """Australian Government AusTender OCDS API."""
    def fetch(self) -> list[Tender]:
        base = self.settings.get("base_url", "https://api.tenders.gov.au/ocds/api")
        # The public API exposes date-range endpoints; keep URL configurable because
        # the API version/path can change without affecting the adapter.
        path = self.settings.get("published_path", "/releases/published/{start}/{end}")
        url = base.rstrip("/") + path.format(start=self.since().isoformat(), end=date.today().isoformat())
        data = self.get(url).json()
        releases = data.get("releases") or []
        tenders = []
        for r in releases:
            t = release_to_tender(r, self.name, default_country="AU")
            if t:
                tenders.append(t)
        log.info("AusTender: %s releases", len(releases))
        return tenders


class SecopColombiaSource(Source):
    """SECOP Colombia OCDS JSON API. Endpoint is configurable."""
    def fetch(self) -> list[Tender]:
        base = self.settings.get("base_url", "https://api.colombiacompra.gov.co")
        url = base.rstrip("/") + self.settings.get("path", "/releases/")
        params = {
            "start": self.since().isoformat(),
            "finish": date.today().isoformat(),
            "status": self.settings.get("status", "tender"),
        }
        # Optional title/UNSPSC filters can be supplied in config later.
        for key in ("title", "items", "procurement_type"):
            if self.settings.get(key):
                params[key] = self.settings[key]
        data = self.get(url, params=params).json()
        releases = data.get("releases") or data.get("data") or []
        tenders = []
        for r in releases:
            if isinstance(r, dict) and "tender" in r:
                t = release_to_tender(r, self.name, default_country="CO")
                if t:
                    tenders.append(t)
        log.info("SECOP Colombia: %s releases", len(releases))
        return tenders


class AnacItalySource(Source):
    """ANAC Italy OCDS monthly JSON feed.

    ANAC publishes monthly OCDS JSON resources. The URL pattern is configurable
    so a change in the dataset path does not require code changes.
    """
    def fetch(self) -> list[Tender]:
        template = self.settings.get(
            "monthly_url",
            "https://dati.anticorruzione.it/opendata/download/dataset/ocds-appalti-ordinari-{year}/filesystem/bulk/{year}/{month:02d}.json",
        )
        today = date.today()
        urls = []
        # Current month first, then previous lookback months.
        for offset in range(max(1, int(self.settings.get("months_back", 2)))):
            m = today.month - offset
            y = today.year
            while m <= 0:
                y -= 1
                m += 12
            urls.append(template.format(year=y, month=m))

        tenders = []
        for url in urls:
            try:
                data = self.get(url).json()
            except Exception as exc:
                log.warning("ANAC feed unavailable %s: %s", url, exc)
                continue
            releases = data.get("releases") if isinstance(data, dict) else data
            if not isinstance(releases, list):
                continue
            for r in releases:
                if isinstance(r, dict):
                    t = release_to_tender(r, self.name, default_country="IT")
                    if t:
                        tenders.append(t)
            log.info("ANAC: %s releases from %s", len(releases), url)
        return tenders


class ProzorroSource(Source):
    """Ukraine Prozorro public API, requesting OCDS-shaped responses."""
    def fetch(self) -> list[Tender]:
        base = self.settings.get("base_url", "https://public-api.prozorro.gov.ua/api/2.5/tenders")
        max_pages = int(self.settings.get("max_pages", 5))
        offset = None
        tenders: list[Tender] = []

        for page in range(max_pages):
            params = {"opt_schema": "ocds"}
            if offset:
                params["offset"] = offset
            data = self.get(base, params=params).json()
            rows = data.get("data") or []
            for row in rows:
                record = row.get("data") if isinstance(row, dict) and isinstance(row.get("data"), dict) else row
                # Depending on API version, an OCDS response is either a record
                # with releases or a single release.
                release = None
                if isinstance(record, dict) and isinstance(record.get("releases"), list):
                    candidates = [r for r in record["releases"] if isinstance(r, dict) and r.get("tender")]
                    if candidates:
                        release = sorted(candidates, key=lambda r: str(r.get("date") or ""))[-1]
                elif isinstance(record, dict) and record.get("tender"):
                    release = record
                if not release:
                    continue
                t = release_to_tender(release, self.name, default_country="UA")
                if t:
                    tid = release.get("ocid") or release.get("id") or t.source_id
                    t.source_id = str(tid)
                    t.raw_ref = str((release.get("tender") or {}).get("id") or t.raw_ref)
                    tender_id = t.raw_ref or t.source_id
                    t.url = f"https://prozorro.gov.ua/tender/{tender_id}"
                    if t.published and t.published < self.since() and not has_future_deadline(t):
                        continue
                    tenders.append(t)
            log.info("Prozorro page %s: %s tender ids", page + 1, len(rows))
            next_page = data.get("next_page") or {}
            next_uri = next_page.get("uri") if isinstance(next_page, dict) else None
            if next_uri:
                # API supplies a complete next URI; keep it for the following request.
                base = next_uri
                offset = None
            else:
                offset = next_page.get("offset") if isinstance(next_page, dict) else None
            if not rows or (not next_uri and not offset):
                break
        return tenders

