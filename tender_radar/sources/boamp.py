from __future__ import annotations

import logging
from datetime import date, timedelta

from ..models import Tender
from ..active import has_future_deadline
from ..normalize import clean_html, parse_date, parse_value
from .base import Source, SourceError

log = logging.getLogger(__name__)


def _pick(row, *names):
    for name in names:
        v = row.get(name) if isinstance(row, dict) else None
        if v not in (None, "", []):
            return v
    return ""


def _text(v):
    if isinstance(v, dict):
        return " ".join(_text(x) for x in v.values())
    if isinstance(v, list):
        return " ".join(_text(x) for x in v)
    return str(v or "")


class BoampSource(Source):
    """Official French BOAMP OpenDataSoft API; no API key required."""

    def fetch(self) -> list[Tender]:
        base = self.settings.get(
            "base_url",
            "https://boamp-datadila.opendatasoft.com/api/explore/v2.0/catalog/datasets/boamp/records",
        )
        limit = min(int(self.settings.get("page_size", 100)), 100)
        max_pages = int(self.settings.get("max_pages", 10))
        keyword = (self.settings.get("query") or "").strip()
        tenders: list[Tender] = []

        for page in range(max_pages):
            params = {"limit": limit, "offset": page * limit}
            # Keep the API query configurable because BOAMP's public schema can evolve.
            if keyword:
                params["q"] = keyword
            data = self.get(base, params=params).json()
            rows = data.get("results") or data.get("records") or []
            if not isinstance(rows, list):
                raise SourceError("boamp: unexpected API response")

            for item in rows:
                row = item.get("record", {}).get("fields", item) if isinstance(item, dict) else {}
                title = clean_html(_text(_pick(row, "objet", "objet_marche", "titre", "title", "description")))
                ref = _text(_pick(row, "idweb", "id", "reference", "numero_avis", "numero"))
                if not title or not ref:
                    continue
                published = parse_date(_pick(row, "dateparution", "date_publication", "publication_date", "date"))
                if published and published < self.since():
                    continue
                deadline = parse_date(_pick(row, "datelimitereponse", "date_limite_reponse", "deadline", "date_limite"))
                buyer = clean_html(_text(_pick(row, "organisme", "nom_organisme", "acheteur", "buyer")))
                description = clean_html(_text(_pick(row, "description", "descriptif", "texte", "objet")))[:8000]
                cpv = _text(_pick(row, "cpv", "code_cpv", "classification_cpv"))
                value = parse_value(_pick(row, "montant", "valeur", "estimated_value", "montant_ht"))
                url = _text(_pick(row, "url", "url_avis", "url_notice", "lien"))
                if not url and ref:
                    url = f"https://www.boamp.fr/avis/detail/{ref}"
                tenders.append(Tender(
                    source=self.name, source_id=ref, title=title, url=url,
                    buyer=buyer, country="FR", description=description, cpv=cpv,
                    published=published, deadline=deadline, value=value, currency="EUR",
                    raw_ref=ref,
                ))
            log.info("BOAMP page %s: %s records", page + 1, len(rows))
            if len(rows) < limit:
                break
        return tenders
