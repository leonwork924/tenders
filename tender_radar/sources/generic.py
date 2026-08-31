"""Generic adapters: RSS/Atom feeds, a CSV drop folder, and a licensed vendor API.

The CSV inbox is the sanctioned route for the paid aggregators. You export or
receive a file from your subscription, drop it in inbox/, and it joins the same
pipeline as everything else. No scraping, no terms-of-service problem, and the
file is archived to inbox/processed/ afterwards.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from ..models import Tender
from ..normalize import clean_html, first_present, parse_date, parse_value
from .base import Source, SourceError

log = logging.getLogger(__name__)


def _map_record(record: dict, column_map: dict, source: str,
                fallback_country: str = "") -> Tender | None:
    title = clean_html(first_present(record, column_map.get("title", ["title"])) or "")
    if not title:
        return None
    url = str(first_present(record, column_map.get("url", ["url"])) or "")
    reference = str(first_present(record, column_map.get("reference", ["reference"])) or "")

    ident = reference or url or title
    source_id = hashlib.sha1(f"{source}|{ident}".encode()).hexdigest()[:20]

    return Tender(
        source=source,
        source_id=source_id,
        title=title,
        url=url,
        buyer=clean_html(first_present(record, column_map.get("buyer", ["buyer"])) or ""),
        country=str(first_present(record, column_map.get("country", ["country"])) or
                    fallback_country)[:40],
        description=clean_html(
            first_present(record, column_map.get("description", ["description"])) or "")[:8000],
        published=parse_date(first_present(record, column_map.get("published", ["published"]))),
        deadline=parse_date(first_present(record, column_map.get("deadline", ["deadline"]))),
        value=parse_value(first_present(record, column_map.get("value", ["value"]))),
        currency=str(first_present(record, column_map.get("currency", ["currency"])) or ""),
        raw_ref=reference,
    )


class RssSource(Source):
    """Any RSS or Atom feed a buyer or portal publishes."""

    def fetch(self) -> list[Tender]:
        import feedparser

        tenders: list[Tender] = []
        for feed in self.settings.get("feeds", []):
            url = feed.get("url")
            if not url:
                continue
            try:
                raw = self.get(url).content
            except Exception as exc:  # one bad feed shouldn't kill the run
                log.warning("RSS %s failed: %s", feed.get("name", url), exc)
                continue
            parsed = feedparser.parse(raw)
            feed_name = feed.get("name") or parsed.feed.get("title", url)
            for entry in parsed.entries:
                title = clean_html(entry.get("title", ""))
                if not title:
                    continue
                link = entry.get("link", "")
                published = None
                if entry.get("published_parsed"):
                    published = datetime(*entry.published_parsed[:6]).date()
                tenders.append(Tender(
                    source=self.name,
                    source_id=hashlib.sha1(
                        f"{feed_name}|{entry.get('id', link or title)}".encode()).hexdigest()[:20],
                    title=title,
                    url=link,
                    buyer=feed_name,
                    country=feed.get("country", ""),
                    description=clean_html(entry.get("summary", ""))[:8000],
                    published=published,
                ))
            log.info("RSS %s: %s entries", feed_name, len(parsed.entries))
        return tenders


class CsvInboxSource(Source):
    """Read licensed exports dropped into a folder as CSV or JSON."""

    def fetch(self) -> list[Tender]:
        folder = Path(self.settings.get("folder", "inbox"))
        folder.mkdir(parents=True, exist_ok=True)
        processed = folder / "processed"
        processed.mkdir(exist_ok=True)

        column_map = self.settings.get("column_map", {})
        tenders: list[Tender] = []

        files = sorted([p for p in folder.iterdir()
                        if p.is_file() and p.suffix.lower() in (".csv", ".tsv", ".json")])
        if not files:
            log.info("csv_inbox: nothing in %s/", folder)
            return []

        for path in files:
            provider = path.stem.split("_")[0].lower() or "inbox"
            try:
                records = self._read(path)
            except Exception as exc:
                log.error("csv_inbox: could not read %s: %s", path.name, exc)
                continue
            count = 0
            for record in records:
                t = _map_record(record, column_map, f"inbox_{provider}")
                if t:
                    tenders.append(t)
                    count += 1
            log.info("csv_inbox: %s -> %s rows", path.name, count)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.move(str(path), str(processed / f"{stamp}_{path.name}"))
        return tenders

    @staticmethod
    def _read(path: Path) -> list[dict]:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                for key in ("data", "records", "results", "tenders"):
                    if isinstance(data.get(key), list):
                        return data[key]
                return [data]
            return data
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open(newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh, delimiter=delimiter))


class VendorApiSource(Source):
    """A paid aggregator's own API. Only usable with a key they issued you."""

    def fetch(self) -> list[Tender]:
        base = self.settings.get("base_url", "").strip()
        if not base:
            raise SourceError("vendor_api: base_url is empty; set it or disable the source")

        key_env = self.settings.get("api_key_env", "VENDOR_API_KEY")
        api_key = os.environ.get(key_env, "")
        if not api_key:
            raise SourceError(f"vendor_api: environment variable {key_env} is not set")

        params = dict(self.settings.get("params", {}))
        params.setdefault("from", self.since().isoformat())

        headers = {}
        style = self.settings.get("auth_style", "bearer")
        if style == "bearer":
            headers["Authorization"] = f"Bearer {api_key}"
        elif style == "header":
            headers[self.settings.get("auth_header", "X-API-Key")] = api_key
        else:
            params[self.settings.get("auth_param", "api_key")] = api_key

        data = self.get(base, params=params, headers=headers).json()
        for key in (self.settings.get("records_path", "data")).split("."):
            if isinstance(data, dict):
                data = data.get(key, data if key == "" else [])
        if not isinstance(data, list):
            raise SourceError("vendor_api: records_path did not resolve to a list")

        provider = (self.settings.get("name") or "vendor").lower().replace(" ", "_")
        column_map = self.settings.get("column_map", {})
        return [t for t in (_map_record(r, column_map, f"api_{provider}") for r in data) if t]
