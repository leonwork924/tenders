"""
org_delegations.py — Délégations auprès des organisations internationales
===========================================================================

Contrairement à `agent.py` (un site par pays, ~117 sources), cette source
s'appuie sur le fait que certaines organisations internationales publient
elles-mêmes une liste centralisée de leurs représentants permanents — une
seule source à maintenir par organisation, plutôt que 100+.

En pratique, la qualité et le format de cette liste centralisée varient
ÉNORMÉMENT d'une organisation à l'autre (voir le détail par organisation
ci-dessous). Ce script n'a jamais tourné en conditions réelles (même
limitation d'accès réseau que les autres scripts de ce projet) : les
extracteurs ci-dessous sont écrits à partir de pages consultées via
recherche web ponctuelle, PAS vérifiés en exécution. Teste avec `--org`
sur une seule organisation à la fois et vérifie manuellement les premières
sorties avant de faire confiance à l'ensemble.

## Organisations couvertes

### ONU (New York) — "un"
Source : jeu de données officiel de la bibliothèque Dag Hammarskjöld de
l'ONU (https://digitallibrary.un.org/record/4091498), format CSV, mis à
jour périodiquement, couvrant les chefs de mission ACTUELS ET PASSÉS.
C'est la meilleure source du lot : structurée, officielle, avec un
historique. En revanche :
- Les noms exacts des colonnes du CSV n'ont pas pu être vérifiés (le
  téléchargement direct a été bloqué par une protection anti-robot lors
  de la préparation de ce script) : le parseur ci-dessous devine les
  colonnes par mots-clés (voir `_guess_column`) plutôt que d'utiliser des
  noms figés, et affiche les en-têtes détectés au premier lancement pour
  vérification.
- Le dataset ne semble pas inclure de date de PRISE de fonction de façon
  garantie (la fiche le décrit comme dérivé d'autorités de noms, pas
  structuré poste par poste) — à vérifier une fois le fichier en main.

### Union africaine (Addis-Abeba) — "au"
Source : https://au.int/en/prc (page officielle du Comité des
Représentants Permanents). Cette page donne de façon fiable le Bureau du
PRC (Président, Vice-Présidents, Rapporteur) mais PAS la liste complète
et à jour des ~55 représentants permanents dans un format exploitable —
les sections "Sub-Committees" de la même page mélangent des listes de
membres de plusieurs années différentes (repérées lors de la préparation
de ce script : une section datée "January 2014 to January 2015" cohabite
avec une actualité 2026 ailleurs sur le site). Le parseur se limite donc
volontairement au Bureau du PRC, qui est la partie fiable et daté de la
page.

### OSCE (Vienne) — "osce"
Source : https://www.osce.org/node/108218 ("Participating States"), un
fil de légendes de photos documentant les présentations de lettres de
créance des ambassadeurs, avec la date et le lieu. Ce n'est PAS une liste
exhaustive et stable des 57 délégations à un instant T, mais un flux
d'événements récents — utile spécifiquement parce qu'il donne des DATES
(l'info demandée), au prix d'une couverture partielle (seuls les
changements récents y figurent, pas nécessairement tous les 57 pays).

### ONG et organisations intergouvernementales observatrices à l'ONU — "un_ngo"
Source : la même page Wikipedia que ci-dessus (liste des représentants
permanents à l'ONU) contient aussi, dans des tableaux séparés, les
**observateurs permanents non-étatiques** admis à l'Assemblée générale :
organisations intergouvernementales régionales (Union africaine, Ligue
arabe, Organisation de la coopération islamique, Conseil de coopération du
Golfe, Commonwealth, Francophonie, etc.) ET véritables ONG/organisations
internationales de la société civile (Comité international de la
Croix-Rouge, Fédération internationale des sociétés de la Croix-Rouge et
du Croissant-Rouge, Comité international olympique, Union
interparlementaire, Ordre souverain de Malte). C'est la réponse la plus
directe trouvée à "des ONG ou organisations du même type" : contrairement
au reste de ce fichier, **la structure de cette page a été vérifiée par une
consultation réelle** lors de la préparation de ce script (contenu récupéré
en direct, pas deviné) — c'est donc l'extracteur le plus fiable du fichier,
même s'il reste soumis aux limites habituelles de Wikipedia (base
collaborative, à recouper). Chaque entrée porte un champ `delegate_type`
(`observer_state` pour le Saint-Siège/la Palestine, `observer_entity` pour
les OIG et ONG) pour les distinguer des délégations d'États membres.

### Union européenne (Coreper, Bruxelles) — non implémenté
Aucune page centralisée fiable identifiée : Coreper n'a pas d'annuaire
public listant les 27 ambassadeurs avec noms à jour sur une page statique
(le portail "EU Whoiswho" charge son contenu en JavaScript, et le site du
Conseil ne liste pas les titulaires actuels de façon structurée). La
solution réaliste serait de reprendre le modèle de `sources.json` :
une source par représentation permanente nationale à Bruxelles (ex :
"Représentation Permanente de la France auprès de l'UE"), comme pour les
ambassades bilatérales. Non construit ici faute de budget de recherche
suffisant pour identifier et vérifier 27 URLs fiables — voir le README
pour la marche à suivre si tu veux l'ajouter toi-même.

Usage:
    python src/org_delegations.py --org un --out data/org_un.json
    python src/org_delegations.py --org au --out data/org_au.json
    python src/org_delegations.py --org osce --out data/org_osce.json
    python src/org_delegations.py --org all --out data/org_delegations.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

USER_AGENT = "MobilitasAGS-DiplomaticSourceAgent/1.0 (+contact: tenders@overseasam.com)"
REQUEST_TIMEOUT = 30
RATE_LIMIT_SECONDS = 2.0

UN_CSV_URL = "https://digitallibrary.un.org/record/4091498/files/2026_01_06_ambassadors.csv"
AU_PRC_URL = "https://au.int/en/prc"
OSCE_PARTICIPATING_STATES_URL = "https://www.osce.org/node/108218"


@dataclass
class OrgDelegateEntry:
    organization: str        # "ONU (New York)", "Union africaine (PRC)", "OSCE (Vienne)"
    country_source: str      # pays (ou nom de l'entité/ONG/OIG) représenté
    name: str
    title: str
    raw_line: str
    source_url: str
    scraped_at: str
    start_date: str | None = None
    end_date: str | None = None
    data_source: str = "org_official"
    role: str = "org_delegate"
    delegate_type: str = "member_state"  # "member_state" | "observer_state" | "observer_entity"
    phone: str | None = None
    email: str | None = None


def fetch(url: str) -> requests.Response | None:
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        print(f"  [erreur] impossible de récupérer {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# ONU
# ---------------------------------------------------------------------------

_COL_KEYWORDS = {
    "name": ["name", "nom", "personal_name", "authorized_name"],
    "country": ["state", "member state", "country", "pays", "jurisdiction"],
    "function": ["function", "role", "title", "position", "note"],
    "start_date": ["start", "from", "date_from", "credential", "appointed"],
    "end_date": ["end", "to", "date_to"],
    "email": ["email", "e-mail", "courriel"],
    "phone": ["phone", "tel", "téléphone", "telephone"],
}


def _guess_column(headers: list[str], key: str) -> str | None:
    lowered = {h: h.lower() for h in headers}
    for h, low in lowered.items():
        if any(kw in low for kw in _COL_KEYWORDS[key]):
            return h
    return None


def fetch_un_delegates() -> list[OrgDelegateEntry]:
    print("[ONU] Téléchargement du jeu de données Dag Hammarskjöld Library...")
    resp = fetch(UN_CSV_URL)
    if resp is None:
        print("  -> échec (le site a peut-être une protection anti-robot ; "
              "essaie de télécharger le CSV manuellement et adapte ce script "
              "pour lire un fichier local si besoin).")
        return []

    now = datetime.now(timezone.utc).isoformat()
    reader = csv.DictReader(io.StringIO(resp.content.decode("utf-8", errors="ignore")))
    headers = reader.fieldnames or []
    print(f"  -> colonnes détectées : {headers}")

    col_name = _guess_column(headers, "name")
    col_country = _guess_column(headers, "country")
    col_function = _guess_column(headers, "function")
    col_start = _guess_column(headers, "start_date")
    col_end = _guess_column(headers, "end_date")
    col_email = _guess_column(headers, "email")
    col_phone = _guess_column(headers, "phone")

    if not col_name or not col_country:
        print("  [erreur] impossible de deviner les colonnes nom/pays — "
              "inspecte le CSV manuellement (voir en-têtes ci-dessus) et "
              "adapte _COL_KEYWORDS ou le parseur en conséquence.")
        return []

    entries = []
    for row in reader:
        name = (row.get(col_name) or "").strip()
        country = (row.get(col_country) or "").strip()
        if not name or not country:
            continue
        function = (row.get(col_function) or "Permanent Representative").strip() if col_function else "Permanent Representative"
        start = (row.get(col_start) or "").strip() if col_start else ""
        end = (row.get(col_end) or "").strip() if col_end else ""
        email = (row.get(col_email) or "").strip() if col_email else ""
        phone = (row.get(col_phone) or "").strip() if col_phone else ""
        entries.append(
            OrgDelegateEntry(
                organization="ONU (New York)",
                country_source=country,
                name=name,
                title=function or "Permanent Representative",
                raw_line=json.dumps(row, ensure_ascii=False),
                source_url=UN_CSV_URL,
                scraped_at=now,
                start_date=start or None,
                end_date=end or None,
                email=email or None,
                phone=phone or None,
            )
        )
    print(f"  -> {len(entries)} entrée(s)")
    return entries


# ---------------------------------------------------------------------------
# Union africaine
# ---------------------------------------------------------------------------

_AU_BUREAU_RE = re.compile(
    r"(Chairperson|First Vice-Chairperson|Second Vice-Chairperson|"
    r"Third Vice-Chairperson|Rapporteur)\s*:\s*([^\n,]+),\s*([^\n]+)"
)


def fetch_au_prc_bureau() -> list[OrgDelegateEntry]:
    print(f"[Union africaine] {AU_PRC_URL} (Bureau du PRC uniquement)...")
    resp = fetch(AU_PRC_URL)
    if resp is None:
        return []
    text = BeautifulSoup(resp.text, "html.parser").get_text("\n")
    now = datetime.now(timezone.utc).isoformat()

    entries = []
    seen = set()
    for m in _AU_BUREAU_RE.finditer(text):
        role_title, name, country = m.group(1), m.group(2).strip(), m.group(3).strip()
        key = (role_title, name, country)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            OrgDelegateEntry(
                organization="Union africaine (Comité des Représentants Permanents)",
                country_source=country,
                name=name,
                title=f"{role_title}, PRC",
                raw_line=m.group(0),
                source_url=AU_PRC_URL,
                scraped_at=now,
            )
        )
    print(f"  -> {len(entries)} entrée(s) (Bureau uniquement — pas les ~55 membres du PRC, voir docstring)")
    return entries


# ---------------------------------------------------------------------------
# OSCE
# ---------------------------------------------------------------------------

# Exemple de légende ciblée : "H.E. Andranik Hovhannisyan, Ambassador and
# Permanent Representative of Armenia to the OSCE, with ..., Vienna, 21
# November 2024."
_OSCE_CAPTION_RE = re.compile(
    r"(?:H\.?E\.?\s+)?([A-ZÀ-Ý][\wÀ-ÿ'’\.-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'’\.-]+){1,4}),\s*"
    r"Ambassador and Permanent Representative of ([A-Z][\w\s'’-]+?) to the OSCE.*?"
    r"(\d{1,2}\s+\w+\s+\d{4})"
)


def fetch_osce_recent_credentials() -> list[OrgDelegateEntry]:
    print(f"[OSCE] {OSCE_PARTICIPATING_STATES_URL} (flux des présentations de lettres de créance)...")
    resp = fetch(OSCE_PARTICIPATING_STATES_URL)
    if resp is None:
        return []
    text = BeautifulSoup(resp.text, "html.parser").get_text(" ")
    now = datetime.now(timezone.utc).isoformat()

    entries = []
    seen = set()
    for m in _OSCE_CAPTION_RE.finditer(text):
        name, country, date_str = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        key = (name, country)
        if key in seen:
            continue
        seen.add(key)
        start_date = _parse_english_date(date_str)
        entries.append(
            OrgDelegateEntry(
                organization="OSCE (Conseil permanent, Vienne)",
                country_source=country,
                name=name,
                title="Ambassador and Permanent Representative to the OSCE",
                raw_line=m.group(0)[:200],
                source_url=OSCE_PARTICIPATING_STATES_URL,
                scraped_at=now,
                start_date=start_date,
            )
        )
    print(f"  -> {len(entries)} entrée(s) (couverture partielle — flux d'événements récents, pas une liste des 57)")
    return entries


def _parse_english_date(date_str: str) -> str | None:
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(date_str, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# ONG et organisations intergouvernementales observatrices à l'ONU
# ---------------------------------------------------------------------------

UN_WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_current_permanent_representatives_to_the_United_Nations"

# Correspondance entre le libellé de la première colonne d'un tableau
# Wikipedia et le delegate_type à assigner. On ignore volontairement la
# colonne "country" (États membres), déjà couverte par le dataset officiel
# ONU (fetch_un_delegates) — pas la peine de dupliquer depuis une source
# tierce quand une source officielle existe pour cette même catégorie.
_WIKI_TABLE_TYPES = {
    "state": "observer_state",     # Saint-Siège, Palestine
    "entity": "observer_entity",   # OIG régionales + vraies ONG (Croix-Rouge, CIO, etc.)
}


def fetch_un_observer_entities() -> list[OrgDelegateEntry]:
    print(f"[ONU - ONG/OIG observatrices] {UN_WIKIPEDIA_URL}...")
    resp = fetch(UN_WIKIPEDIA_URL)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    now = datetime.now(timezone.utc).isoformat()
    entries = []

    for table in soup.find_all("table", class_="wikitable"):
        header_cells = table.find("tr")
        if header_cells is None:
            continue
        headers = [c.get_text(strip=True).lower() for c in header_cells.find_all(["th", "td"])]
        if not headers:
            continue

        # Repère la colonne qui identifie "qui" (state/entity), le nom et
        # la date, par mots-clés dans l'en-tête plutôt que par position fixe
        # (les 3 tableaux de la page n'ont pas exactement les mêmes colonnes).
        idx_who = next((i for i, h in enumerate(headers) if h in _WIKI_TABLE_TYPES), None)
        if idx_who is None:
            continue  # tableau non pertinent (ex : celui des États membres, déjà couvert ailleurs)
        idx_name = next((i for i, h in enumerate(headers) if "name" in h), None)
        idx_date = next((i for i, h in enumerate(headers) if "date" in h), None)
        if idx_name is None:
            continue

        delegate_type = _WIKI_TABLE_TYPES[headers[idx_who]]
        rows = table.find_all("tr")[1:]  # saute l'en-tête
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) <= max(idx_who, idx_name):
                continue
            who = cells[idx_who].get_text(" ", strip=True)
            name = cells[idx_name].get_text(" ", strip=True)
            date_raw = cells[idx_date].get_text(" ", strip=True) if idx_date is not None and idx_date < len(cells) else ""
            if not who or not name:
                continue

            start_date = _parse_english_date(date_raw)
            entries.append(
                OrgDelegateEntry(
                    organization="ONU (New York)",
                    country_source=who,
                    name=name,
                    title=(
                        "Observateur permanent (État non-membre)"
                        if delegate_type == "observer_state"
                        else "Observateur permanent (OIG/ONG)"
                    ),
                    raw_line=f"{who} | {name} | {date_raw}",
                    source_url=UN_WIKIPEDIA_URL,
                    scraped_at=now,
                    start_date=start_date,
                    delegate_type=delegate_type,
                )
            )

    n_state = sum(1 for e in entries if e.delegate_type == "observer_state")
    n_entity = sum(1 for e in entries if e.delegate_type == "observer_entity")
    print(f"  -> {len(entries)} entrée(s) ({n_state} État(s) non-membre(s), {n_entity} OIG/ONG)")
    return entries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

FETCHERS = {
    "un": fetch_un_delegates,
    "au": fetch_au_prc_bureau,
    "osce": fetch_osce_recent_credentials,
    "un_ngo": fetch_un_observer_entities,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", choices=list(FETCHERS.keys()) + ["all"], default="all")
    parser.add_argument("--out", default="data/org_delegations.json")
    args = parser.parse_args()

    orgs = list(FETCHERS.keys()) if args.org == "all" else [args.org]

    all_entries: list[OrgDelegateEntry] = []
    for org in orgs:
        try:
            all_entries.extend(FETCHERS[org]())
        except Exception as e:
            print(f"  [erreur inattendue] {org}: {e}")
        time.sleep(RATE_LIMIT_SECONDS)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([asdict(e) for e in all_entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nTerminé : {len(all_entries)} entrées écrites dans {out_path}")


if __name__ == "__main__":
    main()
