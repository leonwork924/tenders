"""Test de bout en bout sur une base jetable : baseline -> aucun mail ; nouveau tender -> 1 mail ; relance -> 0."""
import json, shutil, sqlite3, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "alerts"))
import notify  # noqa: E402

tmp = Path(tempfile.mkdtemp())
notify.DATA = tmp / "data"
db = tmp / "t.sqlite3"
shutil.copy(ROOT / "data" / "tenders.sqlite3", db)
fails = []


def check(ok, msg):
    if not ok:
        fails.append(msg)


check(notify.to_alpha2("DEU") == "DE" and notify.to_alpha2("ZA") == "ZA" and notify.to_alpha2("ENG") == "GB" and notify.to_alpha2("UNI") == "", "codes pays")
n0 = notify.main(["--db", str(db)])
st = json.loads((notify.DATA / "notified.json").read_text())
check(n0 == 0 and len(st["ids"]) > 50, "baseline")
check(not (notify.DATA / "outbox.json").read_text().strip("[] \\n"), "baseline ne doit créer aucun mail")

con = sqlite3.connect(db)
cols = [r[1] for r in con.execute("pragma table_info(tenders)")]
row = {c: None for c in cols}
row.update(uid="test:1", source="test", source_id="1", title="Spain – Removal services – Mudanzas internacionales", buyer="Ministerio X",
           country="ESP", url="https://example.org/1", score=42.0, matched="mobility(12.0): removals", first_seen="2026-11-10T00:00:00", last_seen="2026-11-10T00:00:00", status="new")
con.execute(f"insert into tenders ({','.join(cols)}) values ({','.join('?' * len(cols))})", [row[c] for c in cols])
row2 = dict(row, uid="test:2", source_id="2", score=5.0)
con.execute(f"insert into tenders ({','.join(cols)}) values ({','.join('?' * len(cols))})", [row2[c] for c in cols])
con.commit(); con.close()

notify.main(["--db", str(db)])
out = json.loads((notify.DATA / "outbox.json").read_text())
check(len(out) == 1 and out[0]["id"] == "TENDER:test:1", f"un seul mail attendu (score>=15) : {len(out)}")
check("AGS Madrid" in out[0]["body"] and "Santa Fe" not in out[0]["body"], "branches ES, sans Santa Fe")
check(out[0]["subject"].startswith("[Tender Radar] Spain: Removal services") and out[0]["to"] == [], "objet / pas d'adresse dans le repo")
check("@" not in json.dumps(json.loads((ROOT / "alerts" / "branches_by_country.json").read_text())), "aucun e-mail dans branches_by_country.json")
notify.main(["--db", str(db)])
out2 = json.loads((notify.DATA / "outbox.json").read_text())
check(len(out2) == 1, "relance : pas de doublon")
print("ÉCHECS :\n - " + "\n - ".join(fails) if fails else "OK : alertes tenders (baseline, nouveau, pas de doublon, branches)")
sys.exit(1 if fails else 0)
