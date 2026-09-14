#!/usr/bin/env python3
"""
Export corporate_contacts.db (companies + contacts, produced by
agent_corporate_contacts.py) into site/corporate_contacts.json, consumed by
the "Corporate" tab on the Contact page.

Usage:
    python export_site.py --db corporate/data/corporate_contacts.db --out site/corporate_contacts.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Only pull columns we know how to display; guards against schema drift if
# the source script evolves (new v9+ columns just won't show up yet).
COMPANY_COLUMNS = [
    "company_id", "legal_name", "lei", "jurisdiction", "primary_domain",
    "company_number", "source_name", "source_url", "retrieved_at",
    "registry_phone", "registry_email", "registry_website",
    "hq_city", "hq_state", "hq_country",
]

CONTACT_COLUMNS = [
    "contact_id", "company_id", "full_name", "job_title", "email", "phone",
    "email_confidence_score", "email_verification_status", "linkedin_url",
    "collected_at",
]


def existing_columns(conn: sqlite3.Connection, table: str, wanted: list[str]) -> list[str]:
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    return [c for c in wanted if c in have]


def export(db_path: str, out_path: str) -> None:
    db_file = Path(db_path)
    if not db_file.exists():
        # First-ever run before any company has been researched yet: write an
        # empty payload rather than failing, so the site shows a clean
        # "no data yet" state instead of erroring.
        payload = {"generated": datetime.now(timezone.utc).isoformat(), "count": 0, "companies": []}
        Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"No DB at {db_path} yet — wrote empty {out_path}")
        return

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    company_cols = existing_columns(conn, "companies", COMPANY_COLUMNS)
    contact_cols = existing_columns(conn, "contacts", CONTACT_COLUMNS)

    companies: dict[str, dict] = {}
    for row in conn.execute(
        f"SELECT {', '.join(company_cols)} FROM companies ORDER BY retrieved_at DESC"
    ):
        d = dict(row)
        d["contacts"] = []
        companies[d["company_id"]] = d

    if contact_cols and "company_id" in contact_cols:
        for row in conn.execute(
            f"SELECT {', '.join(contact_cols)} FROM contacts ORDER BY collected_at DESC"
        ):
            d = dict(row)
            cid = d.pop("company_id", None)
            if cid in companies:
                companies[cid]["contacts"].append(d)

    conn.close()

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "count": len(companies),
        "companies": list(companies.values()),
    }

    Path(out_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Exported {len(companies)} compan{'y' if len(companies) == 1 else 'ies'} -> {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="corporate/data/corporate_contacts.db")
    parser.add_argument("--out", default="site/corporate_contacts.json")
    args = parser.parse_args()
    export(args.db, args.out)
