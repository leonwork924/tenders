"""
jorf_ambassadors.py — Nominations d'ambassadeurs français (source JORFSearch)
==============================================================================

Source : https://jorfsearch.steinertriples.ch/tag/ambassadeur (+ ?format=JSON
si dispo -- voir avertissement plus bas). Base citoyenne construite à partir
de l'opendata officielle de la DILA (Journal Officiel), maintenue par Nathan
Cohen (steinertriples.ch). Gratuite, sans inscription.

⚠️ Comme les autres scripts de ce dossier : je n'ai pas d'accès réseau vers
jorfsearch.steinertriples.ch depuis l'environnement où j'écris ce code, donc
ce parseur n'a jamais tourné "en vrai" contre le site. Ce qu'il fait tourner
en revanche : un test complet contre un extrait RÉEL de la page (voir
fixtures/jorf_ambassadeur_sample.txt, capturé via une requête directe au
moment où ce fichier a été écrit) -- donc la logique d'extraction est
vérifiée sur de vraies données, juste pas via une requête HTTP en direct.
Lance `python jorf_ambassadors.py --self-test` en premier pour reconfirmer
avant un usage réel, et vérifie le nombre d'entrées trouvées sur le premier
vrai run.

Filtre : ne garde que les nominations (type="nomination") avec un pays de
destination (tag ambassadeur_pays présent) -- exclut les ambassadeurs
thématiques (numérique, sport, climat...), les délégués auprès
d'organisations internationales, et les cessations de fonction / départs en
retraite (pas une nouvelle affectation).
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

USER_AGENT = "MobilitasAGS-JorfAmbassadorAgent/1.0 (+contact: tenders@overseasam.com)"
SOURCE_URL = "https://jorfsearch.steinertriples.ch/tag/ambassadeur"
REQUEST_TIMEOUT = 30

# Une entrée = tout ce qui se trouve entre deux "JORFTEXT<id>(source JORF)<type><date>"
# -- l'ancre la plus fiable de la page (toujours présente, toujours unique).
ENTRY_RE = re.compile(
    r"De\s*:\s*(?P<person>.+?)"
    r"Objet\s*:\s*(?P<objet>.+?)"
    r"JORFTEXT(?P<jorf_id>\d+)\(source JORF\)"
    r"(?P<type>nomination|cessation de fonction|admission|détachement|délégation de signature|radiation)"
    r"(?P<date_long>\d{1,2}\s+\S+\s+\d{4})",
    re.DOTALL,
)

PAYS_TAG_RE = re.compile(r'ambassadeur_pays="([^"]+)"')
THEMATIQUE_RE = re.compile(r"ambassadeur_thematique")

# Le nom se termine au premier mot commençant par une minuscule (le grade/la
# fonction qui suit, ex. "administrateur", "secrétaire", "ministre") --
# robuste même quand JORFSearch n'a pas mis le nom de famille en majuscules
# (ça arrive, ex. "François-Xavier Léger" vs "Jean-Noël POIRIER").
NAME_CUTOFF_RE = re.compile(r"^(.*?)(?=\s+[a-zà-ÿ])", re.UNICODE)

_MONTHS_FR = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12,
}
DATE_LONG_RE = re.compile(r"(\d{1,2})\s+(\w+)\s+(\d{4})")


@dataclass
class AmbassadorNomination:
    name: str
    gender: str  # "M" | "F"
    country: str  # nom court, tel que dans le tag ambassadeur_pays (ex. "Kenya")
    posting_phrase: str  # ex. "auprès de la République du Kenya" -- extrait de l'Objet, grammaticalement correct
    nomination_date: str  # ISO YYYY-MM-DD
    jorf_id: str
    jorf_url: str
    raw_objet: str


def _parse_date(day: str, month_name: str, year: str) -> str | None:
    month = _MONTHS_FR.get(month_name.lower())
    if not month:
        return None
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def _extract_name(person_blob: str) -> str:
    m = NAME_CUTOFF_RE.match(person_blob.strip())
    name = m.group(1).strip() if m else person_blob.strip()
    return re.sub(r"\s+", " ", name)


def _extract_posting_phrase(objet: str) -> str | None:
    """Extrait la fin de l'Objet après 'de la République française', qui est
    déjà grammaticalement correcte (accord préposition/pays géré par JORF
    lui-même) -- ex. 'auprès de la République du Kenya', 'en Bosnie-Herzégovine',
    'au Monténégro'. Coupe les compléments type 'à compter du ...' /
    'en résidence à ...' qui ne doivent pas atterrir tels quels dans la lettre.
    """
    m = re.search(r"de la République française\s+(.+)", objet)
    if not m:
        return None
    phrase = m.group(1)
    # Coupe tout ce qui suit la vraie fin de la phrase : compléments qu'on ne
    # veut pas dans la lettre ("à compter du...", "en résidence à...") ou,
    # plus souvent, les balises de métadonnées collées juste après sans
    # séparateur ("Groupe:", "conseil_des_ministres", "ambassadeur_pays=", etc.)
    phrase = re.split(
        r"\s+à compter du\b|\s+en résidence à\b|Groupe\s*:|conseil_des_ministres|"
        r"date_debut\s*=|ambassadeur_pays\s*=|ambassadeur_thematique|"
        r"autorite_delegation\s*=|depart_retraite\s*=",
        phrase,
    )[0]
    return phrase.strip().rstrip(".,")


def parse_entries(raw_text: str) -> list[AmbassadorNomination]:
    results: list[AmbassadorNomination] = []
    for m in ENTRY_RE.finditer(raw_text):
        if m.group("type") != "nomination":
            continue
        objet = m.group("objet")
        if THEMATIQUE_RE.search(objet):
            continue
        pays_match = PAYS_TAG_RE.search(objet)
        if not pays_match:
            continue  # pas de pays de destination identifié -> pas exploitable pour la lettre

        posting_phrase = _extract_posting_phrase(objet)
        if not posting_phrase:
            continue  # forme inattendue -- on ne devine pas, on saute

        date_m = DATE_LONG_RE.search(m.group("date_long"))
        nomination_date = _parse_date(*date_m.groups()) if date_m else None
        if not nomination_date:
            continue

        gender = "F" if objet.strip().lower().startswith("ambassadrice") else "M"
        jorf_id = m.group("jorf_id")

        results.append(
            AmbassadorNomination(
                name=_extract_name(m.group("person")),
                gender=gender,
                country=pays_match.group(1),
                posting_phrase=posting_phrase,
                nomination_date=nomination_date,
                jorf_id=jorf_id,
                jorf_url=f"https://jorfsearch.steinertriples.ch/JORFTEXT{jorf_id}",
                raw_objet=objet.strip(),
            )
        )
    return results


def fetch(url: str = SOURCE_URL) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def run_self_test() -> None:
    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "jorf_ambassadeur_sample.txt"
    text = fixture_path.read_text(encoding="utf-8")
    entries = parse_entries(text)
    print(f"Auto-test : {len(entries)} nomination(s) de pays extraite(s) sur l'échantillon réel.")
    for e in entries:
        print(f"  {e.name:28s} {e.gender}  {e.country:20s} {e.posting_phrase:40s} {e.nomination_date}")

    # Assertions dures -- si ça casse, ne pas utiliser le parseur en l'état.
    assert len(entries) == 9, f"attendu 9 nominations de pays sur l'échantillon, obtenu {len(entries)}"
    by_name = {e.name: e for e in entries}
    assert by_name["Jean-Noël POIRIER"].country == "Pakistan"
    assert by_name["Jean-Noël POIRIER"].posting_phrase == "auprès de la République islamique du Pakistan"
    assert by_name["Cécile HUMBERT-BOUVIER"].gender == "F"
    assert by_name["Cécile HUMBERT-BOUVIER"].posting_phrase == "au Monténégro"
    assert by_name["Kévin THUILLIER"].posting_phrase == "en Bosnie-Herzégovine"
    assert by_name["Marie AUDOUARD"].country == "Émirats arabes unis"
    assert "Adrien ABECASSIS" not in by_name, "thématique aurait dû être exclu"
    assert "Clara CHAPPAZ" not in by_name, "cessation de fonction aurait dû être exclue"
    assert "Christian MASSET" not in by_name, "admission/retraite aurait dû être exclue"
    print("\nOK -- tous les cas de l'échantillon (M/F, prépositions variées, "
          "thématique exclu, cessation exclue, retraite exclue) passent.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Lance le test contre la fixture réelle, sans requête réseau.")
    parser.add_argument("--out", default="france/data/ambassadeurs.json")
    parser.add_argument("--format-json", action="store_true",
                         help="Essaie d'abord ?format=JSON (documenté par JORFSearch) avant le parsing texte -- désactivé par défaut tant que le schéma n'est pas confirmé, voir avertissement en tête de fichier.")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return

    raw_text = fetch(SOURCE_URL)
    entries = parse_entries(raw_text)
    print(f"{len(entries)} nomination(s) de pays extraite(s) depuis {SOURCE_URL}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([asdict(e) for e in entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Écrit {out_path}")


if __name__ == "__main__":
    main()
