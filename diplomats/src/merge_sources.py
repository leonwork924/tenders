"""
merge_sources.py — Fusionne le scraping direct et la source Wikidata
=====================================================================

Combine la sortie de `agent.py` (scraping des sites officiels) et celle de
`wikidata_source.py` en un seul fichier `data/diplomats.json`, en tentant de
rapprocher les entrées qui désignent la même personne pour lui associer une
date de prise de fonction quand le scraping seul n'en a pas trouvé.

Le rapprochement est une HEURISTIQUE simple (nom normalisé identique + même
pays source) : elle peut rater des correspondances (variantes d'orthographe,
ordre prénom/nom différent, translittérations) ou, plus rarement, en créer
de fausses (homonymes). Elle est volontairement prudente : en cas de doute,
les deux entrées sont conservées séparément plutôt que fusionnées à tort.

Usage:
    python src/merge_sources.py \
        --scraped data/diplomats.json \
        --wikidata data/diplomats_wikidata.json \
        --out data/diplomats_merged.json
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path


def normalize_name(name: str) -> str:
    """Normalise un nom pour comparaison : minuscules, sans accents, sans
    ponctuation, espaces compressés. Pas parfait (voir limites en tête de
    fichier) mais suffisant pour un rapprochement conservateur."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    name = "".join(c if c.isalnum() or c.isspace() else " " for c in name)
    return " ".join(name.split())


def load_entries(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  [avertissement] {path} n'existe pas, ignoré.")
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def merge(scraped: list[dict], wikidata: list[dict]) -> list[dict]:
    # Index des entrées Wikidata par (pays normalisé, nom normalisé)
    wd_index: dict[tuple[str, str], list[dict]] = {}
    for wd_entry in wikidata:
        key = (
            normalize_name(wd_entry.get("country_source", "")),
            normalize_name(wd_entry.get("name", "")),
        )
        wd_index.setdefault(key, []).append(wd_entry)

    matched_wd_ids = set()
    merged: list[dict] = []

    for entry in scraped:
        entry = dict(entry)  # copie défensive
        key = (
            normalize_name(entry.get("country_source", "")),
            normalize_name(entry.get("name", "")),
        )
        candidates = wd_index.get(key, [])
        if candidates:
            # On prend la correspondance la plus récente (déjà trié par
            # date de début décroissante côté wikidata_source.py) qui n'a
            # pas déjà servi pour une autre entrée scrapée.
            match = next(
                (c for c in candidates if id(c) not in matched_wd_ids), None
            )
            if match:
                matched_wd_ids.add(id(match))
                if not entry.get("start_date"):
                    entry["start_date"] = match.get("start_date")
                if not entry.get("end_date"):
                    entry["end_date"] = match.get("end_date")
                entry["wikidata_url"] = match.get("source_url")
        merged.append(entry)

    # Ajoute les entrées Wikidata qui n'ont trouvé aucune correspondance
    # dans le scraping — signalées comme telles (data_source="wikidata"),
    # utile pour les pays où le site officiel n'a pas pu être extrait.
    for wd_entry in wikidata:
        if id(wd_entry) not in matched_wd_ids:
            merged.append(dict(wd_entry))

    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scraped", default="data/diplomats.json")
    parser.add_argument("--wikidata", default="data/diplomats_wikidata.json")
    parser.add_argument("--out", default="data/diplomats_merged.json")
    args = parser.parse_args()

    scraped = load_entries(Path(args.scraped))
    wikidata = load_entries(Path(args.wikidata))

    merged = merge(scraped, wikidata)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    n_with_date = sum(1 for e in merged if e.get("start_date"))
    print(
        f"Terminé : {len(merged)} entrées écrites dans {out_path} "
        f"({len(scraped)} scrapées + {len(wikidata) - (len(merged) - len(scraped))} "
        f"reprises de Wikidata sans équivalent scrapé ; {n_with_date} avec une date "
        f"de prise de fonction)."
    )


if __name__ == "__main__":
    main()
