"""notify.py -- Alerte par e-mail quand un NOUVEAU tender intéressant arrive.

Lit data/tenders.sqlite3 (rempli par `python run.py fetch`), repère les tenders
qui n'ont jamais été signalés, les rattache aux branches du pays, et met un e-mail
par tender dans alerts/data/outbox.json. Rien n'est envoyé ici : tools/create_outlook_drafts.ps1
fabrique les brouillons Outlook, tu relis et tu envoies toi-même.

MODE ACTUEL : phase de test, destinataire = Leon seul (le script PowerShell met ton adresse).
Les branches concernées sont listées dans le corps pour que tu voies à qui ça irait.

Premier run (pas de notified.json) : on enregistre tout ce qui existe déjà comme « déjà vu »
SANS envoyer d'alerte (sinon ~170 e-mails d'un coup). Seuls les tenders arrivés ensuite sont signalés.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
DEFAULT_MIN_SCORE = 15.0
MAX_INDIVIDUAL = 10        # au-delà : un seul e-mail récapitulatif pour le reste
KEEP_DAYS = 30
MAX_BRANCHES_SHOWN = 12

ALPHA3_FIX = {"ENG": "GB", "SCT": "GB", "WLS": "GB", "NIR": "GB", "UK": "GB", "UNI": ""}


def to_alpha2(code: str) -> str:
    """Code pays des sources (ISO2, ISO3 ou code maison) -> ISO2, '' si inconnu."""
    code = (code or "").strip().upper()
    if not code:
        return ""
    if code in ALPHA3_FIX:
        return ALPHA3_FIX[code]
    try:
        import pycountry
        if len(code) == 2:
            c = pycountry.countries.get(alpha_2=code)
        elif len(code) == 3:
            c = pycountry.countries.get(alpha_3=code)
        else:
            c = None
        return c.alpha_2 if c else ""
    except Exception:
        return code if len(code) == 2 else ""


def country_name(code: str) -> str:
    try:
        import pycountry
        a2 = to_alpha2(code)
        c = pycountry.countries.get(alpha_2=a2) if a2 else None
        return c.name if c else (code or "unknown")
    except Exception:
        return code or "unknown"


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def branches_for(country_code: str, table: dict) -> list[dict]:
    a2 = to_alpha2(country_code)
    brs = table.get("countries", {}).get(a2, []) if a2 else []
    return sorted(brs, key=lambda b: (not b["branch"].upper().startswith(("AGS", "MOBILITAS", "LASER", "OMM", "SNGCA")), b["branch"]))


def money(v, cur) -> str:
    if v in (None, "", 0):
        return "not stated"
    try:
        return f"{float(v):,.0f} {cur or ''}".strip()
    except (TypeError, ValueError):
        return str(v)


def build_mail(row: dict, table: dict, test_mode: bool) -> dict:
    cname = country_name(row["country"])
    brs = branches_for(row["country"], table)
    lines = [
        f"New tender above the alert threshold (score {row['score']:.1f}).", "",
        f"Title:     {row['title']}",
        f"Buyer:     {row['buyer'] or 'not stated'}",
        f"Country:   {cname}",
        f"Deadline:  {row['deadline'] or 'not stated'}",
        f"Published: {row['published'] or 'not stated'}",
        f"Value:     {money(row['value'], row['currency'])}",
        f"Source:    {row['source']}",
        f"Link:      {row['url']}", "",
    ]
    if row.get("matched"):
        lines += ["Why it scored:", f"  {row['matched'][:400]}", ""]
    if row.get("description"):
        d = " ".join(str(row["description"]).split())
        lines += ["Description (start of the notice):", f"  {d[:600]}{'...' if len(d) > 600 else ''}", ""]
    if brs:
        shown = brs[:MAX_BRANCHES_SHOWN]
        lines.append(f"Branches in {cname} ({len(brs)}):")
        lines += [f"  - {b['branch']}" for b in shown]
        if len(brs) > len(shown):
            lines.append(f"  ... and {len(brs) - len(shown)} more")
    else:
        lines.append(f"No branch listed in {cname}: tender to be handled centrally.")
    lines.append("")
    if test_mode:
        lines.append("TEST MODE: this alert goes to Leon only. The branches above are the ones that WOULD be notified.")
    lines.append("Automatic alert from Tender Radar: please check the notice before acting.")
    dl = f" (deadline {row['deadline']})" if row["deadline"] else ""
    title = row["title"]
    for sep in (" – ", " - "):  # TED préfixe déjà le pays dans le titre
        if title.lower().startswith(cname.lower() + sep):
            title = title[len(cname) + len(sep):]
            break
    return {
        "id": f"TENDER:{row['uid']}",
        "subject": f"[Tender Radar] {cname}: {title[:90]}{dl}",
        "body": "\n".join(lines),
        "meta": {"name": f"Tender {row['uid']}", "event": "tender", "location": cname, "branch": "; ".join(b["branch"] for b in brs[:5]),
                 "link": row["url"], "score": round(row["score"], 1)},
    }


def build_digest(rows: list[dict], table: dict, test_mode: bool) -> dict:
    lines = [f"{len(rows)} more new tenders above the alert threshold:", ""]
    for r in rows:
        dl = f" | deadline {r['deadline']}" if r["deadline"] else ""
        lines += [f"- [{r['score']:.0f}] {country_name(r['country'])}: {r['title'][:110]}{dl}", f"    {r['url']}"]
    lines += ["", "Automatic alert from Tender Radar: please check each notice before acting."]
    h = hashlib.sha1("|".join(sorted(r["uid"] for r in rows)).encode()).hexdigest()[:8]
    return {"id": f"TENDER-DIGEST:{date.today().isoformat()}:{h}",
            "subject": f"[Tender Radar] {len(rows)} more new tenders ({date.today().isoformat()})",
            "body": "\n".join(lines),
            "meta": {"name": f"Tender digest {date.today().isoformat()}", "event": "tender-digest", "location": "", "branch": "", "link": ""}}


def select_new(db: Path, min_score: float, seen: set[str]) -> list[dict]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    today = date.today().isoformat()
    rows = con.execute(
        """SELECT uid, source, title, buyer, country, description, url, published, deadline, value, currency, score, matched, first_seen
           FROM tenders
           WHERE score >= ? AND duplicate_of IS NULL AND (deadline IS NULL OR deadline = '' OR deadline >= ?)
           ORDER BY score DESC""", (min_score, today)).fetchall()
    con.close()
    return [dict(r) for r in rows if r["uid"] not in seen]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ROOT / "data" / "tenders.sqlite3"))
    ap.add_argument("--min-score", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true", help="affiche les e-mails, n'écrit rien")
    ap.add_argument("--baseline", action="store_true", help="marque tout l'existant comme déjà vu, sans alerte")
    ap.add_argument("--sample", type=int, default=0, help="(avec --dry-run) simule N alertes sur les tenders existants")
    args = ap.parse_args(argv)

    cfg = load_json(HERE / "config.json", {})
    min_score = args.min_score if args.min_score is not None else float(cfg.get("min_score", DEFAULT_MIN_SCORE))
    test_mode = cfg.get("mode", "test") == "test"
    table = load_json(HERE / "branches_by_country.json", {})
    state_path, outbox_path = DATA / "notified.json", DATA / "outbox.json"
    state = load_json(state_path, None)
    outbox = load_json(outbox_path, [])
    today = date.today()
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    outbox = [o for o in outbox if o.get("created", "9999") >= cutoff]

    if not Path(args.db).exists():
        print(f"⚠ Base introuvable : {args.db}")
        return 1
    seen = set(state["ids"]) if state else set()
    candidates = select_new(Path(args.db), min_score, seen)

    if args.sample:
        for r in candidates[: args.sample]:
            m = build_mail(r, table, test_mode)
            print("=" * 70, f"\nSUBJECT: {m['subject']}\n" + "-" * 70 + f"\n{m['body']}\n")
        print(f"[sample] {len(candidates)} tender(s) au-dessus de {min_score} pas encore signalés.")
        return 0

    if state is None or args.baseline:
        print(f"[baseline] {len(candidates)} tender(s) existants marqués comme déjà vus, aucune alerte envoyée.")
        if not args.dry_run:
            save_json(state_path, {"created": today.isoformat(), "min_score": min_score,
                                   "ids": sorted(seen | {c["uid"] for c in candidates})})
            save_json(outbox_path, outbox)
        return 0

    print(f"{len(candidates)} nouveau(x) tender(s) au-dessus de {min_score}.")
    mails = [build_mail(r, table, test_mode) for r in candidates[:MAX_INDIVIDUAL]]
    if len(candidates) > MAX_INDIVIDUAL:
        mails.append(build_digest(candidates[MAX_INDIVIDUAL:], table, test_mode))
    for m in mails:
        print("=" * 70, f"\nSUBJECT: {m['subject']}\n" + "-" * 70 + f"\n{m['body']}\n")
    if args.dry_run:
        print("[dry-run] rien écrit.")
        return 0
    for m in mails:
        outbox.append({"id": m["id"], "created": today.isoformat(), "to": [], "subject": m["subject"], "body": m["body"], "meta": m["meta"]})
    state["ids"] = sorted(seen | {c["uid"] for c in candidates})
    save_json(state_path, state)
    save_json(outbox_path, outbox)
    print(f"{len(mails)} e-mail(s) mis en file dans alerts/data/outbox.json (aucun envoi par ce script).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
