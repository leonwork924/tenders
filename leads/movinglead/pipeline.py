import uuid
from datetime import datetime, timezone, timedelta
from .models import Value, Contact, Prospect
from .dedup import duplicate_verdict, check_suppression
from .llm import extract_triggers_batch
from .scoring import score

ROLE_ACCOUNTS = {"hr", "info", "hello", "contact", "admin", "sales", "support"}


def _trigger_description(t):
    """Libellé lisible pour why_now -- ex. NEW_OFFICE -> 'new office'."""
    return t["type"].replace("_", " ").lower()


class LeadGenerationAgent:
    def __init__(self, store, providers, verifier, cfg):
        self.store = store
        self.providers = providers
        self.verifier = verifier
        self.cfg = cfg

    def run(self, brief, dry_run=False):
        out = []
        suppressed = self.store.suppressions()
        run_id = str(uuid.uuid4())

        companies = list(self.providers.search_companies(brief))

        # Batch les actus de TOUTES les entreprises du run pour un seul appel
        # Claude au lieu d'un appel par entreprise (coût + latence).
        all_news = []
        news_ranges = []
        for company in companies:
            news = self.providers.company_news(company) or []
            start = len(all_news)
            all_news.extend(news)
            news_ranges.append((start, len(all_news)))
        per_item_triggers = extract_triggers_batch(all_news)

        for idx, company in enumerate(companies):
            if check_suppression(company["name"], domain=company.get("domain"), suppressed=suppressed)[0]:
                continue
            raw = (self.providers.search_contacts(company) or [None])[0]
            email = raw.get("email") if raw else None
            verdict, evidence = duplicate_verdict(
                company["name"], email=email,
                contact_name=(" ".join([raw.get("first_name", ""), raw.get("last_name", "")]) if raw else None),
                existing=self.store.all_prospects(),
            )
            if verdict == "DUPLICATE":
                continue

            c = None
            if raw:
                vr = self.verifier.verify_email(email)
                email = None if vr.status == "INVALID" else email
                method = "PUBLIC_SOURCE"
                em = Value(email, method, .85) if email else Value()
                c = Contact(
                    str(uuid.uuid4()), Value(raw.get("first_name"), method, .85), Value(raw.get("last_name"), method, .85),
                    Value(raw.get("title"), method, .85), Value(raw.get("department"), method, .80), em,
                    Value(raw.get("phone"), method, .80), Value(raw.get("professional_profile"), method, .80),
                    brief.get("lawful_basis", "NOT_ESTABLISHED"),
                    "YELLOW" if email and email.split("@")[0].lower() in ROLE_ACCOUNTS else "GREEN",
                )

            start, end = news_ranges[idx]
            news = all_news[start:end]
            triggers = [t for group in per_item_triggers[start:end] for t in group]
            s = score(company, c, triggers, self.cfg)

            disq = []
            if company.get("insolvent"):
                disq.append("INSOLVENCY")
            if company.get("excluded_industry"):
                disq.append("EXCLUDED_INDUSTRY")
            if c and c.lawful_basis == "NOT_ESTABLISHED":
                disq.append("MISSING_LAWFUL_BASIS")

            review = "RED" if brief.get("lead_type") == "HIGH_VALUE_B2C" or disq else (
                "YELLOW" if verdict == "POSSIBLE_DUPLICATE" or (c and c.review_level != "GREEN") else "GREEN"
            )
            if disq:
                s["total"] = 0
                s["classification"] = "LOW"

            if triggers:
                why = f"{company['name']} has relevant trigger evidence: {_trigger_description(triggers[0])}."
            elif s["classification"] in ("HOT", "WARM"):
                why = f"{company['name']} matches the target profile and has meaningful relocation potential."
            else:
                why = ""

            p = Prospect(
                "pro_" + uuid.uuid4().hex[:10], company, [c] if c else [], [], news, triggers, s,
                s["moves_estimate"], {"disqualifiers": disq}, why,
                "HUMAN_REVIEW" if review != "GREEN" else ("OUTREACH" if s["classification"] in ("HOT", "WARM") else "NURTURE"),
                .85, [n.get("source") for n in news if n.get("source")], verdict, evidence, review != "GREEN", review,
                c.lawful_basis if c else brief.get("lawful_basis", "NOT_ESTABLISHED"),
                (datetime.now(timezone.utc) + timedelta(days=self.cfg.raw["refresh_days"][s["classification"]])).isoformat(),
                run_id,
            )
            if not dry_run:
                self.store.save(p)
            out.append(p)
        return out
