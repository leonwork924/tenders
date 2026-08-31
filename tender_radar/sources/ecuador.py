from __future__ import annotations

import logging
from datetime import date

from ..models import Tender
from ..active import has_future_deadline
from ..normalize import clean_html, parse_date, parse_value
from .base import Source
from .ocds import release_to_tender

log = logging.getLogger(__name__)


class EcuadorOcdsSource(Source):
    """Ecuador SERCOP public OCDS search API; no authentication."""

    API = "https://datosabiertos.compraspublicas.gob.ec/PLATAFORMA/api/search_ocds"
    RECORD_API = "https://datosabiertos.compraspublicas.gob.ec/PLATAFORMA/api/record"

    def fetch(self) -> list[Tender]:
        year = int(self.settings.get("year") or date.today().year)
        terms = self.settings.get("search_terms") or ["archivo", "digitalizacion"]
        max_pages = int(self.settings.get("max_pages", 3))
        max_records = int(self.settings.get("max_records", 200))
        tenders: list[Tender] = []
        seen: set[str] = set()

        for term in terms:
            term = str(term).strip()
            if len(term) < 3:
                continue
            for page in range(1, max_pages + 1):
                data = self.get(self.API, params={"year": year, "search": term, "page": page}).json()
                rows = data.get("data") or data.get("results") or data.get("releases") or []
                if not isinstance(rows, list) or not rows:
                    break

                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    ocid = row.get("ocid") or row.get("id")
                    if not ocid or str(ocid) in seen:
                        continue
                    seen.add(str(ocid))
                    try:
                        payload = self.get(self.RECORD_API, params={"ocid": ocid}).json()
                    except Exception as exc:
                        log.warning("Ecuador OCDS %s unavailable: %s", ocid, exc)
                        continue
                    records = payload.get("records") if isinstance(payload, dict) else None
                    if not isinstance(records, list):
                        continue
                    releases = []
                    for rec in records:
                        releases.extend(rec.get("releases") or [])
                    candidates = [r for r in releases if isinstance(r, dict) and r.get("tender")]
                    if not candidates:
                        continue
                    release = sorted(candidates, key=lambda r: str(r.get("date") or ""))[-1]
                    published = parse_date(release.get("date"))
                    if published and published < self.since():
                        continue
                    t = release_to_tender(release, self.name, default_country="EC")
                    if t:
                        t.url = f"https://www.compraspublicas.gob.ec/ProcesoContratacion/compras/PC/buscarProcesoCompra.cpe?sg=1"
                        tenders.append(t)
                        if len(tenders) >= max_records:
                            return tenders

                log.info("Ecuador OCDS term '%s' page %s: %s rows", term, page, len(rows))
                if len(rows) < 1 or page >= int(data.get("pages") or page):
                    break
        return tenders
