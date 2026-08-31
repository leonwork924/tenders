from __future__ import annotations

import logging
import os

from ..models import Tender
from ..active import has_future_deadline
from ..normalize import clean_html, parse_date, parse_value
from .base import Source, SourceError

log = logging.getLogger(__name__)


def _pick(d, *keys):
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if v not in (None, "", []):
            return v
    return ""


class KonepsSource(Source):
    """KONEPS/NaraJangteo Open API adapter. Requires a data.go.kr service key."""

    def fetch(self) -> list[Tender]:
        key = os.getenv(self.settings.get("api_key_env", "KONEPS_SERVICE_KEY"), "").strip()
        if not key:
            raise SourceError("koneps: service key missing; set KONEPS_SERVICE_KEY")
        base = self.settings.get("base_url", "https://apis.data.go.kr/1230000/ad/BidPublicInfoService")
        operation = self.settings.get("operation", "getBidPblancListInfoServc")
        url = base.rstrip("/") + "/" + operation
        params = {
            "serviceKey": key, "pageNo": 1, "numOfRows": int(self.settings.get("page_size", 100)),
            "type": "json",
        }
        data = self.get(url, params=params).json()
        body = ((data.get("response") or {}).get("body") or {}) if isinstance(data, dict) else {}
        items = ((body.get("items") or {}).get("item") if isinstance(body.get("items"), dict) else body.get("items")) or []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise SourceError("koneps: unexpected API response")
        tenders: list[Tender] = []
        for row in items:
            ref = str(_pick(row, "bidNtceNo", "bidNtceNoInfo", "bidNtceNoEn") or "")
            title = clean_html(str(_pick(row, "bidNtceNm", "bidNtceNmEn", "bidNtceNmKr") or ""))
            if not ref or not title:
                continue
            published = parse_date(_pick(row, "bidNtceDt", "bidNtceDate"))
            deadline = parse_date(_pick(row, "bidClseDt", "bidClseDate"))
            if published and published < self.since():
                continue
            tenders.append(Tender(
                source=self.name, source_id=ref, title=title,
                url=str(_pick(row, "bidNtceUrl", "bidNtceDtlUrl") or "https://www.g2b.go.kr/"),
                buyer=clean_html(str(_pick(row, "dminsttNm", "ntceInsttNm", "orderInsttNm") or "")),
                country="KR", description=clean_html(str(_pick(row, "bidNtceDtlNm", "bidNtceDesc") or ""))[:8000],
                published=published, deadline=deadline,
                value=parse_value(_pick(row, "presmptPrce", "asignBdgtAmt", "totPrdprc")), currency="KRW", raw_ref=ref,
            ))
        log.info("KONEPS: %s notices", len(tenders))
        return tenders
