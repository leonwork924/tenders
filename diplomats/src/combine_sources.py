"""
combine_sources.py — Combine sources.json (117 MAE) et consular_sources_seed.json
(sources dédiées aux consuls honoraires) en une seule liste, pour n'avoir
qu'une seule passe d'agent.py à faire tourner (au lieu de deux).

Usage:
    python diplomats/src/combine_sources.py \
        --sources diplomats/data/sources.json \
        --consular diplomats/data/consular_sources_seed.json \
        --out diplomats/data/sources_combined.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="diplomats/data/sources.json")
    ap.add_argument("--consular", default="diplomats/data/consular_sources_seed.json")
    ap.add_argument("--out", default="diplomats/data/sources_combined.json")
    args = ap.parse_args()

    sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    consular = json.loads(Path(args.consular).read_text(encoding="utf-8"))

    combined = sources + consular

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Écrit {out_path} : {len(sources)} sources MAE + {len(consular)} sources consulaires = {len(combined)} au total.")


if __name__ == "__main__":
    main()
