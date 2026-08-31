"""The one record shape every source has to produce."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional


@dataclass
class Tender:
    source: str                      # e.g. "ted", "za_etenders"
    source_id: str                   # id unique within that source
    title: str
    url: str
    buyer: str = ""
    country: str = ""                # ISO-2 where we can get it
    description: str = ""
    cpv: str = ""                    # space-separated codes, EU only
    published: Optional[date] = None
    deadline: Optional[date] = None
    value: Optional[float] = None
    currency: str = ""
    language: str = ""
    raw_ref: str = ""                # buyer's own reference number

    # Filled in later by the pipeline
    score: float = 0.0
    matched: str = ""                # human-readable list of what matched
    fingerprint: str = ""
    duplicate_of: Optional[str] = None

    def uid(self) -> str:
        """Stable primary key across runs."""
        return f"{self.source}:{self.source_id}"

    def searchable(self) -> tuple[str, str]:
        """(title, body) as given to the scorer."""
        return self.title or "", " ".join([self.description or "", self.raw_ref or ""])

    def compute_fingerprint(self) -> str:
        from .normalize import normalise_text

        basis = normalise_text(self.title) + "|" + normalise_text(self.buyer)
        self.fingerprint = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
        return self.fingerprint

    def to_row(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat() if self.published else None
        d["deadline"] = self.deadline.isoformat() if self.deadline else None
        d["uid"] = self.uid()
        return d
