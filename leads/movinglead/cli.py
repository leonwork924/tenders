import argparse,json
from .config import ICPConfig
from .storage import Store
from .providers import OfflineFixtureProvider,DefaultEmailVerifier
from .pipeline import LeadGenerationAgent
from .export import export_json,export_csv,review_html
def main():
    ap=argparse.ArgumentParser();sp=ap.add_subparsers(dest="cmd",required=True)
    r=sp.add_parser("run");r.add_argument("--country");r.add_argument("--min-employees",type=int,default=0);r.add_argument("--lead-type",default="CORPORATE_RELOCATION");r.add_argument("--lawful-basis",default="NOT_ESTABLISHED");r.add_argument("--dry-run",action="store_true")
    rv=sp.add_parser("review");rv.add_argument("--out",default="review.html")
    ex=sp.add_parser("export");ex.add_argument("--format",choices=["json","csv"],default="json");ex.add_argument("--classification");ex.add_argument("--out",default="leads.json")
    sp.add_parser("kpis")
    a=ap.parse_args();cfg=ICPConfig.load("config/icp.yaml");store=Store();agent=LeadGenerationAgent(store,OfflineFixtureProvider(),DefaultEmailVerifier(),cfg)
    if a.cmd=="run":
        for p in agent.run(vars(a),a.dry_run):print(json.dumps({"company":p.company["name"],"score":p.scoring["total"],"classification":p.scoring["classification"],"human_review":p.human_review}))
    elif a.cmd=="kpis":print(json.dumps(store.kpis(),indent=2))
    elif a.cmd=="review":
        # Dashboard generation is intentionally lightweight; use stored JSON in a real UI.
        print("Review dashboard generation available after loading stored Prospect objects.")
    else: print("Use export through the Python API in this MVP.")
