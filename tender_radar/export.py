"""Turn database rows into a CSV and a single self-contained HTML page.

The HTML has no external assets and no network calls, so it opens from the file
system and works with the laptop offline.
"""

from __future__ import annotations

import csv
import html
from datetime import date, datetime
from pathlib import Path

COLUMNS = ["score", "title", "buyer", "country", "deadline", "days_left",
           "value", "currency", "source", "published", "url", "matched", "uid"]


def _days_left(deadline: str | None) -> str:
    if not deadline:
        return ""
    try:
        d = datetime.fromisoformat(str(deadline)[:10]).date()
    except ValueError:
        return ""
    return str((d - date.today()).days)


def rows_to_dicts(rows) -> list[dict]:
    out = []
    for r in rows:
        d = dict(r)
        d["days_left"] = _days_left(d.get("deadline"))
        out.append(d)
    return out


COUNTRY_NAME_OVERRIDES = {
    "XK": "Kosovo",
    "GZ": "Gaza / Cisjordanie",
    "YF": "Yougoslavie (ancien code)",
    "ZR": "Congo (RD)",
    "UNI": "Regional / multi-pays",
}


def _country_name(code: str) -> str:
    """Best-effort ISO country name. Falls back to the raw code for the
    non-ISO regional codes some sources (World Bank in particular) use for
    multi-country projects -- still filterable, just not a pretty name.
    """
    if not code:
        return ""
    code = code.strip()
    if code in COUNTRY_NAME_OVERRIDES:
        return COUNTRY_NAME_OVERRIDES[code]
    try:
        import pycountry
        country = None
        if len(code) == 2:
            country = pycountry.countries.get(alpha_2=code.upper())
        elif len(code) == 3:
            country = pycountry.countries.get(alpha_3=code.upper())
        if country:
            return country.name
    except Exception:
        pass
    return code


def write_history_json(expired_rows, renewal_rows, path: str | Path, meta: dict) -> Path:
    """The Historique tab: every expired tender above threshold, plus a
    'renouvellement a prevoir' shortlist of the ones whose stated contract
    ends within the next 6 months. That second list will start sparse (or
    empty) and grow over time -- it only has data where the source actually
    published a contract duration, which most sources don't reliably do.
    See models.py Tender.contract_end for the honest coverage caveat.
    """
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def slim(d):
        return {
            "uid": d.get("uid"),
            "score": round(float(d.get("score") or 0), 1),
            "title": d.get("title"),
            "buyer": d.get("buyer"),
            "country": d.get("country"),
            "country_name": _country_name(d.get("country") or ""),
            "deadline": d.get("deadline"),
            "contract_end": d.get("contract_end"),
            "value": d.get("value"),
            "currency": d.get("currency"),
            "source": d.get("source"),
            "url": d.get("url"),
        }

    payload = {
        "generated": meta.get("generated"),
        "expired": [slim(d) for d in rows_to_dicts(expired_rows)],
        "renewals": [slim(d) for d in rows_to_dicts(renewal_rows)],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=None), encoding="utf-8")
    return path


def write_json(rows, path: str | Path, meta: dict, source_health: list[dict] | None = None) -> Path:
    """Write the shared-site JSON: every active, above-threshold tender.
    This is what the static site on Vercel reads, and it always reflects the
    *full* current shortlist (not just today's new items) so anyone checking
    the page any day sees everything still open. No branch split -- the site
    filters by country instead.
    """
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = rows_to_dicts(rows)

    items = []
    for d in data:
        first_seen = (d.get("first_seen") or "")[:10]
        is_new = False
        if first_seen:
            try:
                age_days = (date.today() - datetime.fromisoformat(first_seen).date()).days
                is_new = age_days <= 2
            except ValueError:
                pass
        items.append({
            "uid": d.get("uid"),
            "score": round(float(d.get("score") or 0), 1),
            "title": d.get("title"),
            "buyer": d.get("buyer"),
            "country": d.get("country"),
            "country_name": _country_name(d.get("country") or ""),
            "deadline": d.get("deadline"),
            "days_left": d.get("days_left"),
            "value": d.get("value"),
            "currency": d.get("currency"),
            "source": d.get("source"),
            "published": d.get("published"),
            "url": d.get("url"),
            "matched": d.get("matched"),
            "is_new": is_new,
        })

    payload = {
        "generated": meta.get("generated"),
        "scope": meta.get("scope"),
        "count": len(items),
        "items": items,
        "source_health": source_health or [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=None), encoding="utf-8")
    return path


def write_csv(rows, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = rows_to_dicts(rows)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in data:
            writer.writerow({k: row.get(k, "") for k in COLUMNS})
    return path


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

CSS = """
:root {
  --paper: #f2f4f6;
  --card: #ffffff;
  --ink: #16202b;
  --ink-soft: #5a6b7c;
  --rule: #d8dee5;
  --navy: #1b3a5c;
  --amber: #b4690e;
  --red: #a32626;
  --green: #2f6b3f;
  --mono: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
  --sans: ui-sans-serif, system-ui, "Segoe UI", Inter, Helvetica, Arial, sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--paper); color: var(--ink);
  font-family: var(--sans); font-size: 14px; line-height: 1.45;
}
header {
  background: var(--navy); color: #eef3f8; padding: 18px 24px;
  display: flex; flex-wrap: wrap; gap: 18px; align-items: baseline;
}
header h1 { margin: 0; font-size: 17px; letter-spacing: .14em; text-transform: uppercase; font-weight: 600; }
header .meta { font-family: var(--mono); font-size: 12px; color: #a9c0d6; }
.toolbar {
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  padding: 12px 24px; background: var(--card); border-bottom: 1px solid var(--rule);
  position: sticky; top: 0; z-index: 5;
}
.toolbar input, .toolbar select {
  font: inherit; padding: 6px 10px; border: 1px solid var(--rule);
  border-radius: 3px; background: #fff; color: var(--ink);
}
.toolbar input[type=search] { min-width: 260px; }
.toolbar label { font-size: 12px; color: var(--ink-soft); text-transform: uppercase; letter-spacing: .08em; }
.count { margin-left: auto; font-family: var(--mono); font-size: 12px; color: var(--ink-soft); }
main { padding: 0 24px 48px; }
table { width: 100%; border-collapse: collapse; background: var(--card); }
thead th {
  text-align: left; font-size: 11px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--ink-soft); font-weight: 600; padding: 10px 12px;
  border-bottom: 2px solid var(--navy); position: sticky; top: 53px; background: var(--card);
  cursor: pointer; white-space: nowrap;
}
tbody td { padding: 10px 12px; border-bottom: 1px solid var(--rule); vertical-align: top; }
tbody tr:hover { background: #f7f9fb; }
td.num, td.date { font-family: var(--mono); font-size: 13px; white-space: nowrap; }
a { color: var(--navy); }
.title { font-weight: 600; display: block; margin-bottom: 3px; }
.why { font-family: var(--mono); font-size: 11px; color: var(--ink-soft); }
.score { display: flex; align-items: center; gap: 8px; }
.score b { font-family: var(--mono); font-size: 14px; width: 34px; text-align: right; }
.bar { width: 52px; height: 6px; background: var(--rule); border-radius: 3px; overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--navy); }
.chip {
  display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 11px;
  font-family: var(--mono); border: 1px solid currentColor;
}
.soon { color: var(--red); } .mid { color: var(--amber); } .ok { color: var(--green); }
.src { font-family: var(--mono); font-size: 11px; color: var(--ink-soft); text-transform: uppercase; }
.src-chip { display:inline-block; padding:3px 7px; border:1px solid var(--rule); border-radius:10px; background:#f7f9fb; }
.source-summary { display:flex; flex-wrap:wrap; gap:8px; margin:0 24px 12px; }
.source-summary .src-chip b { color:var(--ink); }
.empty { padding: 60px 0; text-align: center; color: var(--ink-soft); }
@media (max-width: 900px) {
  .hide-sm { display: none; }
  .toolbar input[type=search] { min-width: 140px; flex: 1; }
}
"""

JS = """
const q = document.getElementById('q');
const srcSel = document.getElementById('src');
const rows = Array.from(document.querySelectorAll('tbody tr'));
const count = document.getElementById('count');

function apply() {
  const term = (q.value || '').toLowerCase();
  const src = srcSel.value;
  let shown = 0;
  rows.forEach(r => {
    const hay = r.dataset.hay;
    const ok = (!term || hay.includes(term)) && (!src || r.dataset.source === src);
    r.style.display = ok ? '' : 'none';
    if (ok) shown++;
  });
  count.textContent = shown + ' of ' + rows.length + ' shown';
}
q.addEventListener('input', apply);
srcSel.addEventListener('change', apply);

document.querySelectorAll('thead th[data-key]').forEach((th, idx) => {
  let asc = false;
  th.addEventListener('click', () => {
    asc = !asc;
    const body = document.querySelector('tbody');
    const sorted = rows.slice().sort((a, b) => {
      const av = a.children[idx].dataset.sort ?? a.children[idx].textContent.trim();
      const bv = b.children[idx].dataset.sort ?? b.children[idx].textContent.trim();
      const an = parseFloat(av), bn = parseFloat(bv);
      const cmp = (!isNaN(an) && !isNaN(bn)) ? an - bn : av.localeCompare(bv);
      return asc ? cmp : -cmp;
    });
    sorted.forEach(r => body.appendChild(r));
  });
});
apply();
"""


def _urgency(days: str) -> str:
    if days == "":
        return "ok"
    d = int(days)
    if d <= 7:
        return "soon"
    if d <= 21:
        return "mid"
    return "ok"


def _money(value, currency) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
        if abs(number - round(number)) < 1e-9:
            rendered = f"{number:,.0f}"
        else:
            rendered = f"{number:,.2f}"
        return f"{rendered} {currency or ''}".strip()
    except (TypeError, ValueError):
        return str(value)


def render_html(rows, meta: dict) -> str:
    data = rows_to_dicts(rows)
    max_score = max([float(d.get("score") or 0) for d in data], default=1.0) or 1.0
    sources = sorted({d.get("source", "") for d in data if d.get("source")})

    body_rows = []
    source_counts = {}
    for d in data:
        source_counts[d.get("source", "") or "unknown"] = source_counts.get(d.get("source", "") or "unknown", 0) + 1
    for d in data:
        e = lambda v: html.escape(str(v or ""))
        days = d.get("days_left", "")
        score = float(d.get("score") or 0)
        hay = " ".join([str(d.get(k) or "") for k in
                        ("title", "buyer", "country", "matched", "source")]).lower()
        body_rows.append(f"""
      <tr data-source="{e(d.get('source'))}" data-hay="{e(hay)}">
        <td class="num" data-sort="{score}">
          <span class="score"><b>{score:.0f}</b>
          <span class="bar"><i style="width:{min(100, score / max_score * 100):.0f}%"></i></span></span>
        </td>
        <td>
          <a class="title" href="{e(d.get('url'))}" target="_blank" rel="noopener">{e(d.get('title'))}</a>
          <span class="why">{e((d.get('matched') or '')[:180])}</span>
        </td>
        <td class="hide-sm">{e(d.get('buyer'))}</td>
        <td class="num">{e(d.get('country'))}</td>
        <td class="date" data-sort="{e(d.get('deadline') or '9999-12-31')}">
          {e(str(d.get('deadline') or '')[:10])}
          {f'<span class="chip {_urgency(days)}">{days}d</span>' if days != '' else ''}
        </td>
        <td class="num hide-sm" data-sort="{d.get('value') or 0}">{e(_money(d.get('value'), d.get('currency')))}</td>
        <td class="src"><span class="src-chip">{e(d.get('source'))}</span></td>
      </tr>""")

    options = "".join(f'<option value="{html.escape(s)}">{html.escape(s)}</option>'
                      for s in sources)
    source_summary = "".join(
        f'<span class="src-chip">{html.escape(str(src).upper())} <b>{count}</b></span>'
        for src, count in sorted(source_counts.items())
    )
    table = f"""
    <div class="source-summary">{source_summary}</div>
    <table>
      <thead><tr>
        <th data-key="score">Score</th>
        <th data-key="title">Tender</th>
        <th data-key="buyer" class="hide-sm">Buyer</th>
        <th data-key="country">Country</th>
        <th data-key="deadline">Deadline</th>
        <th data-key="value" class="hide-sm">Value</th>
        <th data-key="source" class="hide-sm">Source</th>
      </tr></thead>
      <tbody>{''.join(body_rows)}</tbody>
    </table>""" if data else (
        '<p class="empty">No tenders above the score threshold in this window.<br>'
        'Lower <code>run.min_score</code> in config.yaml, or widen '
        '<code>run.lookback_days</code>, and run again.</p>')

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tender Radar — shortlist</title>
<style>{CSS}</style>
</head><body>
<header>
  <h1>Tender Radar</h1>
  <span class="meta">{html.escape(meta.get('generated', ''))} &nbsp;·&nbsp;
  {html.escape(str(meta.get('scope', '')))}</span>
</header>
<div class="toolbar">
  <label for="q">Filter</label>
  <input id="q" type="search" placeholder="title, buyer, country, keyword…" autocomplete="off">
  <label for="src">Source</label>
  <select id="src"><option value="">all</option>{options}</select>
  <span class="count" id="count"></span>
</div>
<main>{table}</main>
<script>{JS}</script>
</body></html>"""


def write_html(rows, path: str | Path, meta: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(rows, meta), encoding="utf-8")
    return path

# Filtered notices export. Uses the same renderer as the shortlist.
def write_filtered_html(rows, path: str | Path, meta: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(rows, meta), encoding="utf-8")
    return path
