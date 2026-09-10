import sqlite3,json,hashlib
from datetime import datetime,timezone
class Store:
    def __init__(self,path="movinglead.db"):
        self.db=sqlite3.connect(path); self.db.row_factory=sqlite3.Row
        self.db.executescript("""CREATE TABLE IF NOT EXISTS prospects(prospect_id TEXT PRIMARY KEY,prospect_json TEXT,contact_email_hash TEXT,contact_name TEXT,classification TEXT,score INTEGER,created_at TEXT,updated_at TEXT); CREATE INDEX IF NOT EXISTS idx_email ON prospects(contact_email_hash); CREATE TABLE IF NOT EXISTS suppressions(kind TEXT,value TEXT,reason TEXT,created_at TEXT,PRIMARY KEY(kind,value));"""); self.db.commit()
    def all_prospects(self):
        return [json.loads(x["prospect_json"]) for x in self.db.execute("select prospect_json from prospects")]
    def save(self,p):
        d=p.to_dict(); c=p.contacts[0] if p.contacts else None; h=c.email_hash if c else None; n=(" ".join(filter(None,[c.first_name.value,c.last_name.value])) if c else ""); now=datetime.now(timezone.utc).isoformat()
        self.db.execute("insert or replace into prospects values(?,?,?,?,?,?,?,?)",(p.prospect_id,json.dumps(d),h,n,p.scoring.get("classification"),p.scoring.get("total"),now,now)); self.db.commit()
    def suppressions(self):
        d={"companies":set(),"emails":set(),"domains":set()}
        for r in self.db.execute("select kind,value from suppressions"):
            if r["kind"] in d:d[r["kind"]].add(r["value"])
        return d
    def suppress(self,kind,value,reason):
        self.db.execute("insert or replace into suppressions values(?,?,?,?)",(kind,value,reason,datetime.now(timezone.utc).isoformat()));self.db.commit()
    def kpis(self):
        rows=self.db.execute("select classification from prospects").fetchall(); n=len(rows); q=sum(x["classification"] in ("HOT","WARM") for x in rows)
        return {"prospects":n,"qualified_per_100":round(q/max(n,1)*100,2),"hot_warm_rate":round(q/max(n,1),3)}
