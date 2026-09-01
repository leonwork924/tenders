"""Score a tender against the keyword taxonomy.

Score = sum over groups of (group weight x hits, capped) + CPV boost + negatives.
Title hits count more than description hits. Repeats of the same term give
diminishing returns so a notice that says "archivage" fifteen times does not
outrank one that says "archivage" and "numerisation" once each.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Tender
from .normalize import compile_terms, normalise_text


@dataclass
class Group:
    name: str
    weight: float
    patterns: list


class Scorer:
    def __init__(self, config: dict):
        kw = config.get("keywords", {})
        sc = config.get("scoring", {})
        self.title_multiplier = float(sc.get("title_multiplier", 2.5))
        self.repeat_factor = float(sc.get("repeat_factor", 0.25))
        self.group_cap = float(sc.get("group_cap", 12.0))
        # Generic/context terms should corroborate a real match, not create one.
        # This list is deliberately conservative and can be extended later.
        self.weak_terms = {
            "information management", "installation", "delivery and installation",
            "logistics", "storage", "warehousing", "packing", "filing",
            "indexing", "metadata", "retrieval", "scanner", "preservation",
            "mobility", "moving", "immigration", "visa", "work permit",
            "travel management", "assignment management", "home search",
            "freight forwarding", "cargo handling", "cargo storage",
            "transportation", "goods transport", "data migration",
            "delivery", "furniture",
        }
        self.weak_multiplier = float(sc.get("weak_term_multiplier", 0.20))
        self.weak_standalone_cap = float(sc.get("weak_standalone_cap", 4.0))

        self.groups = [
            Group(g["name"], float(g.get("weight", 1.0)), compile_terms(g.get("terms", [])))
            for g in kw.get("groups", [])
        ]
        neg = kw.get("negative", {}) or {}
        self.negative_weight = float(neg.get("weight", -5.0))
        self.negative_patterns = compile_terms(neg.get("terms", []))
        self.cpv_boost = {str(k): float(v) for k, v in (kw.get("cpv_boost", {}) or {}).items()}

    # -- internals ---------------------------------------------------------
    def _count(self, pattern, text: str) -> int:
        return len(pattern.findall(text)) if text else 0

    def _group_score(self, group: Group, title: str, body: str):
        total = 0.0
        hits = []
        for term, pattern in group.patterns:
            t_hits = self._count(pattern, title)
            b_hits = self._count(pattern, body)
            if not (t_hits or b_hits):
                continue
            # Each title occurrence is worth title_multiplier, each description
            # occurrence 1. The strongest occurrence counts in full; every
            # further one is damped by repeat_factor, so a notice repeating one
            # word cannot outrank a notice matching several distinct terms.
            occurrences = [self.title_multiplier] * t_hits + [1.0] * b_hits
            # Broad terms are useful evidence, but should not independently
            # promote a notice into the shortlist.
            term_norm = normalise_text(term.rstrip("*"))
            is_weak = term_norm in {normalise_text(x) for x in self.weak_terms}
            occurrences.sort(reverse=True)
            effective = occurrences[0] + self.repeat_factor * sum(occurrences[1:])
            contribution = group.weight * effective
            if is_weak:
                contribution *= self.weak_multiplier
            total += contribution
            hits.append(f"{term}{'*T' if t_hits else ''}{'~weak' if is_weak else ''}")
        return min(total, self.group_cap), hits

    def _cpv_score(self, cpv: str):
        if not cpv or not self.cpv_boost:
            return 0.0, []
        best = 0.0
        hits = []
        for code in str(cpv).replace(",", " ").split():
            for prefix, weight in self.cpv_boost.items():
                if code.startswith(prefix) and weight > best:
                    best, hits = weight, [f"cpv:{code}"]
        return best, hits

    # -- public ------------------------------------------------------------
    def score(self, tender: Tender) -> tuple[float, str]:
        raw_title, raw_body = tender.searchable()
        title = normalise_text(raw_title)
        body = normalise_text(raw_body)

        total = 0.0
        matched: list[str] = []
        strong_match = False

        for group in self.groups:
            before = len(matched)
            gs, hits = self._group_score(group, title, body)
            if gs:
                total += gs
                matched.append(f"{group.name}({gs:.1f}): " + ", ".join(hits[:6]))
                if any("~weak" not in h for h in hits):
                    strong_match = True

        cs, chits = self._cpv_score(tender.cpv)
        if cs:
            total += cs
            matched.append(f"cpv(+{cs:.1f}): " + ", ".join(chits))
            # A precise CPV code is real evidence on its own -- unlike a lone
            # weak keyword, it shouldn't need a matching keyword in the same
            # language to clear the threshold. This is what makes CPV a
            # genuinely language-independent safety net (Dutch, Croatian...
            # any source whose narrative text isn't covered by the taxonomy
            # yet, but that still tags CPV correctly).
            if cs >= 6.0:
                strong_match = True

        neg_hits = [t for t, p in self.negative_patterns
                    if self._count(p, title) or self._count(p, body)]
        if neg_hits:
            total += self.negative_weight * min(len(neg_hits), 2)
            matched.append(f"negative({self.negative_weight * min(len(neg_hits), 2):.1f}): "
                           + ", ".join(neg_hits[:4]))

        # A notice supported only by broad/context terms cannot cross the
        # shortlist threshold. Keep a small score so it remains inspectable.
        if not strong_match:
            total = min(total, self.weak_standalone_cap)

        total = max(total, 0.0)
        tender.score = round(total, 2)
        tender.matched = " | ".join(matched)
        return tender.score, tender.matched
