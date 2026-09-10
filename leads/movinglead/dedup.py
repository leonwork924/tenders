import re,hashlib
from difflib import SequenceMatcher
SUFFIXES={"limited","ltd","inc","incorporated","corp","corporation","llc","plc","pty","pty ltd","company","co","sa"}
def norm(s): return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9\s]"," ",(s or "").lower())).strip()
def normalize_company(name):
    p=norm(name).split()
    while p and p[-1] in SUFFIXES:p.pop()
    return " ".join(p)
def duplicate_verdict(company,email=None,contact_name=None,registry_id=None,existing=None):
    existing=existing or []; cn=normalize_company(company)
    eh=hashlib.sha256(email.lower().strip().encode()).hexdigest() if email else None
    for p in existing:
        pc=p.get("company",{}); pn=normalize_company(pc.get("name",""))
        if registry_id and pc.get("registry_id")==registry_id:return "DUPLICATE",["same registry identifier"]
        if eh and p.get("_contact_email_hash")==eh:return "DUPLICATE",["same normalized email"]
        if contact_name and cn==pn and norm(contact_name)==norm(p.get("_contact_name","")):return "DUPLICATE",["same person at same company"]
        if cn==pn and cn:return "POSSIBLE_DUPLICATE",["same company, different contact"]
        if cn and pn and 0.84<=SequenceMatcher(None,cn,pn).ratio()<.93:return "POSSIBLE_DUPLICATE",[f"similar company names ({SequenceMatcher(None,cn,pn).ratio():.0%})"]
    return "CLEAR",[]
def check_suppression(company,email=None,domain=None,suppressed=None):
    s=suppressed or {"companies":set(),"emails":set(),"domains":set()}
    if normalize_company(company) in {normalize_company(x) for x in s["companies"]}:return True,"company"
    if email and email.lower() in {x.lower() for x in s["emails"]}:return True,"email"
    if domain and domain.lower() in {x.lower() for x in s["domains"]}:return True,"domain"
    return False,""
