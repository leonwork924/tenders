"""Deduplicate across sources.

Two passes:
  1. Exact fingerprint (normalised title + buyer). Cheap, catches aggregators
     that republish TED verbatim.
  2. Fuzzy title match against everything already in the database, guarded by a
     buyer-name check so two different councils buying "Office relocation" stay
     separate records.
"""

from __future__ import annotations

from rapidfuzz import fuzz, process

from .models import Tender
from .normalize import normalise_text


class Deduplicator:
    def __init__(self, db, config: dict):
        cfg = config.get("dedupe", {})
        self.title_threshold = float(cfg.get("title_threshold", 90))
        self.buyer_threshold = float(cfg.get("buyer_threshold", 80))

        rows = db.fingerprints()
        self.by_fingerprint = {r["fingerprint"]: r["uid"] for r in rows if r["fingerprint"]}
        self.titles = {r["uid"]: normalise_text(r["title"]) for r in rows}
        self.buyers = {r["uid"]: normalise_text(r["buyer"]) for r in rows}
        self._title_index = list(self.titles.items())

    def check(self, tender: Tender) -> str | None:
        """Return the uid this tender duplicates, or None."""
        fp = tender.compute_fingerprint()
        uid = tender.uid()

        hit = self.by_fingerprint.get(fp)
        if hit and hit != uid:
            return hit

        title = normalise_text(tender.title)
        buyer = normalise_text(tender.buyer)
        if len(title) >= 12 and self._title_index:
            choices = [t for _, t in self._title_index]
            match = process.extractOne(title, choices, scorer=fuzz.token_set_ratio,
                                       score_cutoff=self.title_threshold)
            if match:
                other_uid = self._title_index[match[2]][0]
                if other_uid != uid:
                    other_buyer = self.buyers.get(other_uid, "")
                    # No buyer on either side: title alone has to carry it, so be strict.
                    if not buyer or not other_buyer:
                        if match[1] >= 97:
                            return other_uid
                    elif fuzz.token_set_ratio(buyer, other_buyer) >= self.buyer_threshold:
                        return other_uid
        return None

    def register(self, tender: Tender):
        """Add an accepted tender to the in-memory index for this run."""
        uid = tender.uid()
        if tender.fingerprint:
            self.by_fingerprint.setdefault(tender.fingerprint, uid)
        title = normalise_text(tender.title)
        self.titles[uid] = title
        self.buyers[uid] = normalise_text(tender.buyer)
        self._title_index.append((uid, title))
