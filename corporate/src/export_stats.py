"""
export_stats.py — Résume corporate_contacts.db (potentiellement des millions de
lignes après un run GLEIF global) en un petit JSON agrégé, consommable par le
site. La base brute complète n'est PAS publiée ici (voir workflow : elle part
en artefact GitHub Actions téléchargeable, trop volumineuse pour git/le site).

Usage:
    python corporate/src/export_stats.py \
        --db corporate_contacts.db \
        --out site/corporate_contacts.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="corporate_contacts.db")
    ap.add_argument("--out", default="site/corporate_contacts.json")
    ap.add_argument("--top-countries", type=int, default=200)  # ~200 = tous les pays/juridictions existants
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"  [avertissement] {db_path} n'existe pas -- base vide utilisée.")
        out = {
            "generated": date.today().isoformat(),
            "total_companies": 0,
            "by_jurisdiction": [],
            "by_source": [],
            "sample": [],
        }
    else:
        conn = sqlite3.connect(str(db_path))
        total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

        by_jurisdiction = [
            {"jurisdiction": row[0] or "(inconnue)", "count": row[1]}
            for row in conn.execute(
                "SELECT jurisdiction, COUNT(*) n FROM companies "
                "GROUP BY jurisdiction ORDER BY n DESC LIMIT ?",
                (args.top_countries,),
            ).fetchall()
        ]

        by_source = [
            {"source": row[0] or "(inconnue)", "count": row[1]}
            for row in conn.execute(
                "SELECT source_name, COUNT(*) n FROM companies "
                "GROUP BY source_name ORDER BY n DESC"
            ).fetchall()
        ]

        # Petit échantillon (pas un dump complet) pour donner une idée concrète
        # du contenu sans essayer de charger des millions de lignes sur le site.
        sample = [
            {
                "legal_name": row[0],
                "jurisdiction": row[1],
                "lei": row[2],
                "hq_city": row[3],
                "hq_country": row[4],
                "source_url": row[5],
            }
            for row in conn.execute(
                "SELECT legal_name, jurisdiction, lei, hq_city, hq_country, source_url "
                "FROM companies WHERE legal_name IS NOT NULL "
                "ORDER BY RANDOM() LIMIT 40"
            ).fetchall()
        ]

        n_runs = conn.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0]
        last_run = conn.execute(
            "SELECT started_at, finished_at, records_seen, records_imported, status "
            "FROM discovery_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        conn.close()

        out = {
            "generated": date.today().isoformat(),
            "total_companies": total,
            "by_jurisdiction": by_jurisdiction,
            "by_source": by_source,
            "sample": sample,
            "last_run": {
                "started_at": last_run[0],
                "finished_at": last_run[1],
                "records_seen": last_run[2],
                "records_imported": last_run[3],
                "status": last_run[4],
            } if last_run else None,
            "total_runs": n_runs,
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Écrit {out_path} : {out['total_companies']:,} entreprise(s) au total.")


if __name__ == "__main__":
    main()
