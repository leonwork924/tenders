"""
letter_generator.py — Génère la lettre personnalisée par ambassadeur nommé
=============================================================================

Prend les nominations extraites par jorf_ambassadors.py et produit, pour
chacune, la lettre de félicitations d'après le modèle fourni par Dorina
Dama (Directrice Commerciale Corporate, AGS France) le 15/09/2026.

Ce que la donnée JORF permet de remplir de façon fiable :
- civilité et accord grammatical du destinataire (ambassadeur/ambassadrice
  -- lu directement dans le texte JORF, pas deviné à partir du prénom)
- la phrase "nomination en qualité d'Ambassadeur de France {posting_phrase}"
  -- {posting_phrase} est extrait tel quel du Journal Officiel, donc déjà
  grammaticalement correct (pas de table préposition/pays nécessaire ici)

Ce qui nécessite la table country_prepositions.py (best-effort, voir son
propre avertissement) :
- "Présent au Kenya..." / "Si votre propre mutation vers le Kenya..."
  -- ces deux tournures ont besoin du nom du pays SANS "République de/du"
  et de la préposition correcte, que le Journal Officiel ne donne jamais
  sous cette forme.

Si le pays n'est pas dans country_prepositions.py, la lettre est quand
même générée mais avec un [[À COMPLÉTER]] à la place des tournures
concernées, bien visible -- jamais une préposition devinée au hasard.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pycountry
from babel import Locale

from country_prepositions import COUNTRY_PREPOSITIONS

PLACEHOLDER = "[[À COMPLÉTER -- pays absent de country_prepositions.py]]"

TEMPLATE = """{salutation},
Permettez-moi de vous adresser mes sincères félicitations à l'occasion de votre nomination en qualité d'{title_de_france} {posting_phrase}.
À l'approche de cette nouvelle affectation, je souhaitais également me présenter. En tant que Directrice Commerciale d'AGS France, j'accompagne les administrations, institutions et collaborateurs en mobilité dans la gestion de leurs transferts internationaux.
Depuis plus de 50 ans, AGS met son expertise au service des expatriés, des organisations internationales et des personnels amenés à rejoindre de nouvelles fonctions à l'étranger. Nos équipes assurent un accompagnement complet couvrant le déménagement international, les formalités douanières, la coordination logistique ainsi que les aspects administratifs et réglementaires associés à chaque mobilité.
Présent {prep_form} à travers notre filiale locale et plus largement à travers un réseau dense de filiales en {region}, AGS accompagne chaque année de nombreux expatriés, diplomates et collaborateurs internationaux dans leurs projets de mobilité. Cette présence locale nous permet d'offrir un accompagnement de proximité, parfaitement adapté aux spécificités du pays et aux exigences des affectations internationales.
Si votre propre mutation {vers_form} devait nécessiter un accompagnement particulier, je serais naturellement ravie d'échanger avec vous et de vous présenter les solutions que nos équipes peuvent mettre à votre disposition.
Je vous souhaite pleine réussite dans l'exercice de vos nouvelles fonctions et vous prie d'agréer, {salutation}, l'expression de ma haute considération.
Bien cordialement,"""


def _build_country_name_map() -> dict[str, str]:
    """Variantes FR/EN de nom de pays -> nom canonique anglais (pour
    retrouver la région dans site/regions.json, qui est indexé en anglais).
    Même construction que dans diplomats/src/agent.py -- dupliquée ici
    plutôt qu'importée pour garder ce dossier autonome."""
    entries: dict[str, str] = {}
    for c in pycountry.countries:
        entries[c.name.lower()] = c.name
        if hasattr(c, "common_name"):
            entries[c.common_name.lower()] = c.name
    loc = Locale("fr")
    for c in pycountry.countries:
        fr_name = loc.territories.get(c.alpha_2)
        if fr_name:
            entries[fr_name.lower()] = c.name
    return entries


COUNTRY_NAME_MAP = _build_country_name_map()


def _region_for(country_fr: str, regions: dict[str, str]) -> str | None:
    # "Sources complémentaires" est une catégorie technique de regions.json
    # (pour le regroupement de l'onglet Contact), pas un continent -- inutile
    # à mentionner dans une lettre. On la traite comme une non-réponse.
    SAFE_REGIONS = {"Europe", "Afrique", "Asie", "Moyen-Orient", "Amériques", "Caraïbes / Territoires d'outre-mer"}

    candidate = regions.get(country_fr)
    if not candidate:
        canonical = COUNTRY_NAME_MAP.get(country_fr.lower())
        candidate = regions.get(canonical) if canonical else None

    return candidate if candidate in SAFE_REGIONS else None


def _vers_form(prep: str, bare_name: str) -> str:
    article = {"au": "le", "en": "la", "aux": "les", "à": ""}.get(prep, "")
    return f"vers {article} {bare_name}".replace("  ", " ").strip()


@dataclass
class GeneratedLetter:
    name: str
    country: str
    nomination_date: str
    jorf_url: str
    letter: str
    needs_review: bool


def generate_letter(nomination: dict, regions: dict[str, str]) -> GeneratedLetter:
    gender = nomination["gender"]
    salutation = "Monsieur l'Ambassadeur" if gender == "M" else "Madame l'Ambassadrice"
    title_de_france = "Ambassadeur de France" if gender == "M" else "Ambassadrice de France"

    country = nomination["country"]
    entry = COUNTRY_PREPOSITIONS.get(country)
    needs_review = entry is None
    if entry:
        prep, bare_name = entry
        prep_form = f"{prep} {bare_name}"
        vers_form = _vers_form(prep, bare_name)
    else:
        prep_form = PLACEHOLDER
        vers_form = PLACEHOLDER

    region = _region_for(country, regions)
    if region is None:
        region = PLACEHOLDER
        needs_review = True

    letter = TEMPLATE.format(
        salutation=salutation,
        title_de_france=title_de_france,
        posting_phrase=nomination["posting_phrase"],
        prep_form=prep_form,
        region=region,
        vers_form=vers_form,
    )

    return GeneratedLetter(
        name=nomination["name"],
        country=country,
        nomination_date=nomination["nomination_date"],
        jorf_url=nomination["jorf_url"],
        letter=letter,
        needs_review=needs_review,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nominations", default="france/data/ambassadeurs.json")
    parser.add_argument("--regions", default="../site/regions.json")
    parser.add_argument("--out", default="site/france_ambassadeurs.json")
    args = parser.parse_args()

    nominations = json.loads(Path(args.nominations).read_text(encoding="utf-8"))
    regions = json.loads(Path(args.regions).read_text(encoding="utf-8"))

    letters = [generate_letter(n, regions) for n in nominations]
    n_review = sum(1 for l in letters if l.needs_review)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "count": len(letters),
        "needs_review_count": n_review,
        "ambassadors": [vars(l) for l in letters],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(letters)} lettre(s) écrite(s) dans {out_path} ({n_review} à relire -- pays hors table).")


if __name__ == "__main__":
    main()
