"""The daily run: fetch -> filter -> score -> dedupe -> store."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from .db import Database
from .dedupe import Deduplicator
from .models import Tender
from .scoring import Scorer
from .sources import build_sources

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    fetched: int = 0
    kept: int = 0
    new: int = 0
    duplicates: int = 0
    below_threshold: int = 0
    filtered_out: int = 0
    filtered: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    per_source: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"fetched {self.fetched} | new {self.new} | duplicates {self.duplicates} "
            f"| below score {self.below_threshold} | filtered {self.filtered_out}"
        ]
        for name, count in sorted(self.per_source.items()):
            lines.append(f"  {name}: {count}")
        for err in self.errors:
            lines.append(f"  ERROR {err}")
        return "\n".join(lines)


def _passes_filters(tender: Tender, config: dict) -> bool:
    filters = config.get("filters", {}) or {}
    include = [c.upper() for c in (filters.get("countries_include") or [])]
    exclude = [c.upper() for c in (filters.get("countries_exclude") or [])]
    country = (tender.country or "").upper()

    if include and country and not any(country.startswith(c) for c in include):
        return False
    if exclude and country and any(country.startswith(c) for c in exclude):
        return False

    # Publication age is not an eligibility rule: an older AO remains actionable
    # while its submission deadline is still open.
    if tender.deadline and tender.deadline < date.today():
        return False
    min_days = int(config.get("run", {}).get("min_days_to_deadline", 0))
    if min_days and tender.deadline and tender.deadline < date.today() + timedelta(days=min_days):
        return False
    return True


def run(config: dict, only_source: str | None = None, dry_run: bool = False) -> RunReport:
    db = Database(config["database"]["path"])
    scorer = Scorer(config)
    deduper = Deduplicator(db, config)
    report = RunReport()
    min_score = float(config.get("run", {}).get("min_score", 0))
    run_id = db.start_run()

    try:
        for source in build_sources(config, only_source=only_source):
            log.info("--- %s ---", source.name)
            try:
                tenders = source.fetch()
                db.record_source(source.name, ok=True)
            except Exception as exc:
                msg = f"{source.name}: {type(exc).__name__}: {exc}"
                log.error(msg)
                report.errors.append(msg)
                db.record_source(source.name, ok=False, error=str(exc))
                continue

            report.fetched += len(tenders)
            report.per_source[source.name] = len(tenders)

            for tender in tenders:
                scorer.score(tender)
                if not _passes_filters(tender, config):
                    report.filtered_out += 1
                    reason = []
                    filters = config.get("filters", {}) or {}
                    include = [c.upper() for c in (filters.get("countries_include") or [])]
                    exclude = [c.upper() for c in (filters.get("countries_exclude") or [])]
                    country = (tender.country or "").upper()
                    if include and country and not any(country.startswith(c) for c in include):
                        reason.append("country not included")
                    if exclude and country and any(country.startswith(c) for c in exclude):
                        reason.append("country excluded")
                    min_days = int(config.get("run", {}).get("min_days_to_deadline", 0))
                    if min_days and tender.deadline and tender.deadline < date.today() + timedelta(days=min_days):
                        reason.append(f"deadline < {min_days} days")
                    item = tender.to_row()
                    item["filter_reason"] = "; ".join(reason) or "filter rule"
                    report.filtered.append(item)
                    continue

                scorer.score(tender)
                if tender.score < min_score:
                    report.below_threshold += 1
                    # still stored, so tuning the threshold later needs no refetch

                dup = deduper.check(tender)
                if dup:
                    tender.duplicate_of = dup
                    report.duplicates += 1
                else:
                    deduper.register(tender)

                if not dry_run:
                    if db.upsert(tender):
                        report.new += 1
                report.kept += 1

            if not dry_run:
                db.commit()

        db.end_run(run_id, report.fetched, report.new, report.duplicates,
                   "; ".join(report.errors)[:2000])
    finally:
        db.close()

    return report
