# Tender Radar

A tender monitor for a removals, fine-art logistics, archiving and
digitisation business. It fetches public procurement notices once a day,
scores them against a bilingual (EN/FR) keyword taxonomy, removes
cross-source duplicates, and publishes a ranked, filterable list to a shared
web page — no account needed, just a link.

Two parts:

- **The pipeline** (`tender_radar/`, this repo) — fetches, scores, dedupes,
  stores in a SQLite file committed to the repo. Runs daily via GitHub
  Actions, or by hand on your laptop.
- **The site** (`site/`) — a plain static page (no framework, no build step)
  that reads `site/data.json`, hosted on Vercel. Filter by country, search,
  sort by score or deadline. Anyone with the link can open it; there's no
  login, so it isn't a new platform people have to register on — it's one
  page that always shows today's list.

---

## 1. Which sources actually work, and how

| Source | Access route | Cost | Status here |
|---|---|---|---|
| **TED** (EU, Tenders Electronic Daily) | Official Search API v3, `POST https://api.ted.europa.eu/v3/notices/search`. No authentication for published notices; there is a fair-usage policy. | Free | **Implemented** (`type: ted`) |
| **UK Find a Tender** | Official OCDS API, `GET /api/1.0/ocdsReleasePackages`. Open Government Licence. | Free | **Implemented** (`type: ocds_fts`) |
| **South Africa eTenders** (National Treasury) | Official OCDS API. Given you're in Cape Town this is probably your highest-value non-EU feed. | Free | **Implemented** (`type: ocds_za`) — confirm the base URL still responds before you rely on it; the endpoint has moved in the past. |
| **GlobalTenders** | Commercial aggregator. No documented open API; data feeds are sold to subscribers. | Paid | Via `csv_inbox` or `vendor_api` |
| **TendersInfo** | Commercial aggregator. Sells subscriber data feeds (XML/CSV), typically as a paid add-on. | Paid | Via `csv_inbox` or `vendor_api` |
| **TenderHive** | Commercial aggregator, subscription. No public API I could verify. | Paid | Via `csv_inbox` |
| **AfricaTender** | Commercial aggregator, subscription. | Paid | Via `csv_inbox` |
| **TRINTA** | I could not verify a public API, or confirm which platform you mean — ask them directly for a data feed and what their terms allow. | Unknown | Via `csv_inbox` once you know |

### On scraping the commercial aggregators

I have not written scrapers for GlobalTenders, TendersInfo, TenderHive,
AfricaTender or TRINTA, and I'd advise against adding them.

These businesses sell exactly what a scraper would take: the aggregation itself.
Their terms of service almost always prohibit automated access, bulk extraction,
and reuse of listings — that's the standard shape for this category, and it's the
term they enforce. Beyond the contract, a scraper against a paid aggregator has
two practical problems: you'd be building a dependency that breaks whenever they
change their markup, and if you're a subscriber, a violation gets your account
terminated and takes the legitimate feed with it.

**Verify before you act**: read the terms of each vendor you actually subscribe
to, and check `/robots.txt`. I can describe the general pattern, but I can't tell
you what any specific vendor's current contract says, and this isn't legal
advice.

**The route that works instead**: every one of these vendors will sell or supply
a data feed. Ask for a daily CSV/XML export or an API key. Drop the export into
`inbox/`, and the `csv_inbox` adapter pulls it into the same pipeline as
everything else. If they give you an API key, fill in the `vendor_api` block in
`config.yaml`. Same result, no contract risk.

The official government portals are the opposite case: TED, Find a Tender and
eTenders publish open data and explicitly want it reused. Those are safe to poll
daily, and they're also the *primary* sources most of the aggregators resell.

### Other sources worth adding

Free and official, in rough order of relevance to your sectors:

- **BOAMP** (France) — French public contracts below the EU threshold, which is
  where most municipal *déménagement* and *archivage* work sits. Published as
  open data on `data.economie.gouv.fr` with an OpenDataSoft API. Worth wiring up
  as a dedicated adapter; TED only carries the above-threshold notices.
- **DECP / PLACE** (France) — *données essentielles de la commande publique*,
  open data, good for seeing who won what.
- **UNGM** (UN Global Marketplace) — relevant for relocation and records work at
  UN agencies. Free registration, email alerts. No open API, and its terms
  restrict automated access, so use their alerts and route them via `inbox/`.
- **World Bank / AfDB / UNDP procurement notices** — open datasets and RSS
  feeds. Relevant for archive digitisation and heritage projects in Africa. The
  `rss` adapter takes any of these; verify each feed URL is still live.
- **National portals in your target markets** — Belgium (e-Procurement),
  Netherlands (TenderNed), Ireland (eTenders). Above-threshold notices reach
  TED anyway, so add these only if you want the smaller contracts too.

---

## 2. Publier le site partagé (Vercel + GitHub Actions)

C'est la partie qui répond au besoin : une page accessible par un lien, sans
compte, mise à jour toute seule chaque jour. Trois étapes, à faire une seule
fois.

### a) Mettre le code sur GitHub

```bash
cd tender-radar
git init
git add .
git commit -m "Initial import"
```

Crée un dépôt (public ou privé, peu importe) sur GitHub, puis :

```bash
git remote add origin https://github.com/<votre-org>/tender-radar.git
git push -u origin main
```

### b) Connecter le dépôt à Vercel

1. Sur [vercel.com](https://vercel.com), "Add New Project" → importer le
   dépôt GitHub que tu viens de créer.
2. Dans les réglages du projet : **Root Directory = `site`**, Framework
   Preset = **Other** (c'est du HTML/CSS/JS pur, pas de build).
3. Déployer. Vercel donne une URL du type `https://tender-radar.vercel.app`
   — c'est le lien à partager en interne.

Chaque `git push` sur `main` redéploie automatiquement.

### c) Activer la mise à jour quotidienne

Le fichier `.github/workflows/fetch.yml` est déjà dans le dépôt : dès que le
code est sur GitHub, il tourne tout seul chaque jour à 06:15 UTC (modifiable
dans le fichier — c'est une expression cron), récupère les nouveaux appels
d'offres, régénère `site/data.json`, et commit le résultat. Vercel voit le
commit et redéploie automatiquement : le site est à jour sans que personne
n'intervienne.

Pour lancer un premier run tout de suite sans attendre demain matin : onglet
**Actions** du dépôt GitHub → `fetch tenders` → **Run workflow**.

Le seul réglage à faire dans GitHub : Settings → Actions → General →
*Workflow permissions* → **Read and write permissions** (nécessaire pour que
le job puisse commit `site/data.json` à votre place).

### Diffuser le lien

Pas de compte, pas de mot de passe : le lien Vercel suffit. Comment le
communiquer aux équipes (mail interne, Teams, intranet...) reste à vous —
aucune automatisation n'est nécessaire côté outil pour ça.

Le filtre pays fonctionne aussi via l'URL, utile pour un lien direct :
`https://tender-radar.vercel.app/?country=DEU` ouvre la page déjà filtrée
sur l'Allemagne (le code pays est celui du champ `country` de chaque source
— parfois ISO-2, parfois ISO-3 selon la source d'origine, géré automatiquement
à l'export).

---

## 3. Setup local (optionnel, pour tester ou déboguer)

Python 3.10 or newer. Instructions for both platforms.

### macOS

```bash
cd ~/tender-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py fetch --open
```

### Windows (PowerShell)

```powershell
cd C:\tender-radar
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py fetch --open
```

If PowerShell blocks the activation script, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

That first run creates `data/tenders.sqlite3` and writes
`out/shortlist.csv` and `out/shortlist.html`.

---

## 4. Commands

```
python run.py fetch                # the daily job: fetch, score, dedupe, store, export
python run.py fetch --source ted   # one source only, useful when debugging
python run.py fetch --dry-run -v   # fetch and score, write nothing
python run.py fetch --mark-seen    # what the scheduler runs: today's items become
                                   # "seen", so tomorrow's shortlist is new items only
python run.py export --all         # rebuild CSV/HTML including items already seen
python run.py dashboard            # live view at http://127.0.0.1:8765
python run.py status               # DB counts, last run, per-source health
python run.py keywords             # rebuild keywords.yaml from the spreadsheet
python run.py score "déménagement des archives municipales"   # offline scoring probe
python run.py purge --days 60      # drop tenders whose deadline is long past
```

### Keywords

The keyword list is **generated**, not hand-written in `config.yaml`:

```
tools/keywords_source.xlsx     your spreadsheet (English, some German)
tools/keywords_manual.yaml     French terms, negatives, CPV, routing, demotions
                ↓  python run.py keywords
keywords.yaml                  generated — do not edit by hand
```

262 terms across 8 groups came out of your spreadsheet plus the French layer.
To change them, edit the spreadsheet or `keywords_manual.yaml` and re-run
`python run.py keywords`. For a one-off addition you don't want to maintain,
use the `keywords_extra` block in `config.yaml` — it survives regeneration.

What the importer does with your sheet, and why:

- **Merges all three sheets.** They overlap almost entirely; "Records
  Management" is the superset.
- **Splits combined cells.** `Digitization / Digitisation` becomes both
  spellings; `enterprise content management (ECM)` becomes the phrase and the
  acronym.
- **Stops at the blank row.** The *Boubacar* sheet has a second prose block
  underneath ("Core Records-Management Process", "– Records Retrieval"). Those
  are section headings, not search terms, and matching on them would be noise.
- **Routes by subject, not by column.** Your columns mix subjects — digitisation
  terms appear under both Records Management and Heritage. Terms are sent to the
  group they belong to, so `digitization of audio tape` lands in `av_media` and
  `manuscripts` in `heritage`. The rules are in `keywords_manual.yaml` under
  `routing`, so you can change where things go without touching code.
- **Demotes terms that are too generic** to `support` (weight 1.5): *installation,
  logistics, storage, warehousing, packing, filing, indexing, metadata,
  retrieval, mobility, moving, visa, immigration, work permit, freight
  forwarding, cargo handling, transportation*. These are real parts of your
  offer, but each appears in tens of thousands of unrelated notices. In the
  support group they add corroborating weight to a notice that already matched
  something specific, and can't push one over the threshold alone. The full list
  is `demote:` in `keywords_manual.yaml` — move any term back out if you disagree.
- **Deduplicates.** A term appearing in several columns is kept only in its
  highest-weighted group, so `scanning` cannot score twice. A wildcard also
  absorbs its own longer forms: `digitalisier*` replaces both *digitalisieren*
  and *digitalisierung*.

### Three corrections I made to the sheet

1. **`restauration` is dropped.** In French it overwhelmingly means *catering*,
   not restoration — "restauration collective" and "restauration scolaire" are
   school meals contracts. Matching it would have flooded you with catering
   tenders. `restauration du patrimoine` and `restauration d'oeuvre*` are in
   instead, and the catering phrases are now negatives.
2. **Typos fixed**: `mome search` → home search, `Historial Documents` →
   historical documents, `Digitiztion Services` → digitisation services,
   `Commerical Removals` → commercial removals, `Musems,Libraries Archives`
   split into three terms.
3. **`ton band` / `video band`** looked like a translation of *bande son* /
   *bande vidéo*, so they expand to *audio tape, sound tape, bande son* and
   *video tape, videotape, bande vidéo*. Correct me if they meant something else.

All three are in the `rewrite:` map in `keywords_manual.yaml`, one line each, if
you want to change any of them back.

### German

Your sheet had two German words (*digitalisieren*, *digitalisierung*), so I
rounded out the same vocabulary: *archivierung, archivbestand\*, aktenlagerung,
schriftgutverwaltung, umzug\*, betriebsumzug, möbeltransport, magnetband\*,
kulturgut, restaurierung, bestandserhaltung, scannen, mikroverfilmung*. Delete
the German blocks in `keywords_manual.yaml` if you never bid in DE/AT/CH.

### Tuning and calibration

`tools/calibrate.py` holds 21 labelled example notices — ten that must be
shortlisted, nine that must not, two judgement calls — and checks the taxonomy
against all of them offline:

```bash
python tools/calibrate.py           # pass/fail at the configured threshold
python tools/calibrate.py --sweep   # which thresholds classify everything correctly
```

The sweep is how `min_score: 10` was chosen: any value from 6 to 11 classifies
all 19 labelled cases correctly, and 10 sits in the middle with room either way.
Above 11 you start losing genuine heritage-conservation work; below 6 generic
warehousing notices get in.

**Add your own notices to `CASES` as you go** — the contracts you won, and the
false positives that wasted your time. It takes a minute and it's the thing that
keeps the list honest as you re-tune.

For a single ad-hoc check, `python run.py score "<text>"` prints the breakdown:

```
$ python run.py score "Asbestos removal and waste removal works"
score 0.0
  negative(-14.0): asbestos removal, waste removal
below threshold (10)
```

That's what stops "removal" from filling your shortlist with asbestos and refuse
contracts — the single biggest false-positive source in this sector.

Other knobs in `config.yaml`: raise `run.min_score` for less noise, lower
`scoring.group_cap` if one group dominates, raise `scoring.title_multiplier` to
weight titles harder (a term in the title currently counts 2.5x one in the body,
and repeats beyond the first count 0.25x).

---

## 5. Lancer localement sur un planning (alternative a GitHub Actions)

Si vous préférez ne pas utiliser GitHub Actions (§2) et faire tourner le
fetch depuis un PC qui reste allumé, ces deux options font la même chose
en local :

- **Windows**: `scheduling/windows-task.md` — a PowerShell one-liner that
  registers a Task Scheduler job at 07:15 with `-StartWhenAvailable`, so a run
  missed while the laptop was asleep fires on wake.
- **macOS**: `scheduling/com.tenderradar.daily.plist` — edit the three paths,
  copy to `~/Library/LaunchAgents/`, `launchctl load` it.

Both run `run.py fetch --mark-seen`. Because `lookback_days` is 3 and dedupe is
persistent, a missed day costs you nothing: the next run re-reads the window and
skips what it already has.

---

## 6. How it works

```
sources/*.py   fetch    each adapter returns a list of Tender objects
pipeline.py    filter   country rules, minimum days to deadline
scoring.py     score    weighted EN/FR keyword groups + CPV boost - negatives
dedupe.py      dedupe   exact fingerprint, then fuzzy title guarded by buyer name
db.py          store    SQLite; status new -> seen so you only see new items
export.py      output   CSV + self-contained HTML
```

**Deduplication** runs in two passes. First an exact fingerprint (SHA-1 of the
normalised title plus buyer), which catches aggregators republishing TED notices
verbatim. Then a fuzzy title match (`rapidfuzz`, token-set ratio ≥ 90) against
everything already stored, guarded by a buyer-name check so two different
councils both tendering "Office relocation" stay separate records. Duplicates
are stored with a `duplicate_of` pointer rather than discarded, so you can audit
what got suppressed.

**Only-new-once** is the `status` column: `new` → `seen`. `fetch --mark-seen`
flips today's exported items, so tomorrow's shortlist contains only genuinely
new notices. Nothing is deleted; `export --all` or the dashboard checkbox brings
back the full history.

**Everything below the score threshold is still stored.** Raising or lowering
`min_score` later needs no refetch — just `python run.py export`.

---

## 7. Project layout

```
tender-radar/
├── config.yaml                 # settings, filters, sources
├── keywords.yaml               # GENERATED keyword taxonomy
├── requirements.txt
├── run.py                      # entry point
├── README.md
├── data/tenders.sqlite3        # committed -- carries state between Actions runs
├── out/                        # shortlist.csv, shortlist.html (local only, gitignored)
├── inbox/                      # drop licensed CSV/JSON exports here
├── site/                       # the shared page -- Vercel root directory
│   ├── index.html
│   ├── style.css
│   ├── app.js                  # country filter, search, sort -- no build step
│   └── data.json               # GENERATED, committed by the daily Action
├── .github/workflows/
│   └── fetch.yml                # daily: fetch -> export -> commit -> Vercel redeploys
├── tools/
│   ├── keywords_source.xlsx    # your keyword spreadsheet
│   ├── keywords_manual.yaml    # French/German terms, negatives, CPV, routing
│   ├── import_keywords.py      # spreadsheet + manual -> keywords.yaml
│   └── calibrate.py            # labelled test notices
├── scheduling/
│   ├── windows-task.md
│   └── com.tenderradar.daily.plist
└── tender_radar/
    ├── cli.py                  # commands
    ├── config.py               # config loading, path resolution
    ├── models.py               # the Tender record
    ├── normalize.py            # de-accenting, term compiling, date/value parsing
    ├── scoring.py               # bilingual keyword scorer
    ├── dedupe.py                # cross-source dedupe
    ├── db.py                    # SQLite schema and queries
    ├── pipeline.py               # the daily run
    ├── export.py                 # CSV + HTML + site JSON (with country names)
    ├── dashboard.py               # local Flask app, 127.0.0.1 only -- optional, for debugging
    └── sources/
        ├── base.py             # HTTP session, rate limiting
        ├── ted.py              # TED Search API v3
        ├── ocds.py             # UK Find a Tender + SA eTenders
        └── generic.py          # RSS, CSV inbox, licensed vendor API
```

## 8. Adding a source

Subclass `Source`, implement `fetch()` returning `list[Tender]`, register it in
`sources/__init__.py`, add a block to `config.yaml`. The scoring, dedupe, storage
and export layers need no changes.

```python
class BoampSource(Source):
    def fetch(self) -> list[Tender]:
        data = self.get(self.settings["base_url"], params={...}).json()
        return [Tender(source="boamp", source_id=r["id"], title=r["objet"], ...)
                for r in data["records"]]
```

`self.get()` and `self.post()` handle the rate limit and timeout for you.

## 9. What I tested, and what you need to verify

The full pipeline — keyword import, scoring, dedupe, SQLite storage, CSV and
HTML export — was tested end to end on sample data covering French, English and
German notices, including the asbestos false positive, the French
catering/restoration trap, and a duplicate republished by an aggregator.
`python tools/calibrate.py` passes on all 19 labelled cases.

I could not make live calls to TED, Find a Tender or eTenders from where I built
this, so those three adapters were tested against fixture responses shaped like
the documented schemas rather than against the live services. Run
`python run.py fetch --source ted -v --dry-run` first: if a field name or an
endpoint has drifted, the verbose log shows you exactly what came back, and the
mapping is one small function per source (`_to_tender` in `ted.py`,
`release_to_tender` in `ocds.py`).
