#!/usr/bin/env python3
"""Check the keyword taxonomy against labelled example notices.

    python tools/calibrate.py              # use min_score from config.yaml
    python tools/calibrate.py --threshold 12
    python tools/calibrate.py --sweep      # try a range and report the best

Add your own real notices to CASES as you see them — the ones you won, and the
false positives that annoyed you. This is the cheapest way to keep the keyword
list honest, and it runs entirely offline.

Labels: HIT must score at or above the threshold, MISS must score below it.
EDGE cases are printed but never counted as failures; they are the notices you
could argue either way, and they show you what a threshold change would cost.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tender_radar.config import load_config          # noqa: E402
from tender_radar.models import Tender               # noqa: E402
from tender_radar.scoring import Scorer              # noqa: E402

CASES: list[tuple[str, str, str]] = [
    # --- should be shortlisted -------------------------------------------
    ("HIT", "Prestations de déménagement et transfert de mobilier des services municipaux",
     "Déménagement d'entreprise, emballage, manutention et garde-meuble."),
    ("HIT", "Records management and offsite document storage services",
     "Physical records storage, records retrieval, scan-on-demand and secure shredding."),
    ("HIT", "Numérisation des bandes magnétiques et archives audiovisuelles",
     "Migration de supports, sous-titrage, voix off, indexation documentaire."),
    ("HIT", "Fine art handling and transport for a touring exhibition",
     "Art packing, crating, courier of artworks, constat d'état."),
    ("HIT", "Digitisation of books and manuscripts for the national library",
     "High resolution scanning, imaging services, metadata and indexing."),
    ("HIT", "Supply and installation of FF&E for hotel refurbishment",
     "Custom furniture for hotels, delivery and installation, on-site assembly, "
     "warehousing and staging."),
    ("HIT", "Employee relocation and destination services",
     "Move management, visa and work permit support, home search."),
    ("HIT", "Digitalisierung von Archivbeständen der Stadtverwaltung",
     "Scannen und Archivierung, Mikroverfilmung."),
    ("HIT", "Conservation-restauration et numérisation 3D d'un monument historique",
     "Photogrammétrie, relevé 3D, conservation préventive."),
    ("HIT", "Restoration work on a heritage monument",
     "Physical conservation and restoration of a listed building."),

    # --- must not be shortlisted -----------------------------------------
    ("MISS", "Asbestos removal and waste removal works",
     "Removal of asbestos and refuse collection across three depots."),
    ("MISS", "Supply of office stationery", "Pens, paper, toner cartridges."),
    ("MISS", "Urban mobility plan consultancy",
     "Plan de mobilité urbaine et transport scolaire."),
    ("MISS", "Battery energy storage system installation",
     "Storage and installation of grid batteries, logistics included."),
    ("MISS", "Marché de restauration collective pour les écoles",
     "Service de restauration scolaire, livraison de repas."),
    ("MISS", "Patient transport services",
     "Ambulance and non-emergency patient transport."),
    ("MISS", "Cloud hosting and software licences",
     "Data migration to cloud storage, software license renewal."),
    ("MISS", "Provision of office cleaning",
     "Daily cleaning, window cleaning, waste removal."),
    ("MISS", "Construction of a new school building",
     "Civil works, groundworks and installation of fixtures and equipment."),

    # --- judgement calls --------------------------------------------------
    ("EDGE", "Warehousing and logistics services",
     "Storage, packing, handling and inventory management."),
    ("EDGE", "Furniture supply for council offices",
     "Delivery and installation of desks and shelving."),
]


def evaluate(scorer: Scorer, threshold: float, verbose: bool = True) -> tuple[int, int]:
    wrong = edges = 0
    for label, title, description in CASES:
        probe = Tender(source="calibrate", source_id="x", title=title, url="",
                       description=description)
        score, matched = scorer.score(probe)
        if label == "EDGE":
            verdict, edges = ("in" if score >= threshold else "out"), edges + 1
        elif (score >= threshold) == (label == "HIT"):
            verdict = "ok"
        else:
            verdict, wrong = "MISCLASSIFIED", wrong + 1
        if verbose:
            print(f"{score:7.1f}  {label:5} {verdict:14} {title[:58]}")
            if verdict == "MISCLASSIFIED":
                for part in matched.split(" | "):
                    print(f"{'':16}{part}")
    return wrong, edges


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("-c", "--config")
    args = ap.parse_args()

    config = load_config(args.config)
    scorer = Scorer(config)

    if args.sweep:
        print("threshold  misclassified  edge cases included")
        for th in [t / 2 for t in range(8, 41)]:
            wrong, _ = evaluate(scorer, th, verbose=False)
            included = sum(1 for label, ti, de in CASES if label == "EDGE"
                           and scorer.score(Tender(source="c", source_id="x", title=ti,
                                                   url="", description=de))[0] >= th)
            flag = "  <-- clean" if wrong == 0 else ""
            print(f"{th:9.1f}  {wrong:13}  {included:19}{flag}")
        return 0

    threshold = args.threshold if args.threshold is not None else float(
        config["run"]["min_score"])
    print(f"threshold {threshold:g}\n")
    wrong, edges = evaluate(scorer, threshold)
    print(f"\n{len(CASES) - edges} labelled cases, {wrong} misclassified, "
          f"{edges} edge cases")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
