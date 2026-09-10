import csv, json, html
def export_json(ps,path):
    with open(path,"w",encoding="utf-8") as f:json.dump([p.to_dict() for p in ps],f,ensure_ascii=False,indent=2)
def export_csv(ps,path,classification=None):
    rows=[]
    for p in ps:
        if classification and p.scoring.get("classification")!=classification:continue
        c=p.contacts[0] if p.contacts else None
        rows.append({"prospect_id":p.prospect_id,"company":p.company.get("name"),"contact":" ".join(filter(None,[c.first_name.value,c.last_name.value])) if c else "","title":c.title.value if c else "","email":c.email.value if c else "","score":p.scoring.get("total"),"classification":p.scoring.get("classification"),"why_now":p.why_now,"human_review":p.human_review})
    with open(path,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ["prospect_id"]);w.writeheader();w.writerows(rows)
def review_html(ps,path):
    body="".join(f"<article><h2>{html.escape(p.company.get('name',''))} — {p.scoring.get('classification')} ({p.scoring.get('total')})</h2><p>{html.escape(p.why_now)}</p><pre>{html.escape(chr(10).join(p.scoring.get('reasons',[])))}</pre></article>" for p in sorted(ps,key=lambda x:x.scoring.get("total",0),reverse=True))
    open(path,"w",encoding="utf-8").write("<html><body><h1>MovingLead Review</h1>"+body+"</body></html>")
