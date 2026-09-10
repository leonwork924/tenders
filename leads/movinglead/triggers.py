from datetime import datetime,timezone
KEYWORDS={"INTERNATIONAL_EXPANSION":["international expansion","expansion into","global expansion"],"NEW_OFFICE":["new office","opens office","opened office","office opening"],"MARKET_ENTRY":["market entry","enters the market","launches in"],"MAJOR_INTERNATIONAL_HIRING":["international hiring","recruiting internationally","major hiring"],"ACQUISITION":["acquires","acquisition"],"MERGER":["merger","merges with"],"EMPLOYEE_MOBILITY_PROGRAMME":["mobility programme","mobility program","global mobility"],"HEADQUARTERS_RELOCATION":["headquarters relocation","relocates headquarters"],"INTERNATIONAL_PROJECT":["international project","cross-border project"],"CORPORATE_RESTRUCTURING":["restructuring","reorganisation","reorganization"],"LAYOFFS":["layoffs","redundancies","job cuts"],"HIRING_FREEZE":["hiring freeze","freeze hiring"],"MARKET_EXIT":["market exit","withdraws from"],"INSOLVENCY":["insolvent","insolvency","bankruptcy"],"INCUMBENT_CONTRACT_AWARDED":["contract awarded to","appointed provider","incumbent provider"]}
def classify(text):
    t=(text or "").lower(); return [k for k,v in KEYWORDS.items() if any(x in t for x in v)]
def decay_factor(date,half):
    if not date:return .5
    try:d=datetime.fromisoformat(date.replace("Z","+00:00")); d=d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except:return .5
    age=max(0,(datetime.now(timezone.utc)-d).total_seconds()/86400); return .5**(age/max(1,half))
