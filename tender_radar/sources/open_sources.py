"""Open procurement sources used by Tender Radar.

This module deliberately favors public, machine-readable sources: official OCDS
APIs or the Open Contracting Partnership's public yearly JSONL snapshots.
All sources are disabled by the installer; enable them one at a time and dry-run.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
from datetime import date

from ..models import Tender
from ..active import has_future_deadline
from ..normalize import clean_html, parse_date, parse_value
from .base import Source
from .ocds import release_to_tender

log = logging.getLogger(__name__)


def _latest_release(record: dict):
    """Return the newest release carrying a tender block from an OCDS record."""
    releases = record.get("releases") if isinstance(record, dict) else None
    if isinstance(releases, list):
        candidates = [r for r in releases if isinstance(r, dict) and isinstance(r.get("tender"), dict)]
        if candidates:
            return sorted(candidates, key=lambda r: str(r.get("date") or ""))[-1]
    if isinstance(record, dict) and isinstance(record.get("tender"), dict):
        return record
    return None


def _is_current_tender(release: dict) -> bool:
    """Keep only tenders whose actionable deadline has not passed.

    OCP yearly snapshots contain historical releases.  A recent release date
    alone must not make a closed procurement look live in Tender Radar.
    """
    if not isinstance(release, dict):
        return False
    tender = release.get("tender")
    if not isinstance(tender, dict):
        return False

    period = tender.get("tenderPeriod") or {}
    end_date = parse_date(period.get("endDate"))
    if end_date:
        return end_date >= date.today()

    # Fallback for publishers exposing the deadline as a milestone.
    milestones = tender.get("milestones") or []
    if isinstance(milestones, list):
        dates = []
        for milestone in milestones:
            if isinstance(milestone, dict):
                d = parse_date(milestone.get("dueDate"))
                if d:
                    dates.append(d)
        if dates:
            return max(dates) >= date.today()

    # No explicit deadline: leave the decision to the lookback filter.
    return True


class OcpRegistrySource(Source):
    """Read public OCDS snapshots from the OCP Data Registry.

    V2.5 is deliberately tolerant of the different valid OCDS shapes used by
    publishers: direct releases, record objects containing ``releases``, and
    compiled-release objects.  It also avoids assuming that the first N lines
    of a yearly file are the newest records.
    """

    def _candidate_releases(self, obj: dict) -> list[dict]:
        if not isinstance(obj, dict):
            return []
        candidates: list[dict] = []

        for key in ("compiledRelease", "compiled_release"):
            value = obj.get(key)
            if isinstance(value, dict):
                candidates.append(value)

        releases = obj.get("releases")
        if isinstance(releases, list):
            candidates.extend(r for r in releases if isinstance(r, dict))

        # A line can itself be an OCDS release.
        if isinstance(obj.get("tender"), dict):
            candidates.append(obj)

        # Some publishers expose a record wrapper with a single release-like
        # object under ``record``.
        record = obj.get("record")
        if isinstance(record, dict):
            if isinstance(record.get("releases"), list):
                candidates.extend(r for r in record["releases"] if isinstance(r, dict))
            if isinstance(record.get("tender"), dict):
                candidates.append(record)

        # De-duplicate candidate objects by their stable release/ocid/id.
        out, seen = [], set()
        for r in candidates:
            key = str(r.get("id") or r.get("ocid") or id(r))
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    def _release_date(self, release: dict):
        """Return the best date for lookback filtering.

        Publication/update date wins.  Tender period dates are fallbacks because
        several OCP snapshots omit release.date or use publisher-specific
        release metadata.
        """
        paths = (
            ("date",),
            ("publishedDate",),
            ("lastModified",),
            ("tender", "tenderPeriod", "startDate"),
            ("tender", "tenderPeriod", "endDate"),
            ("tender", "milestones", "0", "dueDate"),
        )
        for path in paths:
            cur = release
            ok = True
            for part in path:
                if isinstance(cur, dict):
                    cur = cur.get(part)
                elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                    cur = cur[int(part)]
                else:
                    ok = False
                    break
            if ok and cur not in (None, ""):
                parsed = parse_date(cur)
                if parsed:
                    return parsed
        return None

    def fetch(self) -> list[Tender]:
        registry_id = int(self.settings["registry_id"])
        year = int(self.settings.get("year") or date.today().year)
        download_name = self.settings.get("download_name", f"{year}.jsonl.gz")
        url = self.settings.get(
            "download_url",
            f"https://data.open-contracting.org/en/publication/{registry_id}/download?name={download_name}",
        )
        country = self.settings.get("country", "")
        max_records = int(self.settings.get("max_records", 0))
        tenders: list[Tender] = []
        seen: set[str] = set()

        stats = {
            "lines": 0,
            "parsed": 0,
            "candidate_releases": 0,
            "with_tender": 0,
            "dated": 0,
            "in_lookback": 0,
            "converted": 0,
        }

        self._wait()
        log.debug("GET %s", url)
        with self.session.get(
            url, timeout=self.timeout, verify=self.ssl_verify, stream=True
        ) as resp:
            resp.raise_for_status()
            raw = gzip.GzipFile(fileobj=resp.raw)
            for line_no, line in enumerate(raw, 1):
                stats["lines"] += 1
                if max_records and line_no > max_records:
                    log.warning(
                        "%s reached max_records=%s; set max_records: 0 to scan the full yearly snapshot",
                        self.name, max_records,
                    )
                    break
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    stats["parsed"] += 1
                except json.JSONDecodeError:
                    continue

                candidates = self._candidate_releases(record)
                stats["candidate_releases"] += len(candidates)
                for release in candidates:
                    if not isinstance(release.get("tender"), dict):
                        continue
                    stats["with_tender"] += 1

                    d = self._release_date(release)
                    period = release.get("tender", {}).get("tenderPeriod") or {}
                    deadline = parse_date(period.get("endDate"))

                    if d:
                        stats["dated"] += 1

                    # Keep a tender when either the release is recent OR the
                    # tender is still open. Yearly OCDS snapshots can contain
                    # open tenders whose release date is older than lookback.
                    recent_release = bool(d and d >= self.since())
                    open_deadline = bool(deadline and deadline >= date.today())

                    if not recent_release and not open_deadline:
                        continue

                    stats["in_lookback"] += 1

                    t = release_to_tender(
                        release, self.name, default_country=country,
                        default_currency=self.settings.get("default_currency", ""),
                    )
                    if t and t.uid() not in seen:
                        seen.add(t.uid())
                        tenders.append(t)
                        stats["converted"] += 1

        log.info(
            "%s OCDS diagnostics: lines=%s parsed=%s releases=%s tender=%s "
            "dated=%s in_lookback=%s converted=%s",
            self.name,
            stats["lines"],
            stats["parsed"],
            stats["candidate_releases"],
            stats["with_tender"],
            stats["dated"],
            stats["in_lookback"],
            stats["converted"],
        )
        return tenders


class MtenderSource(Source):
    """Moldova MTender public OCDS point; no credentials required."""

    LIST_URL = "https://public.mtender.gov.md/tenders/"
    DETAIL_URL = "https://public.eprocurement.systems/ocds/tenders/{ocid}"

    def fetch(self) -> list[Tender]:
        list_url = self.settings.get("list_url", self.LIST_URL)
        detail_url = self.settings.get("detail_url", self.DETAIL_URL)
        max_items = int(self.settings.get("max_items", 100))
        offset = self.settings.get("offset", "0")
        tenders: list[Tender] = []
        seen: set[str] = set()

        for _ in range(max(1, int(self.settings.get("pages", 3)))):
            data = self.get(list_url, params={"offset": offset}).json()
            items = data.get("data") if isinstance(data, dict) else data
            if not isinstance(items, list):
                items = data.get("tenders") if isinstance(data, dict) else []
            if not items:
                break
            for item in items:
                if len(tenders) >= max_items:
                    break
                ocid = item.get("ocid") if isinstance(item, dict) else None
                if not ocid:
                    ocid = item.get("id") if isinstance(item, dict) else None
                if not ocid or str(ocid) in seen:
                    continue
                seen.add(str(ocid))
                try:
                    payload = self.get(detail_url.format(ocid=ocid)).json()
                except Exception as exc:
                    log.warning("MTender %s unavailable: %s", ocid, exc)
                    continue
                record = payload
                if isinstance(payload, dict) and isinstance(payload.get("records"), list):
                    recs = payload["records"]
                    record = recs[0] if recs else {}
                release = _latest_release(record)
                if not release:
                    continue
                published = parse_date(release.get("date"))
                if published and published < self.since():
                    continue
                t = release_to_tender(release, self.name, default_country="MD", default_currency="MDL")
                if t:
                    # MTender's public portal is a better landing page than the OCDS API.
                    t.url = f"https://mtender.gov.md/en/tenders/{ocid}"
                    tenders.append(t)
            if len(tenders) >= max_items:
                break
            next_offset = None
            if isinstance(data, dict):
                next_offset = data.get("nextOffset") or data.get("next_offset")
                if next_offset is None:
                    next_offset = data.get("offset")
            if next_offset in (None, offset, ""):
                break
            offset = next_offset
        log.info("MTender: %s notices", len(tenders))
        return tenders


# --- V2.4 OCDS helpers ---
from datetime import datetime, timezone

def _v24_get(obj, *paths):
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None

def _v24_parse_dt(value):
    if not value:
        return None
    s = str(value).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(s[:10], fmt).replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return None

def _v24_first_date(record):
    # Prefer tender period start/end, then tender status/update/publication.
    return _v24_get(
        record,
        "tender.tenderPeriod.startDate",
        "tender.tenderPeriod.endDate",
        "tender.milestones.0.dueDate",
        "tender.milestones.0.dateMet",
        "date",
        "publishedDate",
        "lastModified",
        "awards.0.date",
    )

def _v24_title(record):
    return _v24_get(record, "tender.title", "title", "tender.description", "description") or ""

def _v24_description(record):
    return _v24_get(record, "tender.description", "description", "tender.title", "title") or ""

def _v24_status(record):
    return str(_v24_get(record, "tender.status", "status") or "").lower()

def _v24_value(record):
    amount = _v24_get(record, "tender.value.amount", "value.amount", "contracts.0.value.amount")
    currency = _v24_get(record, "tender.value.currency", "value.currency", "contracts.0.value.currency")
    return amount, currency

def _v24_buyer(record):
    b = record.get("buyer") if isinstance(record, dict) else None
    if isinstance(b, dict):
        return b.get("name") or b.get("id") or ""
    return str(b or "")

def _v24_ocid(record):
    return str(record.get("ocid") or record.get("id") or "")

def _v24_extract_records(obj):
    # OCP registry files are compiled releases: one JSON object per line.
    # Some download endpoints wrap releases in a top-level `records` array.
    if isinstance(obj, dict) and isinstance(obj.get("records"), list):
        return obj["records"]
    if isinstance(obj, dict):
        return [obj]
    return []


class TanzaniaNestLiveSource(Source):
    """Live OCDS releases from Tanzania NeST Data Portal."""

    DEFAULT_URL = "https://nest.go.tz/gateway/nest-data-portal-api/api/releases"

    def fetch(self) -> list[Tender]:
        url = self.settings.get("base_url", self.DEFAULT_URL)
        max_pages = int(self.settings.get("max_pages", 10))
        cursor = str(self.settings.get("cursor", "0"))
        since = self.since().isoformat() + "T00:00:00Z"
        tenders: list[Tender] = []
        seen: set[str] = set()

        for page in range(1, max_pages + 1):
            params = {"cursor": cursor, "since": since}
            self._wait()
            log.debug("GET %s %s", url, params)
            resp = self.session.get(url, params=params, timeout=self.timeout, verify=self.ssl_verify)
            resp.raise_for_status()
            data = resp.json()

            releases = data.get("releases") if isinstance(data, dict) else None
            if releases is None and isinstance(data, dict):
                package = data.get("data")
                if isinstance(package, dict):
                    releases = package.get("releases")
                elif isinstance(package, list):
                    releases = package
            if not isinstance(releases, list):
                releases = []

            log.info("%s NeST API page %s: %s releases", self.name, page, len(releases))
            if not releases:
                break

            for release in releases:
                if not isinstance(release, dict) or not isinstance(release.get("tender"), dict):
                    continue
                period = release.get("tender", {}).get("tenderPeriod") or {}
                deadline = parse_date(period.get("endDate"))
                if deadline and deadline < date.today():
                    continue
                t = release_to_tender(release, self.name, default_country="TZ", default_currency="TZS")
                if t and t.uid() not in seen:
                    seen.add(t.uid())
                    tenders.append(t)

            next_cursor = None
            if isinstance(data, dict):
                next_cursor = data.get("nextCursor") or data.get("next_cursor")
                if next_cursor is None:
                    links = data.get("links")
                    if isinstance(links, dict):
                        next_cursor = links.get("nextCursor") or links.get("next_cursor")
            if next_cursor in (None, "", cursor):
                break
            cursor = str(next_cursor)

        log.info("%s NeST live: %s current tenders", self.name, len(tenders))
        return tenders
