"""
export_by_country.py — Découpe corporate_contacts.db en petits fichiers JSON
par pays (paginés au-delà d'un certain volume), pour rendre la base COMPLÈTE
consultable depuis le site -- contrairement à export_stats.py qui ne produit
que des statistiques agrégées.

Écrit :
    site/corporate/index.json                  -- liste des pays + nb de pages
    site/corporate/<jurisdiction>_<page>.json   -- un lot de PAGE_SIZE entreprises

Usage:
    python corporate/src/export_by_country.py \
        --db corporate_contacts.db \
        --out-dir site/corporate
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

PAGE_SIZE = 50_000  # ~7-10 Mo par fichier avec ces champs -- large marge sous la limite git de 100 Mo/fichier


def company_row(row: tuple) -> dict:
    legal_name, lei, hq_city, hq_state, hq_country, company_number = row
    return {
        "n": legal_name,
        "lei": lei,
        "city": hq_city,
        "state": hq_state,
        "country": hq_country,
        "reg": company_number,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="corporate_contacts.db")
    ap.add_argument("--out-dir", default="site/corporate")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    db_path = Path(args.db)

    # On repart de zéro à chaque run : le nombre de pages par pays peut changer
    # d'une semaine à l'autre, il ne faut pas laisser de vieux fichiers orphelins.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        print(f"  [avertissement] {db_path} n'existe pas -- index vide écrit.")
        (out_dir / "index.json").write_text(json.dumps({"jurisdictions": []}, ensure_ascii=False), encoding="utf-8")
        return

    conn = sqlite3.connect(str(db_path))

    jurisdictions = [
        row[0] for row in conn.execute(
            "SELECT DISTINCT jurisdiction FROM companies WHERE jurisdiction IS NOT NULL ORDER BY jurisdiction"
        ).fetchall()
    ]

    index = []
    total_written = 0

    for j in jurisdictions:
        count = conn.execute(
            "SELECT COUNT(*) FROM companies WHERE jurisdiction=?", (j,)
        ).fetchone()[0]
        n_pages = (count + PAGE_SIZE - 1) // PAGE_SIZE

        for page in range(n_pages):
            rows = conn.execute(
                "SELECT legal_name, lei, hq_city, hq_state, hq_country, company_number "
                "FROM companies WHERE jurisdiction=? ORDER BY legal_name LIMIT ? OFFSET ?",
                (j, PAGE_SIZE, page * PAGE_SIZE),
            ).fetchall()
            companies = [company_row(r) for r in rows]
            fname = f"{j}_{page}.json"
            (out_dir / fname).write_text(
                json.dumps({"jurisdiction": j, "page": page, "companies": companies}, ensure_ascii=False),
                encoding="utf-8",
            )
            total_written += len(companies)

        index.append({"jurisdiction": j, "count": count, "pages": n_pages})
        print(f"  {j}: {count:,} entreprise(s), {n_pages} page(s)")

    conn.close()

    (out_dir / "index.json").write_text(
        json.dumps({"jurisdictions": index, "page_size": PAGE_SIZE}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Terminé : {len(jurisdictions)} pays, {total_written:,} entreprise(s) écrite(s) dans {out_dir}/")


if __name__ == "__main__":
    main()
