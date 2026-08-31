"""Command line interface: python -m tender_radar <command>"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from .config import load_config
from .db import Database
from .export import write_csv, write_html, write_filtered_html, write_json
from .models import Tender
from .pipeline import run as run_pipeline
from .scoring import Scorer


def _setup_logging(verbose: bool):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_fetch(args, config):
    report = run_pipeline(config, only_source=args.source, dry_run=args.dry_run)
    print("\n" + report.summary())
    filtered_path = write_filtered_html(
        report.filtered,
        config["output"].get("filtered_html_path", "out/filtered.html"),
        {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "scope": f"{len(report.filtered)} notices rejected by hard filters",
        },
    )
    print(f"  Filtered HTML {filtered_path}")
    if not args.dry_run and not args.no_export:
        cmd_export(args, config)
    return 1 if report.errors and report.fetched == 0 else 0


def cmd_export(args, config):
    db = Database(config["database"]["path"])
    try:
        rows = db.shortlist(
            min_score=float(config["run"]["min_score"]),
            only_new=not getattr(args, "all", False),
            limit=int(config["output"].get("limit", 200)),
            min_days=int(config["run"].get("min_days_to_deadline", 0)),
        )
        meta = {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "scope": (f"{len(rows)} tenders · min score "
                      f"{config['run']['min_score']:g} · "
                      f"last {config['run']['lookback_days']} days"),
        }
        csv_path = write_csv(rows, config["output"]["csv_path"])
        html_path = write_html(rows, config["output"]["html_path"], meta)

        # The site JSON always reflects the *full* active shortlist (every
        # tender still above threshold and not past its deadline), regardless
        # of --all / seen status -- a branch checking the page any day should
        # see everything currently open, not just "what's new since I last
        # ran this locally".
        site_rows = db.shortlist(
            min_score=float(config["run"]["min_score"]),
            only_new=False,
            limit=int(config["output"].get("site_limit", 1000)),
            min_days=int(config["run"].get("min_days_to_deadline", 0)),
        )
        json_meta = {
            "generated": meta["generated"],
            "scope": f"{len(site_rows)} active tenders above score "
                     f"{config['run']['min_score']:g}",
        }
        json_path = write_json(site_rows, config["output"]["json_path"], json_meta)

        if getattr(args, "mark_seen", False):
            db.mark_seen([r["uid"] for r in rows])
    finally:
        db.close()

    print(f"\n{len(rows)} tenders on the shortlist")
    print(f"  CSV  {csv_path}")
    print(f"  HTML {html_path}")
    print(f"  JSON {json_path}  ({len(site_rows)} active tenders, for the site)")
    if getattr(args, "open", False):
        webbrowser.open(html_path.as_uri())
    return 0


def cmd_dashboard(args, config):
    from .dashboard import create_app

    app = create_app(config)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Dashboard on {url}  (Ctrl-C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


def cmd_status(args, config):
    db = Database(config["database"]["path"])
    try:
        stats = db.stats()
    finally:
        db.close()
    print(f"Database: {config['database']['path']}")
    print(f"  stored {stats['total']} · unseen {stats['new']} · "
          f"duplicates {stats['duplicates']}")
    last = stats["last_run"]
    if last:
        print(f"  last run {last['started_at']} → {last['ended_at']}: "
              f"fetched {last['fetched']}, new {last['inserted']}, "
              f"dupes {last['duplicates']}")
        if last["errors"]:
            print(f"  errors: {last['errors']}")
    for s in stats["sources"]:
        flag = "ok" if s["last_ok"] == s["last_run"] else "FAILED"
        print(f"  {s['source']:<20} {flag:<7} last run {s['last_run']}"
              + (f" · {s['last_error']}" if s["last_error"] else ""))
    return 0


def cmd_score(args, config):
    """Score a piece of text without touching the network. Use it to tune weights."""
    text = " ".join(args.text) if args.text else sys.stdin.read()
    scorer = Scorer(config)
    probe = Tender(source="probe", source_id="probe", title=text[:200],
                   url="", description=text)
    score, matched = scorer.score(probe)
    print(f"score {score}")
    for part in matched.split(" | "):
        print("  " + part)
    threshold = float(config["run"]["min_score"])
    print(f"\n{'ABOVE' if score >= threshold else 'below'} threshold ({threshold:g})")
    return 0


def cmd_purge(args, config):
    db = Database(config["database"]["path"])
    try:
        cur = db.conn.execute(
            "DELETE FROM tenders WHERE deadline IS NOT NULL AND deadline < date('now', ?)",
            (f"-{args.days} day",))
        db.conn.commit()
        print(f"Removed {cur.rowcount} tenders whose deadline passed over {args.days} days ago")
    finally:
        db.close()
    return 0


def cmd_keywords(args, config):
    """Rebuild keywords.yaml from the spreadsheet + manual layer."""
    import runpy
    script = Path(config["_root"]) / "tools" / "import_keywords.py"
    argv = ["import_keywords.py"]
    if args.xlsx:
        argv += ["--xlsx", args.xlsx]
    if args.dry_run:
        argv.append("--dry-run")
    sys.argv = argv
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return exc.code or 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tender-radar",
                                description="Local public tender monitor")
    p.add_argument("-c", "--config", help="path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="fetch, score and store (the daily job)")
    f.add_argument("--source", help="run only this source")
    f.add_argument("--dry-run", action="store_true", help="fetch and score, store nothing")
    f.add_argument("--no-export", action="store_true")
    f.add_argument("--mark-seen", action="store_true",
                   help="mark exported items as seen so tomorrow's run is new-only")
    f.add_argument("--open", action="store_true", help="open the HTML shortlist afterwards")
    f.add_argument("--all", action="store_true", help="export seen items too")
    f.set_defaults(func=cmd_fetch)

    e = sub.add_parser("export", help="re-write CSV and HTML from the database")
    e.add_argument("--all", action="store_true", help="include items already seen")
    e.add_argument("--mark-seen", action="store_true")
    e.add_argument("--open", action="store_true")
    e.set_defaults(func=cmd_export)

    d = sub.add_parser("dashboard", help="serve the local dashboard")
    d.add_argument("--port", type=int, default=8765)
    d.add_argument("--no-open", action="store_true")
    d.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("status", help="what the database and sources look like")
    s.set_defaults(func=cmd_status)

    sc = sub.add_parser("score", help="score arbitrary text, offline, for tuning")
    sc.add_argument("text", nargs="*")
    sc.set_defaults(func=cmd_score)

    k = sub.add_parser("keywords", help="rebuild keywords.yaml from the spreadsheet")
    k.add_argument("--xlsx", help="use a different spreadsheet")
    k.add_argument("--dry-run", action="store_true", help="print, write nothing")
    k.set_defaults(func=cmd_keywords)

    pu = sub.add_parser("purge", help="delete tenders whose deadline is long past")
    pu.add_argument("--days", type=int, default=60)
    pu.set_defaults(func=cmd_purge)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    config = load_config(args.config)
    return args.func(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
