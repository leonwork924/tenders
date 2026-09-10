from .triggers import decay_factor
def company_score(c,cfg):
    ind=(c.get("industry") or "").lower().replace(" ","_"); tier="tier_1" if ind in cfg.raw["industry_tiers"]["tier_1"] else ("tier_2" if ind in cfg.raw["industry_tiers"]["tier_2"] else "other")
    pts={"tier_1":8,"tier_2":6,"other":3}[tier]; h=c.get("employee_count") or 0; hp=next(x["points"] for x in cfg.raw["headcount_bands"] if h<=x["max"])
    gp=5 if c.get("country") in cfg.raw["target_countries"] else 1; fp=5 if c.get("international_presence") else (3 if c.get("countries_active") else 1)
    return min(25,pts+hp+gp+fp),[f"industry tier: {tier} (+{pts})",f"headcount (+{hp})",f"geography (+{gp})",f"international footprint (+{fp})"]
def contact_score(c,cfg):
    if not c:return 0,["no contact"]
    title=(c.title.value or "").lower(); rp=1; rr="other"
    for x in cfg.raw["contact_roles"]:
        if x["role"] in title:rp=x["points"];rr=x["role"];break
    sp=5 if any(x in title for x in ["director","head","chief","vp","vice president"]) else (3 if "manager" in title else 1)
    cp=3 if c.email.value and c.email.method=="VERIFIED_VENDOR" else (2 if c.email.value else 0)
    return min(20,rp+sp+cp),[f"role: {rr} (+{rp})",f"seniority (+{sp})",f"contactability (+{cp})"]
def moves(c,cfg,has_trigger):
    ind=(c.get("industry") or "").lower().replace(" ","_"); tier="tier_1" if ind in cfg.raw["industry_tiers"]["tier_1"] else ("tier_2" if ind in cfg.raw["industry_tiers"]["tier_2"] else "other")
    intl=c.get("international_share",.15); conf=1.0 if "international_share" in c else .4; est=(c.get("employee_count") or 0)*intl*cfg.raw["mobility_rates"][tier]*(1.25 if has_trigger else 1)
    return {"estimated_annual_moves":round(est,2),"range":[round(est*.5,2),round(est*1.5,2)],"confidence":conf,"basis":"headcount x international share x industry mobility rate x trigger uplift"}
def score(c,contact,triggers,cfg):
    cf,cr=company_score(c,cfg); ct,ctr=contact_score(contact,cfg); mi=moves(c,cfg,bool(triggers)); mp=max(0,min(25,round(min(25,mi["estimated_annual_moves"]*4)*mi["confidence"])))
    tr=0; trr=[]
    for t in triggers:
        v=cfg.raw["trigger_weights"].get(t["type"],0)*decay_factor(t.get("date"),cfg.raw["trigger_half_life_days"].get(t["type"],180)); tr+=round(v); trr.append(f"{t['type']}: {round(v)} after recency decay")
    tr=max(-20,min(20,tr)); dq=10 if contact and contact.email.value else (6 if contact else 4); total=max(0,min(100,cf+ct+mp+tr+dq))
    cls="HOT" if total>=80 else "WARM" if total>=60 else "COLD" if total>=40 else "LOW"
    return {"company_fit":cf,"contact_fit":ct,"moving_potential":mp,"trigger":tr,"data_quality":dq,"total":total,"classification":cls,"reasons":cr+ctr+[f"moving potential (+{mp})"]+trr+[f"data quality (+{dq})"],"moves_estimate":mi}
