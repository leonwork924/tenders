"""TED — Tenders Electronic Daily (EU), official Search API v3.

Endpoint:  POST https://api.ted.europa.eu/v3/notices/search
No authentication is needed for published notices. There is a fair-usage
policy, so keep page_size at 100 and don't hammer it: one run a day is fine.

Field names come from the eForms search field list:
https://docs.ted.europa.eu/ODS/latest/reuse/field-list.html
"""

from __future__ import annotations

import logging
import re

from ..models import Tender
from ..normalize import clean_html, parse_date, parse_value
from .base import Source, SourceError

log = logging.getLogger(__name__)

ENDPOINT = "https://api.ted.europa.eu/v3/notices/search"

FIELDS = [
    "publication-number",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "description-lot",
    "description-proc",
    "classification-cpv",
    "deadline-receipt-request",
    "deadline-date-lot",
    "publication-date",
    "estimated-value-lot",
    "estimated-value-proc",
    "estimated-value-cur-lot",
    "estimated-value-cur-proc",
    "total-value",
    "total-value-cur",
    # Best-effort: contract/framework duration, following the same lot/proc
    # pairing as the value fields above. NOT verified against a live
    # response from here (network-restricted sandbox) -- if these field
    # names are wrong, TED just won't return them and contract_end stays
    # empty for TED, same as today. Check after the first real fetch.
    "duration-lot",
    "duration-proc",
    "notice-type",
    "links",
]


def _parse_duration_to_end_date(raw, deadline):
    """Best-effort: turn whatever TED returns for duration-lot/-proc into a
    contract end date, counted from the bid deadline. Unverified field
    format -- handles a plain number of months (most likely), an ISO 8601
    duration like 'P36M', or gives up cleanly to None on anything else.
    Never raises: a wrong guess here should just mean no contract_end, not
    a broken fetch.
    """
    if not raw or not deadline:
        return None
    from datetime import timedelta
    import re as _re

    text = str(raw).strip()
    months = None
    m = _re.match(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?$", text, _re.IGNORECASE)
    if m:
        years, mo, days = (int(g) if g else 0 for g in m.groups())
        months = years * 12 + mo
        if days and not months:
            return deadline + timedelta(days=days)
    elif _re.match(r"^\d+(\.\d+)?$", text):
        months = round(float(text))
    if months:
        # Simple month math without a calendar dependency: good enough for
        # a "roughly 6 months out" alert, not a legal deadline.
        total = deadline.month - 1 + months
        year = deadline.year + total // 12
        month = total % 12 + 1
        day = min(deadline.day, 28)
        try:
            from datetime import date as _date
            return _date(year, month, day)
        except ValueError:
            return None
    return None


def _first(value):
    """TED returns most fields as lists, sometimes as {lang: [..]} maps."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("eng", "fra", "en", "fr"):
            if key in value:
                return _first(value[key])
        for v in value.values():
            got = _first(v)
            if got:
                return got
        return ""
    if isinstance(value, (list, tuple)):
        for v in value:
            got = _first(v)
            if got:
                return got
        return ""
    return str(value)


def _join(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_join(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_join(v) for v in value)
    return str(value)


def _extract_currency(value) -> str:
    """Extract an ISO currency code from TED's sometimes nested field shape."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("currency", "currencyID", "currencyId", "code"):
            if key in value:
                got = _extract_currency(value[key])
                if got:
                    return got
        for v in value.values():
            got = _extract_currency(v)
            if got:
                return got
        return ""
    if isinstance(value, (list, tuple)):
        for v in value:
            got = _extract_currency(v)
            if got:
                return got
        return ""
    text = str(value).strip()
    return text.upper() if re.fullmatch(r"[A-Za-z]{3}", text) else ""


def _number_close(a, b) -> bool:
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) <= max(0.02, abs(float(b)) * 1e-9)
    except (TypeError, ValueError):
        return False


def _currency_from_description(description: str, value: float | None) -> str:
    """Find an explicit currency printed next to the selected amount."""
    if not description or value is None:
        return ""
    pattern = re.compile(
        r"(?:estimated\s+value|estimated\s+contract\s+value|"
        r"estimated\s+overall\s+contract\s+amount|"
        r"valeur\s+estim(?:e|é)e|valoare\s+estimata|"
        r"valoarea\s+estimata)"
        r"[^0-9]{0,180}([0-9][0-9\s.,]{0,40})\s*([A-Za-z]{3})\b",
        re.IGNORECASE,
    )
    for m in pattern.finditer(description):
        parsed = parse_value(m.group(1))
        if _number_close(parsed, value):
            return m.group(2).upper()
    return ""


def _link(record) -> str:
    """Pull an HTML link out of the 'links' block, else build the canonical one."""
    links = record.get("links") or {}
    html = links.get("html") if isinstance(links, dict) else None
    if isinstance(html, dict):
        for key in ("ENG", "FRA", "eng", "fra"):
            if html.get(key):
                return _first(html[key])
        for v in html.values():
            got = _first(v)
            if got:
                return got
    pubnum = _first(record.get("publication-number"))
    return f"https://ted.europa.eu/en/notice/-/detail/{pubnum}" if pubnum else ""


class TedSource(Source):
    def fetch(self) -> list[Tender]:
        expert = (self.settings.get("expert_query") or "").strip()
        since = self.since().isoformat().replace("-", "")
        query = f"publication-date>={since}"
        if expert:
            query = f"({expert}) AND {query}"

        page_size = int(self.settings.get("page_size", 100))
        max_pages = int(self.settings.get("max_pages", 20))
        wanted_types = {t.lower() for t in self.settings.get("notice_types", [])}

        tenders: list[Tender] = []
        token = None
        for page in range(max_pages):
            body = {
                "query": query,
                "fields": FIELDS,
                "limit": page_size,
                "scope": "ACTIVE",
                "paginationMode": "ITERATION",
            }
            if token:
                body["iterationNextToken"] = token

            resp = self.post(ENDPOINT, json=body,
                             headers={"Content-Type": "application/json"})
            data = resp.json()
            notices = data.get("notices") or data.get("results") or []
            if not notices:
                break

            for record in notices:
                t = self._to_tender(record)
                if t is None:
                    continue
                if wanted_types:
                    ntype = _first(record.get("notice-type")).lower()
                    # keep it if we can't tell what type it is
                    if ntype and not any(w in ntype for w in wanted_types):
                        continue
                tenders.append(t)

            token = data.get("iterationNextToken")
            log.info("TED page %s: %s notices (running total %s)",
                     page + 1, len(notices), len(tenders))
            if not token or len(notices) < page_size:
                break

        if not tenders:
            log.info("TED returned nothing for query: %s", query[:200])
        return tenders

    def _to_tender(self, record: dict) -> Tender | None:
        pubnum = _first(record.get("publication-number"))
        title = clean_html(_first(record.get("notice-title")))
        if not pubnum or not title:
            return None

        description = clean_html(
            _join(record.get("description-lot")) + " " + _join(record.get("description-proc"))
        )[:8000]

        deadline = (parse_date(_first(record.get("deadline-receipt-request")))
                    or parse_date(_first(record.get("deadline-date-lot"))))
        lot_value = parse_value(_first(record.get("estimated-value-lot")))
        proc_value = parse_value(_first(record.get("estimated-value-proc")))
        total_value = parse_value(_first(record.get("total-value")))

        # Prefer the procedure-level estimated value for the notice-level
        # headline amount. If it is absent, fall back to the lot value.
        # This avoids pairing a procedure amount with a first-lot currency.
        if proc_value is not None:
            value = proc_value
            currency = _extract_currency(record.get("estimated-value-cur-proc"))
        elif lot_value is not None:
            value = lot_value
            currency = _extract_currency(record.get("estimated-value-cur-lot"))
        else:
            value = total_value
            currency = _extract_currency(record.get("total-value-cur"))

        # If the selected amount is printed with an explicit currency in the
        # returned notice text, that explicit currency wins. Never substitute
        # EUR merely because the currency field is missing or ambiguous.
        text_currency = _currency_from_description(description, value)
        if text_currency:
            currency = text_currency

        contract_end = _parse_duration_to_end_date(
            _first(record.get("duration-proc")) or _first(record.get("duration-lot")), deadline
        )

        return Tender(
            source="ted",
            source_id=pubnum,
            title=title,
            url=_link(record),
            buyer=clean_html(_first(record.get("buyer-name"))),
            country=(_first(record.get("buyer-country")) or "").upper()[:3],
            description=description,
            cpv=" ".join(str(c) for c in (record.get("classification-cpv") or [])
                         if c) if isinstance(record.get("classification-cpv"), list)
                else _join(record.get("classification-cpv")),
            published=parse_date(_first(record.get("publication-date"))),
            deadline=deadline,
            contract_end=contract_end,
            value=value,
            currency=currency or "",
            raw_ref=pubnum,
        )
