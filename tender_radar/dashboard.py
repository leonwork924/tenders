"""A local dashboard, bound to 127.0.0.1 only.

Live view of the database, so you can change the score threshold without
re-running the fetch, and mark items as seen or ignored.
"""

from __future__ import annotations

from datetime import datetime

from flask import Flask, redirect, request, url_for

from .db import Database
from .export import render_html


def create_app(config: dict) -> Flask:
    app = Flask(__name__)
    db_path = config["database"]["path"]
    default_min = float(config.get("run", {}).get("min_score", 0))
    min_days = int(config.get("run", {}).get("min_days_to_deadline", 0))

    @app.get("/")
    def index():
        min_score = float(request.args.get("min_score", default_min))
        only_new = request.args.get("all") != "1"
        with_deadline = min_days if request.args.get("expired") != "1" else 0

        db = Database(db_path)
        try:
            rows = db.shortlist(min_score, only_new=only_new, limit=500,
                                min_days=with_deadline)
            stats = db.stats()
        finally:
            db.close()

        scope = (f"min score {min_score:g} · "
                 f"{'unseen only' if only_new else 'all items'} · "
                 f"{stats['total']} stored, {stats['new']} unseen, "
                 f"{stats['duplicates']} duplicates suppressed")
        page = render_html(rows, {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "scope": scope,
        })
        controls = f"""
<div class="toolbar" style="position:static;border-top:1px solid var(--rule)">
  <form method="get" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <label for="ms">Min score</label>
    <input id="ms" name="min_score" type="number" step="0.5" value="{min_score:g}" style="width:90px">
    <label><input type="checkbox" name="all" value="1" {'checked' if not only_new else ''}> include already seen</label>
    <label><input type="checkbox" name="expired" value="1" {'checked' if with_deadline == 0 else ''}> include near-deadline</label>
    <button type="submit">Apply</button>
  </form>
  <form method="post" action="{url_for('mark_seen')}" style="margin-left:auto">
    <button type="submit">Mark everything shown as seen</button>
  </form>
</div>"""
        return page.replace("<main>", controls + "<main>")

    @app.post("/seen")
    def mark_seen():
        db = Database(db_path)
        try:
            rows = db.shortlist(0, only_new=True, limit=100000)
            db.mark_seen([r["uid"] for r in rows])
        finally:
            db.close()
        return redirect(url_for("index"))

    return app
