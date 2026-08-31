from __future__ import annotations
import gzip, json, re
from datetime import datetime, timezone
from pathlib import Path

def _dt(v):
    if not v:
        return None
    s = str(v).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _get(o, *paths):
    for p in paths:
        x=o
        ok=True
        for k in p.split("."):
            if isinstance(x, dict):
                x=x.get(k)
            elif isinstance(x, list) and k.isdigit() and int(k)<len(x):
                x=x[int(k)]
            else:
                ok=False; break
        if ok and x not in (None,""):
            return x
    return None

def inspect_jsonl(path, lookback_days=14, sample=5):
    """Inspect an OCDS JSONL(.gz) file and print why records may become zero."""
    p=Path(path)
    op=gzip.open if p.suffix==".gz" else open
    total=parsed=with_tender=dated=recent=0
    samples=[]
    now=datetime.now(timezone.utc)
    cutoff=now.timestamp()-lookback_days*86400

    with op(p,"rt",encoding="utf-8",errors="replace") as f:
        for line in f:
            if not line.strip(): continue
            total += 1
            try: obj=json.loads(line); parsed += 1
            except Exception: continue
            recs=obj.get("records") if isinstance(obj,dict) else None
            if not isinstance(recs,list): recs=[obj]
            for r in recs:
                if not isinstance(r,dict): continue
                if isinstance(r.get("tender"),dict):
                    with_tender += 1
                date=_get(r,
                    "tender.tenderPeriod.startDate",
                    "tender.tenderPeriod.endDate",
                    "tender.milestones.0.dueDate",
                    "date","publishedDate","lastModified",
                    "awards.0.date")
                d=_dt(date)
                if d:
                    dated += 1
                    if d.timestamp() >= cutoff:
                        recent += 1
                        if len(samples)<sample:
                            samples.append({
                                "ocid":r.get("ocid"),
                                "date":date,
                                "title":_get(r,"tender.title","title"),
                                "status":_get(r,"tender.status","status"),
                                "country":_get(r,"parties.0.address.country"),
                                "currency":_get(r,"tender.value.currency","value.currency"),
                            })
    print(f"total lines: {total}")
    print(f"parsed lines: {parsed}")
    print(f"records with tender: {with_tender}")
    print(f"records with usable date: {dated}")
    print(f"records in lookback ({lookback_days}d): {recent}")
    if samples:
        print("sample recent records:")
        for s in samples: print(s)
