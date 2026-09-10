"""
merge_sources.py — Assemble le scraping direct et les délégations internationales
=====================================================================================

Combine la sortie de `agent.py` (scraping des sites officiels) et celle de
`org_delegations.py` (délégations ONU/UA/OSCE) en un seul fichier
`data/diplomats_merged.json`. Simple concaténation : ce sont deux univers
distincts (ambassades bilatérales vs délégations auprès d'organisations),
pas de rapprochement à faire entre les deux.

Usage:
    python src/merge_sources.py \
        --scraped data/diplomats.json \
        --org data/org_delegations.json \
        --out data/diplomats_merged.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_entries(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  [avertissement] {path} n'existe pas, ignoré.")
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scraped", default="data/diplomats.json")
    parser.add_argument("--org", default=None,
                         help="Sortie de org_delegations.py (ONU/UA/OSCE), simplement concaténée : "
                              "univers distinct (délégations auprès d'organisations, pas d'ambassades "
                              "bilatérales), pas de rapprochement par nom avec le reste.")
    parser.add_argument("--out", default="data/diplomats_merged.json")
    args = parser.parse_args()

    scraped = load_entries(Path(args.scraped))
    merged = [dict(e) for e in scraped]  # copie défensive

    n_org = 0
    if args.org:
        org_entries = load_entries(Path(args.org))
        merged.extend(org_entries)
        n_org = len(org_entries)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    n_with_date = sum(1 for e in merged if e.get("start_date"))
    print(
        f"Terminé : {len(merged)} entrées écrites dans {out_path} "
        f"({len(scraped)} scrapées + {n_org} délégations internationales ; "
        f"{n_with_date} avec une date de prise de fonction)."
    )


if __name__ == "__main__":
    main()
