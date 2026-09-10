"""
export_site.py — Transforme diplomats_merged.json (+ sources.json) en site/contact.json
==========================================================================================

Dernière étape du pipeline : prend la sortie de merge_sources.py, la recroise avec la
liste des sources (pour garder un pays même quand aucune donnée n'a pu en être extraite,
avec le lien officiel en repli), et écrit le fichier que site/contact.js consomme.

Usage:
    python diplomats/src/export_site.py \
        --sources diplomats/data/sources.json \
        --merged diplomats/data/diplomats_merged.json \
        --out site/contact.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

METHODOLOGY = (
    "Chaque semaine, un agent automatisé consulte le site du ministère des Affaires "
    "étrangères de chaque pays listé ci-dessous et tente d'y repérer la liste "
    "diplomatique officielle (souvent un PDF) ainsi que, si elle existe, une liste "
    "distincte de consuls honoraires, dont il extrait les noms et titres par "
    "reconnaissance de motifs (« S.E. M./Mme », « H.E. Mr./Mrs », « Ambassadeur », "
    "« Consul honoraire », etc.). Ces données sont "
    "complétées par les listes officielles de représentants permanents de l'ONU, de "
    "l'Union africaine et de l'OSCE. Quand un email ou un téléphone apparaît à "
    "proximité du nom sur la page source, il est également extrait — il s'agit presque "
    "toujours du standard institutionnel de l'ambassade, pas d'une ligne personnelle "
    "(sauf, potentiellement, pour une partie des consuls honoraires, souvent des "
    "particuliers), mais l'extraction reste heuristique et peut se tromper. Le "
    "robots.txt de chaque site est respecté : si l'accès automatisé y est interdit, la "
    "source est ignorée et seul le lien officiel reste affiché. L'extraction est une "
    "heuristique — elle échoue sur les listes publiées en image scannée, chargées en "
    "JavaScript, ou rédigées dans un alphabet non-latin ; dans ces cas, seul le lien "
    "officiel est affiché pour consultation manuelle."
)

SEARCH_TERMS = [
    "diplomatic list", "corps diplomatique", "liste diplomatique",
    "foreign missions", "heads of mission", "ambassadeur",
]


def load_json(path: Path, default):
    if not path.exists():
        print(f"  [avertissement] {path} n'existe pas, valeur par défaut utilisée.")
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def diplomat_view(entry: dict) -> dict:
    return {
        "name": entry.get("name"),
        "title": entry.get("title"),
        "role": entry.get("role", "ambassador"),
        "start_date": entry.get("start_date"),
        "end_date": entry.get("end_date"),
        "data_source": entry.get("data_source"),
        "source_url": entry.get("source_url"),
        "email": entry.get("email"),
        "phone": entry.get("phone"),
    }


def org_delegate_view(entry: dict) -> dict:
    return {
        "name": entry.get("name"),
        "title": entry.get("title"),
        "country_source": entry.get("country_source"),
        "delegate_type": entry.get("delegate_type", "member_state"),
        "start_date": entry.get("start_date"),
        "end_date": entry.get("end_date"),
        "source_url": entry.get("source_url"),
        "email": entry.get("email"),
        "phone": entry.get("phone"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="diplomats/data/sources_combined.json")
    ap.add_argument("--merged", default="diplomats/data/diplomats_merged.json")
    ap.add_argument("--org", default="diplomats/data/org_delegations.json")
    ap.add_argument("--out", default="site/contact.json")
    args = ap.parse_args()

    sources = load_json(Path(args.sources), [])
    merged = load_json(Path(args.merged), [])
    org_entries = load_json(Path(args.org), [])

    by_country: dict[str, list[dict]] = defaultdict(list)
    for entry in merged:
        by_country[entry.get("country_source", "")].append(entry)

    regions: dict[str, list[dict]] = defaultdict(list)
    for src in sources:
        country = src["country"]
        region = src["region"]
        diplomats = sorted(
            by_country.get(country, []),
            key=lambda e: (e.get("start_date") or "", e.get("name") or ""),
            reverse=True,
        )
        regions[region].append(
            {
                "country": country,
                "url": src["url"],
                "diplomats": [diplomat_view(d) for d in diplomats],
            }
        )

    international_organizations: dict[str, list[dict]] = defaultdict(list)
    for entry in org_entries:
        international_organizations[entry.get("organization", "Autre")].append(
            org_delegate_view(entry)
        )
    for org_name, delegates in international_organizations.items():
        delegates.sort(key=lambda e: (e.get("country_source") or "", e.get("name") or ""))

    notes = [
        "Toutes les données proviennent de publications officielles des États et "
        "organisations internationales (protocole diplomatique) — rien n'est collecté "
        "depuis une source privée ou non publique.",
        "Chaque entrée indique sa provenance (site officiel ou organisation "
        "internationale) et, quand connue, sa date de prise de fonction.",
        "Les consuls honoraires sont distingués des ambassadeurs quand la source le "
        "permet (voir l'étiquette sur chaque entrée) ; leurs coordonnées, quand connues, "
        "sont plus susceptibles d'être personnelles (ce sont souvent des particuliers) "
        "que celles des ambassadeurs.",
        "Ces données sont réactualisées automatiquement chaque semaine — une entrée "
        "obsolète est remplacée ou retirée au prochain passage.",
    ]

    out = {
        "generated": date.today().isoformat(),
        "regions": regions,
        "international_organizations": international_organizations,
        "methodology": METHODOLOGY,
        "notes": notes,
        "search_terms": SEARCH_TERMS,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    n_countries = sum(len(v) for v in regions.values())
    n_diplomats = sum(len(c["diplomats"]) for v in regions.values() for c in v)
    n_org_delegates = sum(len(v) for v in international_organizations.values())
    print(
        f"Écrit {out_path} : {n_countries} pays, {n_diplomats} diplomate(s) bilatéraux, "
        f"{n_org_delegates} délégué(s) auprès d'organisations internationales."
    )


if __name__ == "__main__":
    main()
