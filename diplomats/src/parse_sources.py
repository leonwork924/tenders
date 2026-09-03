"""
parse_sources.py
-----------------
Convertit le fichier texte "MOBILITAS_AGS_Diplomatic_Sources" en une liste
structurée JSON (pays, région, URL) exploitable par l'agent de scraping.

Usage:
    python parse_sources.py data/sources_raw.txt data/sources.json
"""

import json
import re
import sys
from pathlib import Path

REGION_HEADERS = {
    "EUROPE": "Europe",
    "AFRICA — 54 COUNTRIES": "Afrique",
    "ASIA": "Asie",
    "MIDDLE EAST": "Moyen-Orient",
    "CARIBBEAN / OVERSEAS TERRITORIES": "Caraïbes / Territoires d'outre-mer",
    "ADDITIONAL HIGH-VALUE SOURCES": "Sources complémentaires",
}

# Lignes à ignorer complètement (notes, entrées supprimées, etc.)
SKIP_PATTERNS = [
    r"^\[SUPPRIME\]",
    r"^\[NOTE",
    r"^\[ATTENTION",
    r"^---",
    r"^===",
    r"^Purpose:",
    r"^MOBILITAS",
    r"^Corrections et",
    r"^Statut par",
    r"^tel quel",
]

URL_RE = re.compile(r"https?://\S+")


def clean_country_name(raw: str) -> str:
    raw = raw.split("[VERIFIE")[0]
    raw = raw.split("—")[0] if "—" not in raw[:3] else raw
    return raw.strip(" -—")


def parse(path_in: Path):
    lines = path_in.read_text(encoding="utf-8", errors="ignore").splitlines()

    current_region = "Non classé"
    entries = []
    pending_country = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if any(re.match(p, line) for p in SKIP_PATTERNS):
            continue

        # Détection d'un changement de région (en-tête suivi d'une ligne de tirets)
        header_key = line.upper()
        for key in REGION_HEADERS:
            if header_key.startswith(key):
                current_region = REGION_HEADERS[key]
                pending_country = None
                break

        url_match = URL_RE.search(line)
        if url_match:
            url = url_match.group(0).rstrip(").,")
            # Le nom du pays est soit avant le tiret sur la même ligne,
            # soit sur la ligne précédente (format "Pays — description\nURL")
            before_url = line[: url_match.start()].strip(" -—")
            if before_url and "—" not in before_url and len(before_url) < 60:
                country = clean_country_name(before_url)
            elif pending_country:
                country = pending_country
            else:
                country = "Inconnu"

            entries.append(
                {
                    "country": country,
                    "region": current_region,
                    "url": url,
                }
            )
            pending_country = None
            continue

        # Ligne "Pays — Description" sans URL sur la même ligne : on la retient
        # pour l'associer à l'URL qui suit sur la ligne d'après.
        if "—" in line and not line.startswith("["):
            pending_country = clean_country_name(line.split("—")[0])

    return current_region, entries


def main():
    if len(sys.argv) != 3:
        print("Usage: python parse_sources.py <input.txt> <output.json>")
        sys.exit(1)

    path_in = Path(sys.argv[1])
    path_out = Path(sys.argv[2])

    _, entries = parse(path_in)

    # Déduplique (même pays + même URL)
    seen = set()
    deduped = []
    for e in entries:
        key = (e["country"], e["url"])
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    path_out.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(deduped)} sources écrites dans {path_out}")


if __name__ == "__main__":
    main()
