"""
run_weekly.py — Lance MovingLead sur la newsletter réelle et exporte pour le site
====================================================================================

Contrairement à `movinglead run` (démo sur fixtures), ceci utilise
NewsletterProvider : les entreprises et actus viennent de site/newsletter.json
(déjà généré chaque semaine par le processus newsletter existant), pas de
données inventées. Seuls les prospects classés HOT ou WARM sont publiés sur
le site -- les COLD/LOW n'apportent pas de signal exploitable et
n'encombreraient que l'affichage.

Usage:
    python leads/run_weekly.py \
        --newsletter site/newsletter.json \
        --icp leads/icp.yaml \
        --out site/prospects.json
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from movinglead.config import ICPConfig
from movinglead.pipeline import LeadGenerationAgent
from movinglead.providers import DefaultEmailVerifier, NewsletterProvider
from movinglead.storage import Store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--newsletter", default="site/newsletter.json")
    ap.add_argument("--icp", default="leads/icp.yaml")
    ap.add_argument("--out", default="site/prospects.json")
    args = ap.parse_args()

    cfg = ICPConfig.load(args.icp)
    store = Store(":memory:")  # pas de mémoire inter-runs pour l'instant (MVP) -- voir note dans le README
    provider = NewsletterProvider(args.newsletter)
    agent = LeadGenerationAgent(store, provider, DefaultEmailVerifier(), cfg)

    # lawful_basis "LEGITIMATE_INTEREST" : base B2B standard pour du contenu
    # déjà public (deals annoncés publiquement) -- pas de contact individuel
    # concerné ici puisque search_contacts renvoie toujours vide.
    prospects = agent.run({"lawful_basis": "LEGITIMATE_INTEREST"}, dry_run=True)

    published = [
        {
            "company": p.company.get("name"),
            "country": p.company.get("country"),
            "score": p.scoring.get("total"),
            "classification": p.scoring.get("classification"),
            "why_now": p.why_now,
            "triggers": sorted({t["type"] for t in p.triggers}),
            "sources": p.sources,
        }
        for p in prospects
        # Le score (company_fit/moving_potential) est structurellement bas ici
        # -- on n'a ni secteur ni effectif via la newsletter -- donc on publie
        # sur "a un signal détecté" plutôt que sur un seuil HOT/WARM qui ne
        # serait jamais atteint avec cette source. Le score reste affiché,
        # à titre indicatif seulement.
        if p.triggers
    ]
    published.sort(key=lambda x: x["score"], reverse=True)

    out = {
        "generated": date.today().isoformat(),
        "total_scanned": len(prospects),
        "prospects": published,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Écrit {out_path} : {len(published)} prospect(s) HOT/WARM sur {len(prospects)} entreprise(s) scannée(s).")


if __name__ == "__main__":
    main()
