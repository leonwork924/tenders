from dataclasses import dataclass
@dataclass
class VerificationResult: status:str; method:str; detail:str=""
class OfflineFixtureProvider:
    def search_companies(self,brief):
        xs=[{"name":"ABC International","website":"https://abc.example","industry":"financial_services","employee_count":1200,"country":"South Africa","city":"Cape Town","international_presence":True,"countries_active":["South Africa","UK","UAE","Singapore"],"international_share":.18},{"name":"Tech Expansion Ltd","website":"https://tech.example","industry":"technology","employee_count":2500,"country":"South Africa","international_presence":True,"countries_active":["South Africa","Kenya"]}]
        return [x for x in xs if x["employee_count"]>=brief.get("min_employees",0) and (not brief.get("country") or x["country"]==brief["country"])]
    def search_contacts(self,c):
        return [{"first_name":"Sarah","last_name":"Johnson","title":"Global Mobility Manager","department":"HR","email":"sarah.johnson@abc.example","phone":"+27 00 000 0000","professional_profile":"https://example.org/sarah"}] if c["name"]=="ABC International" else []
    def company_news(self,c):
        return [{"headline":"ABC International opens new office in Dubai","date":"2026-09-01","source":"https://example.org/news/abc-dubai"}] if c["name"]=="ABC International" else []
class DefaultEmailVerifier:
    def verify_email(self,email):
        import re
        if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$",email):return VerificationResult("INVALID","RULE","Invalid email syntax")
        return VerificationResult("UNVERIFIED","RULE","Syntax checked; deliverability was not verified")


class NewsletterProvider:
    """Source réelle (pas fixture) : lit newsletter.json et en extrait les
    entreprises citées dans les deals + leurs actus associées. Pas de vraie
    recherche par pays/effectif (cette donnée n'existe pas dans la
    newsletter) -- toutes les entreprises citées sont retournées, le filtre
    min_employees est ignoré ici (volontairement, contrairement à
    OfflineFixtureProvider). search_contacts renvoie toujours vide (aucune
    source de contact branchée)."""

    def __init__(self, newsletter_path):
        import json
        with open(newsletter_path, encoding="utf-8") as f:
            self._data = json.load(f)
        self._companies = self._extract_companies()

    def _extract_companies(self):
        seen = {}

        def add(name, pays, headline, date, source):
            name = (name or "").strip()
            if not name or len(name) > 120:  # filtre grossier anti faux-positif
                return
            if name not in seen:
                seen[name] = {
                    "name": name, "country": pays, "news": [],
                    # Champs requis par scoring.py, jamais connus depuis la
                    # newsletter -- valeurs neutres plutôt qu'inventées.
                    "industry": None, "employee_count": None,
                    "international_presence": None, "countries_active": None,
                }
            seen[name]["news"].append({"headline": headline, "date": date, "source": source})

        def parties_to_names(parties):
            # "Deutsche Telekom ← Fiberhost & Inea" -> plusieurs noms
            import re
            parts = re.split(r"[←→]|(?:\s+and\s+)|&|,", parties or "")
            return [p.strip() for p in parts if p.strip()]

        for sector_items in (self._data.get("newly_signed") or {}).values():
            for it in sector_items:
                headline = f"{it.get('deal', '')} — {it.get('details', '')}".strip(" —")
                for name in parties_to_names(it.get("parties")):
                    add(name, it.get("pays"), headline, it.get("date_signed"),
                        (it.get("source") or {}).get("url"))

        for it in self._data.get("investments") or []:
            headline = f"{it.get('deal', '')} — {it.get('scope', '')}".strip(" —")
            for name in parties_to_names(it.get("parties")):
                add(name, it.get("pays"), headline, None, (it.get("source") or {}).get("url"))

        return list(seen.values())

    def search_companies(self, brief):
        country = brief.get("country")
        return [c for c in self._companies if not country or c["country"] == country]

    def search_contacts(self, company):
        return []  # pas de source de contact branchée pour l'instant

    def company_news(self, company):
        return next((c["news"] for c in self._companies if c["name"] == company["name"]), [])
