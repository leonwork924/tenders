"""
Agent de génération de contacts corporate (grandes entreprises / ETI)
Europe / Afrique / Moyen-Orient / Asie

Pipeline :
  1. Recherche entreprise      -> routeur multi-sources (registre national si disponible, sinon OpenCorporates)
                                   - France (FR)      : API Recherche d'Entreprises (data.gouv.fr) — gratuite, sans clé
                                   - Royaume-Uni (GB)  : Companies House API — gratuite, clé requise
                                   - Norvège (NO)      : Brønnøysundregistrene (Brreg) — gratuite, sans clé
                                   - Singapour (SG)    : ACRA via data.gov.sg — gratuite, 5 req/min sans clé
                                   - Belgique (BE)     : KBO/BCE Open Data — gratuite, fichier mensuel (pas d'API en direct),
                                                          SEULE source fournissant de vrais contacts (tél/email/site)
                                   - Autres juridictions : OpenCorporates (gratuit, clé optionnelle, 170+ pays)
  2. Résolution LEI            -> GLEIF API (gratuit, sans clé)
  3. Résolution domaine        -> déduit des données registre / à défaut fourni manuellement
  4. Enrichissement contacts   -> Hunter.io API (clé requise, free tier: 25 recherches/mois)
  5. Vérification email        -> Hunter.io email-verifier
  6. Stockage                  -> SQLite, structure alignée sur schema_contacts_corporate.json

Prérequis :
    pip install requests --break-system-packages

Configuration :
    Renseignez vos clés dans les variables d'environnement :
        OPENCORPORATES_API_KEY     (optionnel, augmente les quotas OpenCorporates)
        COMPANIES_HOUSE_API_KEY    (gratuit sur https://developer.company-information.service.gov.uk/,
                                     nécessaire uniquement pour les entreprises UK)
        HUNTER_API_KEY             (obligatoire pour l'enrichissement de contacts)
        BELGIUM_KBO_DATA_DIR       (dossier contenant l'extrait KBO Open Data décompressé,
                                     voir instructions dans le code — inscription gratuite requise sur
                                     https://kbopub.economie.fgov.be/kbo-open-data/login)

Ajouter une nouvelle source nationale :
    Créez une fonction search_company_<pays>(name) qui retourne un dict
    {legal_name, jurisdiction, company_number, source_url}, puis ajoutez-la
    au routeur REGISTRY_ROUTES ci-dessous.

Usage :
    python agent_corporate_contacts.py --name "Danone" --country FR
"""

import os
import sqlite3
import uuid
import argparse
import time
import csv
import io
import json
from urllib.parse import quote
from datetime import datetime, timezone

import requests

OPENCORPORATES_API_KEY = os.environ.get("OPENCORPORATES_API_KEY")
COMPANIES_HOUSE_API_KEY = os.environ.get("COMPANIES_HOUSE_API_KEY")
HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY")

DB_PATH = "corporate_contacts.db"


# ---------------------------------------------------------------------------
# 1. Base SQLite (structure alignée sur le schéma JSON défini précédemment)
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            company_id TEXT PRIMARY KEY,
            legal_name TEXT,
            lei TEXT,
            jurisdiction TEXT,
            primary_domain TEXT,
            source_name TEXT,
            source_url TEXT,
            retrieved_at TEXT,
            registry_phone TEXT,
            registry_email TEXT,
            registry_website TEXT,
            hq_city TEXT,
            hq_state TEXT,
            hq_country TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_registry (
            source_id TEXT PRIMARY KEY,
            source_type TEXT,
            source_name TEXT,
            jurisdiction TEXT,
            source_url TEXT,
            source_license TEXT,
            collection_method TEXT,
            automated_access TEXT,
            personal_data TEXT,
            commercial_use_allowed TEXT,
            marketing_allowed TEXT,
            policy_class TEXT,
            terms_checked_at TEXT,
            notes TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS corporate_relationships (
            relationship_id TEXT PRIMARY KEY,
            child_company_id TEXT,
            parent_lei TEXT,
            relationship_type TEXT,
            source_name TEXT,
            source_url TEXT,
            retrieved_at TEXT,
            FOREIGN KEY (child_company_id) REFERENCES companies(company_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS procurement_events (
            procurement_id TEXT PRIMARY KEY,
            notice_id TEXT,
            source_name TEXT,
            source_url TEXT,
            buyer_name TEXT,
            supplier_name TEXT,
            country TEXT,
            cpv_code TEXT,
            title TEXT,
            notice_date TEXT,
            contract_value TEXT,
            raw_json TEXT,
            retrieved_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            contact_id TEXT PRIMARY KEY,
            company_id TEXT,
            full_name TEXT,
            job_title TEXT,
            email TEXT,
            phone TEXT,
            email_confidence_score REAL,
            email_verification_status TEXT,
            linkedin_url TEXT,
            legal_basis TEXT,
            collection_source TEXT,
            collected_at TEXT,
            opt_out INTEGER DEFAULT 0,
            FOREIGN KEY (company_id) REFERENCES companies (company_id)
        )
    """)
    conn.commit()
    register_open_sources(conn)
    return conn


# ---------------------------------------------------------------------------
# 2. Étape 1 : recherche de l'entreprise sur OpenCorporates
# ---------------------------------------------------------------------------

def search_company_opencorporates(name: str, jurisdiction: str = None):
    """Recherche une entreprise par nom (et code pays ISO optionnel) sur OpenCorporates."""
    url = "https://api.opencorporates.com/v0.4/companies/search"
    params = {"q": name, "per_page": 5}
    if jurisdiction:
        params["jurisdiction_code"] = jurisdiction.lower()
    if OPENCORPORATES_API_KEY:
        params["api_token"] = OPENCORPORATES_API_KEY

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    companies = data.get("results", {}).get("companies", [])
    if not companies:
        return None

    # On prend le premier résultat comme meilleure correspondance
    best = companies[0]["company"]
    return {
        "legal_name": best.get("name"),
        "jurisdiction": best.get("jurisdiction_code"),
        "company_number": best.get("company_number"),
        "opencorporates_url": best.get("opencorporates_url"),
    }


# ---------------------------------------------------------------------------
# 2 bis. Sources nationales gratuites (meilleure couverture que OpenCorporates
#         seul sur certaines juridictions)
# ---------------------------------------------------------------------------

def search_company_france_gouv(name: str):
    """France : API Recherche d'Entreprises (data.gouv.fr) — gratuite, sans clé, 7 req/s."""
    url = "https://recherche-entreprises.api.gouv.fr/search"
    resp = requests.get(url, params={"q": name, "per_page": 1}, timeout=15)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    if not results:
        return None

    best = results[0]
    return {
        "legal_name": best.get("nom_complet"),
        "jurisdiction": "fr",
        "company_number": best.get("siren"),
        "source_url": f"https://annuaire-entreprises.data.gouv.fr/entreprise/{best.get('siren')}",
    }


def search_company_companies_house(name: str):
    """Royaume-Uni : Companies House API — gratuite, clé requise (auth HTTP basic, mot de passe vide)."""
    if not COMPANIES_HOUSE_API_KEY:
        return None

    url = "https://api.company-information.service.gov.uk/search/companies"
    resp = requests.get(
        url, params={"q": name, "items_per_page": 1},
        auth=(COMPANIES_HOUSE_API_KEY, ""), timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return None

    best = items[0]
    return {
        "legal_name": best.get("title"),
        "jurisdiction": "gb",
        "company_number": best.get("company_number"),
        "source_url": f"https://find-and-update.company-information.service.gov.uk/company/{best.get('company_number')}",
    }


def search_company_norway_brreg(name: str):
    """Norvège : Brønnøysundregistrene (Enhetsregisteret) — gratuite, sans clé."""
    url = "https://data.brreg.no/enhetsregisteret/api/enheter"
    resp = requests.get(url, params={"navn": name, "size": 1}, timeout=15)
    resp.raise_for_status()
    entities = resp.json().get("_embedded", {}).get("enheter", [])
    if not entities:
        return None

    best = entities[0]
    org_number = best.get("organisasjonsnummer")
    return {
        "legal_name": best.get("navn"),
        "jurisdiction": "no",
        "company_number": org_number,
        "source_url": f"https://www.brreg.no/enhet/{org_number}",
    }


# ---------------------------------------------------------------------------
# 2 ter. Singapour : ACRA via l'API officielle de téléchargement filtré
# ---------------------------------------------------------------------------

def search_company_singapore_acra(name: str, max_polls: int = 10, poll_interval: float = 2.0):
    """
    Singapour : ACRA via data.gov.sg — gratuite, sans clé pour usage non-production
    (limite : 5 requêtes/minute sans clé API ; créez-en une sur data.gov.sg pour la prod).

    Contrairement aux autres connecteurs, il n'existe pas d'endpoint de recherche
    instantanée : l'API officielle "Download Dataset" (documentée sur
    guide.data.gov.sg) fonctionne en 2 temps :
      1. initiate-download avec un filtre ILIKE sur entity_name
      2. poll-download jusqu'à obtenir l'URL du CSV filtré, qu'on télécharge et parse

    Dataset utilisé : "Entities Registered with ACRA" (2,1M+ entités)
    resource_id : d_3f960c10fed6145404ca7b821f263b87
    """
    dataset_id = "d_3f960c10fed6145404ca7b821f263b87"
    base = f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}"
    payload = {
        "columnNames": ["uen", "entity_name", "entity_type_desc", "uen_status_desc"],
        "filters": [{"columnName": "entity_name", "type": "ILIKE", "value": name}],
    }

    initiate = requests.get(f"{base}/initiate-download", json=payload, timeout=15)
    initiate.raise_for_status()

    download_url = None
    for _ in range(max_polls):
        time.sleep(poll_interval)
        poll = requests.get(f"{base}/poll-download", json=payload, timeout=15)
        poll.raise_for_status()
        poll_data = poll.json().get("data", {})
        if poll_data.get("status") == "COMPLETED":
            download_url = poll_data.get("url")
            break
    if not download_url:
        return None  # toujours en cours de génération après max_polls tentatives

    csv_resp = requests.get(download_url, timeout=30)
    csv_resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(csv_resp.text))
    first_row = next(reader, None)
    if not first_row:
        return None

    return {
        "legal_name": first_row.get("entity_name"),
        "jurisdiction": "sg",
        "company_number": first_row.get("uen"),
        "source_url": "https://data.gov.sg/datasets/d_3f960c10fed6145404ca7b821f263b87/view",
    }


# ---------------------------------------------------------------------------
# 2 quater. Belgique : KBO/BCE Open Data — SEULE source de ce script qui fournit
# de vrais contacts (téléphone/email/site) directement au niveau registre.
#
# Différence importante avec tous les autres connecteurs : il n'existe PAS
# d'API de recherche en direct. FPS Economy publie chaque mois un export
# complet (ZIP) après inscription gratuite (vérification email requise) :
#   https://kbopub.economie.fgov.be/kbo-open-data/login
# Téléchargez et décompressez le ZIP, puis pointez la variable d'environnement
# BELGIUM_KBO_DATA_DIR vers le dossier contenant denomination.csv et contact.csv.
#
# Attention : le taux de remplissage des contacts est faible (beaucoup
# d'entreprises n'ont ni téléphone ni email renseigné) — traitez ces champs
# comme un enrichissement bonus, pas comme une source fiable à 100%.
# Les conditions d'usage de FPS Economy interdisent le marketing direct à
# partir de données personnelles issues de ce jeu de données.
# ---------------------------------------------------------------------------

BELGIUM_KBO_DATA_DIR = os.environ.get("BELGIUM_KBO_DATA_DIR")
_belgium_kbo_cache = {"denominations": None, "contacts": None}


def _load_belgium_kbo_data():
    """Charge une seule fois (mise en cache mémoire) les fichiers de l'extrait KBO Open Data."""
    if _belgium_kbo_cache["denominations"] is not None:
        return

    if not BELGIUM_KBO_DATA_DIR or not os.path.isdir(BELGIUM_KBO_DATA_DIR):
        raise RuntimeError(
            "BELGIUM_KBO_DATA_DIR non configuré ou introuvable. Le connecteur Belgique "
            "n'a pas d'API de recherche en direct : inscrivez-vous sur "
            "https://kbopub.economie.fgov.be/kbo-open-data/login, téléchargez l'extrait "
            "mensuel (ZIP), décompressez-le, et pointez BELGIUM_KBO_DATA_DIR vers ce dossier."
        )

    denominations = {}
    with open(os.path.join(BELGIUM_KBO_DATA_DIR, "denomination.csv"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            denominations.setdefault(row["EntityNumber"], []).append(row["Denomination"])

    contacts = {}
    with open(os.path.join(BELGIUM_KBO_DATA_DIR, "contact.csv"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            contacts.setdefault(row["EntityNumber"], {})[row["ContactType"]] = row["Value"]

    _belgium_kbo_cache["denominations"] = denominations
    _belgium_kbo_cache["contacts"] = contacts


def search_company_belgium_kbo(name: str):
    """Belgique : recherche par sous-chaîne dans l'extrait KBO/BCE Open Data chargé localement."""
    _load_belgium_kbo_data()
    name_lower = name.lower()
    denominations = _belgium_kbo_cache["denominations"]
    contacts = _belgium_kbo_cache["contacts"]

    for entity_number, names in denominations.items():
        for denom in names:
            if name_lower in denom.lower():
                contact = contacts.get(entity_number, {})
                return {
                    "legal_name": denom,
                    "jurisdiction": "be",
                    "company_number": entity_number,
                    "source_url": f"https://kbopub.economie.fgov.be/kbopub/toonondernemingps.html?ondernemingsnummer={entity_number}",
                    "registry_phone": contact.get("TEL"),
                    "registry_email": contact.get("EMAIL"),
                    "registry_website": contact.get("WEB"),
                }
    return None


# ---------------------------------------------------------------------------
# GLOBAL CORPORATE DATA ENGINE
# ---------------------------------------------------------------------------
OPEN_CORPORATE_SOURCES = {
    "ireland_cro":{"type":"registry","name":"Ireland CRO Open Data","jurisdiction":"IE","url":"https://opendata.cro.ie/dataset/companies","license":"CC BY 4.0","method":"bulk/API","policy":"GREEN"},
    "estonia_ebr":{"type":"registry","name":"Estonia e-Business Register","jurisdiction":"EE","url":"https://avaandmed.ariregister.rik.ee/en/","license":"Official API terms","method":"API","policy":"ORANGE","notes":"API agreement/authentication required."},
    "japan_corporate_number":{"type":"registry","name":"Japan Corporate Number Web-API","jurisdiction":"JP","url":"https://www.houjin-bangou.nta.go.jp/webapi/index.html","license":"Official API terms","method":"REST API","policy":"ORANGE"},
    "brazil_cnpj":{"type":"registry","name":"Brazil Receita Federal CNPJ Open Data","jurisdiction":"BR","url":"https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/dados-abertos/cadastros","license":"Official open data","method":"bulk files","policy":"ORANGE"},
    "sec_edgar":{"type":"filings","name":"SEC EDGAR APIs","jurisdiction":"US","url":"https://www.sec.gov/search-filings/edgar-application-programming-interfaces","license":"SEC public data / terms","method":"REST API","policy":"GREEN"},
    "gleif":{"type":"entity_resolution","name":"GLEIF LEI Data","jurisdiction":"GLOBAL","url":"https://www.gleif.org/en/lei-data/gleif-api","license":"GLEIF terms","method":"REST API","policy":"GREEN"},
    "opencorporates":{"type":"aggregator","name":"OpenCorporates","jurisdiction":"GLOBAL","url":"https://opencorporates.com/","license":"API/data terms","method":"REST API","policy":"ORANGE"},
    "ted":{"type":"procurement","name":"TED Open Data / API","jurisdiction":"EU","url":"https://developer.ted.europa.eu/","license":"EU open data / reuse terms","method":"API/SPARQL/XML","policy":"GREEN"},
    "world_bank_procurement":{"type":"procurement","name":"World Bank Procurement","jurisdiction":"GLOBAL","url":"https://projects.worldbank.org/","license":"Official data terms","method":"API/data","policy":"GREEN"},
    "ungm":{"type":"procurement","name":"UNGM","jurisdiction":"GLOBAL","url":"https://www.ungm.org/Developer","license":"Partner/API terms","method":"API","policy":"ORANGE","notes":"Use only approved API access."},
    "opensanctions":{"type":"risk","name":"OpenSanctions","jurisdiction":"GLOBAL","url":"https://www.opensanctions.org/","license":"Dataset-specific licence","method":"API/datasets","policy":"ORANGE"},
    "denmark_cvr":{"type":"registry","name":"Denmark CVR","jurisdiction":"DK","url":"https://datacvr.virk.dk/","license":"Official service terms","method":"registry/API/data","policy":"ORANGE"},
    "switzerland_zefix":{"type":"registry","name":"Switzerland Zefix","jurisdiction":"CH","url":"https://www.zefix.ch/","license":"Official service terms","method":"search/service","policy":"ORANGE"},
    "australia_abr":{"type":"registry","name":"Australia ABR / ABN Lookup","jurisdiction":"AU","url":"https://abr.business.gov.au/","license":"Official service terms","method":"API","policy":"ORANGE"},
    "new_zealand_companies":{"type":"registry","name":"New Zealand Companies Register","jurisdiction":"NZ","url":"https://companies-register.companiesoffice.govt.nz/","license":"Official service terms","method":"API/search","policy":"ORANGE"},
    "south_africa_cipc":{"type":"registry","name":"South Africa CIPC","jurisdiction":"ZA","url":"https://www.cipc.co.za/","license":"Official service terms","method":"service","policy":"ORANGE"},
    "netherlands_kvk":{"type":"registry","name":"Netherlands KVK","jurisdiction":"NL","url":"https://www.kvk.nl/","license":"Official API terms","method":"REST API","policy":"ORANGE","notes":"API key required."},
    "nigeria_cac":{"type":"registry","name":"Nigeria CAC","jurisdiction":"NG","url":"https://www.cac.gov.ng/","license":"Official service/API terms","method":"API/service","policy":"ORANGE","notes":"Authenticated VAS API."},
    "ghana_orc":{"type":"registry","name":"Ghana Office of Registrar of Companies","jurisdiction":"GH","url":"https://www.orc.gov.gh/","license":"Official service terms","method":"online service","policy":"ORANGE"},
    "kenya_brs":{"type":"registry","name":"Kenya Business Registration Service","jurisdiction":"KE","url":"https://brsv2.ecitizen.go.ke/","license":"Official service terms","method":"online service","policy":"ORANGE"},
}

def register_open_sources(conn):
    now=datetime.now(timezone.utc).isoformat()
    for sid,cfg in OPEN_CORPORATE_SOURCES.items():
        conn.execute("""INSERT OR REPLACE INTO source_registry
        (source_id,source_type,source_name,jurisdiction,source_url,source_license,collection_method,
         automated_access,personal_data,commercial_use_allowed,marketing_allowed,policy_class,terms_checked_at,notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
            sid,cfg.get("type"),cfg.get("name"),cfg.get("jurisdiction"),cfg.get("url"),cfg.get("license"),
            cfg.get("method"),"yes", "company-level", "review", "review", cfg.get("policy"), now, cfg.get("notes")))
    conn.commit()

def search_company_ireland_cro(name: str):
    url="https://opendata.cro.ie/api/3/action/datastore_search"
    params={"resource_id":"3fef41bc-b8f4-4b10-8434-ce51c29b1bba","limit":5,"q":name}
    r=requests.get(url,params=params,timeout=20); r.raise_for_status()
    rows=r.json().get("result",{}).get("records",[])
    if not rows: return None
    x=rows[0]
    return {"legal_name":x.get("company_name"),"jurisdiction":"ie","company_number":x.get("company_num"),"source_url":"https://opendata.cro.ie/dataset/companies"}

def search_company_japan_corporate_number(name: str):
    app_id=os.environ.get("JAPAN_CORPORATE_NUMBER_APP_ID")
    if not app_id: return None
    url="https://www.houjin-bangou.nta.go.jp/webapi/api/quickSearch"
    params={"id":app_id,"name":name,"type":"01","mode":"2","from":1,"to":1}
    r=requests.get(url,params=params,timeout=20); r.raise_for_status()
    if r.text.lstrip().startswith("{"):
        rows=r.json().get("data",[]) or r.json().get("results",[])
        if rows:
            x=rows[0]; return {"legal_name":x.get("name"),"jurisdiction":"jp","company_number":x.get("corporateNumber"),"source_url":"https://www.houjin-bangou.nta.go.jp/"}
    return None

def search_company_sec(name: str):
    ua=os.environ.get("SEC_USER_AGENT")
    if not ua: return None
    r=requests.get("https://www.sec.gov/files/company_tickers.json",headers={"User-Agent":ua},timeout=20); r.raise_for_status()
    target=name.lower()
    for x in r.json().values():
        title=(x.get("title") or "")
        if target in title.lower() or title.lower() in target:
            cik=str(x.get("cik_str")).zfill(10)
            return {"legal_name":title,"jurisdiction":"us","company_number":cik,"source_url":f"https://www.sec.gov/edgar/browse/?CIK={cik}"}
    return None

def lookup_gleif_relationships(lei: str):
    out=[]
    if not lei: return out
    for rel in ("direct-parent","ultimate-parent","direct-child","ultimate-child"):
        try:
            r=requests.get(f"https://api.gleif.org/api/v1/lei-records/{quote(lei)}/relationships/{rel}",params={"page[size]":100},timeout=20)
            if r.status_code==404: continue
            r.raise_for_status()
            for item in r.json().get("data",[]):
                attrs=item.get("attributes",{}); rellei=attrs.get("lei") or item.get("id")
                if rellei: out.append((rel,rellei))
        except requests.RequestException: pass
    return out

def collect_gleif_relationships(company_id: str, lei: str, conn=None):
    own=False
    if conn is None: conn=init_db(); own=True
    now=datetime.now(timezone.utc).isoformat(); rows=lookup_gleif_relationships(lei)
    for rel,plei in rows:
        conn.execute("INSERT INTO corporate_relationships VALUES (?,?,?,?,?,?,?)",(str(uuid.uuid4()),company_id,plei,rel,"GLEIF",f"https://api.gleif.org/api/v1/lei-records/{lei}",now))
    conn.commit()
    if own: conn.close()
    return len(rows)

def _first_value(obj, *keys):
    """Return the first non-empty value, including nested/list TED values."""
    if not isinstance(obj, dict):
        return None
    for key in keys:
        value=obj.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, list) and value:
                first=value[0]
                if isinstance(first, dict):
                    return first.get("value") or first.get("name") or first.get("text") or str(first)
                return first
            if isinstance(value, dict):
                return value.get("value") or value.get("name") or value.get("text") or str(value)
            return value
    return None


def search_ted_notices(query_text: str, limit: int=100, fields=None, page: int=1,
                       pagination_mode: str="PAGE_NUMBER", iteration_token: str=None):
    """Search TED API v3 published notices.

    PAGE_NUMBER is capped by TED at 15,000 notices; ITERATION can continue
    through all matching notices using the returned iterationNextToken.
    """
    if pagination_mode not in {"PAGE_NUMBER", "ITERATION"}:
        raise ValueError("pagination_mode must be PAGE_NUMBER or ITERATION")
    fields = fields or [
        "publication-number", "notice-identifier", "notice-title", "notice-type",
        "publication-date", "dispatch-date", "buyer-name", "buyer-country",
        "buyer-city", "business-name", "business-country", "business-identifier",
        "main-classification-lot", "contract-value-notice", "deadline-receipt-tender-date-lot"
    ]
    payload={
        "query": query_text or "*",
        "fields": fields,
        "page": page,
        "limit": min(int(limit), 250),
        "scope": "ACTIVE",
        "checkQuerySyntax": True,
        "paginationMode": pagination_mode,
    }
    if iteration_token:
        payload["iterationNextToken"] = iteration_token
    r=requests.post("https://api.ted.europa.eu/v3/notices/search",json=payload,timeout=60)
    r.raise_for_status()
    return r.json()


def _ted_items(data):
    if isinstance(data, list): return data
    if not isinstance(data, dict): return []
    for key in ("notices", "results", "data", "items"):
        value=data.get(key)
        if isinstance(value, list): return value
    # Some responses wrap the list in a result object.
    for key in ("response", "result"):
        value=data.get(key)
        if isinstance(value, dict):
            for sub in ("notices", "results", "data", "items"):
                if isinstance(value.get(sub), list): return value[sub]
    return []


def import_ted_results(conn,data):
    now=datetime.now(timezone.utc).isoformat(); count=0
    for n in _ted_items(data):
        if not isinstance(n,dict): continue
        nid=_first_value(n,"publication-number","notice-identifier","noticeId","id")
        if not nid: continue
        url=_first_value(n,"notice-url","url") or f"https://ted.europa.eu/en/notice/-/detail/{nid}"
        buyer=_first_value(n,"buyer-name","buyerName","organisation-name-buyer")
        supplier=_first_value(n,"business-name","winner-name","supplierName")
        country=_first_value(n,"buyer-country","country")
        cpv=_first_value(n,"main-classification-lot","main-classification-proc","cpv","cpvCode")
        title=_first_value(n,"notice-title","title","contractTitle")
        date=_first_value(n,"publication-date","dispatch-date","date")
        value=_first_value(n,"contract-value-notice","result-value-notice","contract-value","value")
        conn.execute("INSERT OR REPLACE INTO procurement_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(
            str(uuid.uuid4()),str(nid),"TED",url,buyer,supplier,country,cpv,title,date,str(value or ""),json.dumps(n,ensure_ascii=False),now))
        count+=1
    conn.commit(); return count


def import_ted_query(conn, query_text: str, pages: int=1, limit: int=100, iteration=False):
    total=0; token=None
    mode="ITERATION" if iteration else "PAGE_NUMBER"
    for page in range(1, max(1,pages)+1):
        data=search_ted_notices(query_text,limit=limit,page=page,pagination_mode=mode,iteration_token=token)
        total += import_ted_results(conn,data)
        if mode != "ITERATION":
            if len(_ted_items(data)) < min(limit,250): break
        token=data.get("iterationNextToken") or data.get("nextToken") or data.get("iterationNextTokenValue")
        if mode == "ITERATION" and not token: break
    return total


def search_world_bank_procurement(query_text: str="", country_code: str=None, rows: int=100, offset: int=0):
    """Retrieve World Bank financed procurement notices from the official API.

    The API is public; the Finances One dataset is updated daily and licensed
    CC BY 4.0. Parameters are intentionally conservative so callers can page.
    """
    params={"format":"json", "rows":min(int(rows),1000), "os":max(0,int(offset))}
    if query_text:
        params["qterm"] = query_text
    if country_code:
        params["countrycode"] = country_code.upper()
    r=requests.get("https://search.worldbank.org/api/procnotices",params=params,timeout=60)
    r.raise_for_status()
    return r.json()


def _world_bank_items(data):
    if isinstance(data,list): return data
    if not isinstance(data,dict): return []
    for key in ("procnotices","procurementNotices","notices","results","data","items"):
        value=data.get(key)
        if isinstance(value,list): return value
        if isinstance(value,dict):
            # Common World Bank responses can be keyed by notice id.
            return list(value.values())
    return []


def import_world_bank_results(conn,data):
    now=datetime.now(timezone.utc).isoformat(); count=0
    for n in _world_bank_items(data):
        if not isinstance(n,dict): continue
        nid=_first_value(n,"notice_number","noticeNumber","id","noticeid","procurement_notice_id")
        if not nid: continue
        project=_first_value(n,"project_id","projectid","projectId")
        source_url=_first_value(n,"url","notice_url","noticeUrl")
        if not source_url:
            source_url=(f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}" if str(nid).startswith("OP") else "https://search.worldbank.org/api/procnotices")
        buyer=_first_value(n,"borrower","buyer_name","borrower_name","procuring_entity","entity_name")
        supplier=_first_value(n,"supplier","supplier_name","awardee")
        country=_first_value(n,"country","country_name","countrycode")
        cpv=_first_value(n,"procurement_code","cpv_code","major_sector")
        title=_first_value(n,"title","notice_title","project_name","description")
        date=_first_value(n,"publication_date","notice_date","published_date")
        deadline=_first_value(n,"deadline_date","submission_deadline")
        value=_first_value(n,"estimated_amount","contract_value","award_amount","amount")
        raw=dict(n)
        raw["project_id"] = project
        if deadline: raw["deadline_date"] = deadline
        conn.execute("INSERT OR REPLACE INTO procurement_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(
            str(uuid.uuid4()),str(nid),"World Bank Procurement",source_url,buyer,supplier,country,cpv,title,date,str(value or ""),json.dumps(raw,ensure_ascii=False),now))
        count+=1
    conn.commit(); return count


def import_world_bank_query(conn, query_text="", country_code=None, pages=1, rows=100):
    total=0
    for page in range(max(1,int(pages))):
        data=search_world_bank_procurement(query_text,country_code,rows=rows,offset=page*min(int(rows),1000))
        got=import_world_bank_results(conn,data); total+=got
        if got < min(int(rows),1000): break
    return total

# Routeur : pour chaque code pays, la source nationale prioritaire à essayer
# avant de retomber sur OpenCorporates. Étendez ce dict au fur et à mesure
# que vous ajoutez de nouvelles sources nationales.

def _simple_json_search(url, params, name_key, number_key, jurisdiction, source_url):
    r=requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data=r.json()
    rows=data.get("results") or data.get("data") or data.get("items") or data.get("companies") or []
    if isinstance(rows, dict): rows=list(rows.values())
    if not rows: return None
    x=rows[0]
    return {"legal_name": x.get(name_key) or x.get("name") or x.get("title"),
            "jurisdiction": jurisdiction, "company_number": x.get(number_key) or x.get("company_number") or x.get("registration_number"),
            "source_url": source_url}


def search_company_netherlands_kvk(name: str):
    # Official KVK APIs require credentials; keep this connector credential-gated.
    key=os.environ.get("KVK_API_KEY")
    if not key: return None
    url="https://api.kvk.nl/api/v2/zoeken"
    r=requests.get(url, params={"q":name}, headers={"apikey":key}, timeout=20)
    r.raise_for_status(); rows=r.json().get("resultaten",[])
    if not rows: return None
    x=rows[0]
    return {"legal_name":x.get("naam"),"jurisdiction":"nl","company_number":x.get("kvkNummer"),"source_url":"https://www.kvk.nl/zoeken/"}


def search_company_cipc(name: str):
    # CIPC's official Companies API is OAuth/subscription based.
    token=os.environ.get("CIPC_ACCESS_TOKEN")
    if not token: return None
    url=os.environ.get("CIPC_COMPANIES_SEARCH_URL","https://api.cipc.co.za/companies")
    r=requests.get(url,params={"name":name},headers={"Authorization":f"Bearer {token}"},timeout=20)
    r.raise_for_status(); data=r.json(); rows=data.get("results") or data.get("data") or []
    if not rows: return None
    x=rows[0]
    return {"legal_name":x.get("name") or x.get("companyName"),"jurisdiction":"za","company_number":x.get("enterpriseNumber") or x.get("registrationNumber"),"source_url":"https://developer.cipc.co.za/"}


def search_company_nigeria_cac(name: str):
    token=os.environ.get("CAC_ACCESS_TOKEN")
    if not token: return None
    url=os.environ.get("CAC_SEARCH_URL","https://vas.cac.gov.ng/api")
    r=requests.get(url,params={"name":name},headers={"Authorization":f"Bearer {token}"},timeout=20)
    if r.status_code in (404,405): return None
    r.raise_for_status(); data=r.json(); rows=data.get("results") or data.get("data") or data.get("companies") or []
    if not rows: return None
    x=rows[0]
    return {"legal_name":x.get("name") or x.get("companyName"),"jurisdiction":"ng","company_number":x.get("rcNumber") or x.get("registrationNumber"),"source_url":"https://vas.cac.gov.ng/"}


def search_company_ghana_orc(name: str):
    # ORC exposes online search/services, but no stable public search API was verified.
    return None


def search_company_kenya_brs(name: str):
    # BRS requires authenticated service access; no public search API is assumed.
    return None

REGISTRY_ROUTES = {
    "fr": search_company_france_gouv,
    "gb": search_company_companies_house,
    "uk": search_company_companies_house,
    "no": search_company_norway_brreg,
    "sg": search_company_singapore_acra,
    "be": search_company_belgium_kbo,
    "ie": search_company_ireland_cro,
    "jp": search_company_japan_corporate_number,
    "us": search_company_sec,
    "nl": search_company_netherlands_kvk,
    "za": search_company_cipc,
    "ng": search_company_nigeria_cac,
    "gh": search_company_ghana_orc,
    "ke": search_company_kenya_brs,
}

# Sources candidates non encore connectées (API existante mais nécessitant soit
# une clé/inscription payante, soit un travail de mapping supplémentaire que je
# n'ai pas pu vérifier de façon fiable) :
#   - Estonie (e-Business Register) : ariregister.rik.ee — recherche basique libre,
#     API complète soumise à inscription.
#   - Allemagne (Handelsregister) : pas d'API officielle gratuite structurée.
#   - Inde (MCA21) : accès principalement payant / captcha sur le portail public.
#   - Afrique du Sud (CIPC) : pas d'API publique stable à ce jour.
#
# Sources explicitement écartées après recherche approfondie sur les chambres
# de commerce (voir rapport de recherche) — à ne PAS tenter de connecter :
#   - Pays-Bas (KVK Open Dataset) et Allemagne (IHK Berlin Gewerbedaten) :
#     "open data" mais volontairement ANONYMISÉ — nom d'entreprise et contacts
#     explicitement exclus pour raisons de protection des données.
#   - France (AEF, réseau CCI) et Italie (InfoCamere/visure) : contiennent de
#     vrais contacts nominatifs, mais accès PAYANT (pas open data).
#   - Autriche (WKO), Dubaï, Maroc (ASMEX/AMDIE), Ghana (GEPA), Nigeria (NEPC),
#     Japon (JETRO e-Venue) : annuaires HTML consultables, aucun export/API
#     officiel — nécessiteraient du scraping, exclu par principe.
# Pour chacune, le principe reste le même : écrire search_company_<pays>(name)
# et l'ajouter au dict ci-dessus.


def search_company(name: str, jurisdiction: str = None):
    """Routeur multi-sources : tente la source nationale la plus fiable, puis OpenCorporates en repli."""
    if jurisdiction:
        national_fn = REGISTRY_ROUTES.get(jurisdiction.lower())
        if national_fn:
            result = national_fn(name)
            if result:
                result["source_name"] = national_fn.__name__.replace("search_company_", "")
                return result

    result = search_company_opencorporates(name, jurisdiction)
    if result:
        result["source_url"] = result.pop("opencorporates_url", None)
        result["source_name"] = "opencorporates"
    return result


# ---------------------------------------------------------------------------
# 3. Étape 2 : résolution du LEI via GLEIF (aide à la désambiguïsation)
# ---------------------------------------------------------------------------

def lookup_lei_gleif(legal_name: str):
    """Recherche un identifiant LEI correspondant au nom légal via l'API GLEIF (gratuite, sans clé)."""
    url = "https://api.gleif.org/api/v1/lei-records"
    params = {"filter[entity.legalName]": legal_name, "page[size]": 1}

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    records = data.get("data", [])
    if not records:
        return None

    return records[0]["attributes"]["lei"]


# ---------------------------------------------------------------------------
# 4. Étape 3 : enrichissement des contacts via Hunter.io (domain search)
# ---------------------------------------------------------------------------

def enrich_contacts_hunter(domain: str, limit: int = 10):
    """
    Retourne un tuple (contacts, hq_location) pour un domaine donné.

    contacts : liste de dicts (nom, poste, email, téléphone si disponible, score de confiance)
    hq_location : dict {city, state, country} du SIÈGE de l'entreprise tel que connu de Hunter.

    Attention, deux limites importantes :
    - Le téléphone par contact ('phone_number') est rarement renseigné sur le plan
      gratuit Hunter.io — la plupart des contacts n'auront que l'email.
    - hq_location est la localisation du SIÈGE SOCIAL, pas celle de chaque personne.
      Hunter (comme la plupart des outils gratuits/freemium) ne fournit pas la ville
      où chaque contact individuel est basé — seulement l'adresse de l'entreprise.
      Pour de grandes entreprises multi-sites, un contact donné peut très bien être
      basé ailleurs que le siège renvoyé ici.
    """
    if not HUNTER_API_KEY:
        raise RuntimeError("HUNTER_API_KEY manquant : requis pour l'enrichissement de contacts.")

    url = "https://api.hunter.io/v2/domain-search"
    params = {"domain": domain, "api_key": HUNTER_API_KEY, "limit": limit}

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("data", {})

    hq_location = {
        "city": data.get("city"),
        "state": data.get("state"),
        "country": data.get("country"),
    }

    contacts = []
    for person in data.get("emails", []):
        contacts.append({
            "full_name": f"{person.get('first_name', '')} {person.get('last_name', '')}".strip(),
            "job_title": person.get("position"),
            "email": person.get("value"),
            "phone": person.get("phone_number"),
            "confidence": person.get("confidence"),
            "linkedin_url": person.get("linkedin"),
        })
    return contacts, hq_location


def verify_email_hunter(email: str):
    """Vérifie la délivrabilité d'un email via Hunter.io email-verifier."""
    url = "https://api.hunter.io/v2/email-verifier"
    params = {"email": email, "api_key": HUNTER_API_KEY}

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("status", "unknown")


# ---------------------------------------------------------------------------
# 5. Orchestration du pipeline complet
# ---------------------------------------------------------------------------

def run_pipeline(company_name: str, jurisdiction: str = None, domain: str = None):
    conn = init_db_v5()
    now = datetime.now(timezone.utc).isoformat()

    print(f"[1/4] Recherche entreprise (routeur multi-sources) : {company_name}")
    company_data = search_company(company_name, jurisdiction)
    if not company_data:
        print("Aucune entreprise trouvée sur les sources disponibles.")
        return
    print(f"       -> trouvé via source : {company_data['source_name']}")

    print(f"[2/4] Résolution LEI (GLEIF) pour : {company_data['legal_name']}")
    lei = lookup_lei_gleif(company_data["legal_name"])

    company_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO companies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            company_id, company_data["legal_name"], lei, company_data["jurisdiction"],
            domain, company_data["source_name"], company_data["source_url"], now,
            company_data.get("registry_phone"), company_data.get("registry_email"),
            company_data.get("registry_website"), None, None, None,
        ),
    )
    init_v5_schema(conn)
    conn.execute("UPDATE companies SET company_number=?, normalized_name=? WHERE company_id=?", (company_data.get("company_number"), normalize_company_name(company_data.get("legal_name")), company_id))
    add_identifier(conn, company_id, "company_number", company_data.get("company_number"), company_data.get("source_name"), company_data.get("source_url"), 95)
    add_company_evidence(conn, company_id, "legal_name", company_data.get("legal_name"), company_data.get("source_name"), company_data.get("source_url"), 95)
    if lei: add_identifier(conn, company_id, "LEI", lei, "GLEIF", f"https://api.gleif.org/api/v1/lei-records/{lei}", 99)
    conn.commit()
    enrich_company_master(company_id, conn)

    if company_data.get("registry_email") or company_data.get("registry_phone"):
        email_display = company_data.get("registry_email") or "(pas d'email)"
        phone_display = company_data.get("registry_phone") or "(pas de tél)"
        print(f"       -> contact registre trouvé : {email_display} / {phone_display}")

    if not domain:
        print("Aucun domaine fourni : l'étape d'enrichissement de contacts est ignorée.")
        print("Relancez avec --domain exemple.com pour activer l'enrichissement Hunter.io.")
        return

    print(f"[3/4] Enrichissement des contacts (Hunter.io) pour le domaine : {domain}")
    contacts, hq_location = enrich_contacts_hunter(domain)

    conn.execute(
        "UPDATE companies SET hq_city=?, hq_state=?, hq_country=? WHERE company_id=?",
        (hq_location.get("city"), hq_location.get("state"), hq_location.get("country"), company_id),
    )
    conn.commit()
    if hq_location.get("country"):
        loc_parts = [p for p in [hq_location.get("city"), hq_location.get("state"), hq_location.get("country")] if p]
        print(f"       -> siège localisé (Hunter) : {', '.join(loc_parts)} "
              f"(localisation du SIÈGE, pas de chaque contact individuellement)")

    print(f"[4/4] Vérification et stockage de {len(contacts)} contact(s)")
    for c in contacts:
        status = "unknown"
        if c["email"]:
            time.sleep(1)  # respecter les limites de rate-limit de l'API
            status = verify_email_hunter(c["email"])

        conn.execute(
            "INSERT INTO contacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()), company_id, c["full_name"], c["job_title"], c["email"],
                c.get("phone"), c["confidence"], status, c["linkedin_url"],
                "interet_legitime_b2b", "Hunter.io", now, 0,
            ),
        )
    conn.commit()
    conn.close()

    print(f"\nTerminé. Résultats stockés dans {DB_PATH} (tables 'companies' et 'contacts').")


# ---------------------------------------------------------------------------
# 5 bis. Sources chambres : GREEN / ORANGE policy-controlled collectors
# ---------------------------------------------------------------------------
# IMPORTANT : un annuaire public n'est pas automatiquement "libre de scraping".
# Les sources ORANGE ci-dessous sont activées uniquement parce qu'aucune
# interdiction explicite de collecte automatisée n'a été identifiée lors de la
# revue actuelle. Elles restent soumises aux conditions locales, robots.txt,
# rate limits et à une revue périodique des conditions d'utilisation.
# Les sources RED ne sont jamais appelées par ce collecteur.

CHAMBER_SOURCE_POLICY = {
    # ORANGE: public member directory; collect company-level public data only.
    "ahk_uae": {
        "network": "AHK",
        "country": "United Arab Emirates",
        "status": "ORANGE",
        "directory_url": "https://vae.ahk.de/en/members/member-directory",
        "terms_url": "https://vae.ahk.de/en/general-terms-and-conditions",
        "page_param": "members_directory_filter[page]",
        "start_page": 1,
        "max_pages": 45,
        "company_row_selector": "table tr",
    },
    "ahk_saudi": {
        "network": "AHK",
        "country": "Saudi Arabia",
        "status": "ORANGE",
        "directory_url": "https://saudiarabien.ahk.de/en/members/members-directory",
        "terms_url": "https://saudiarabien.ahk.de/en/privacy-policy",
        "page_param": "members_directory_filter[page]",
        "start_page": 1,
        "max_pages": 100,
        "company_row_selector": "table tr",
    },
    "amcham_za": {
        "network": "AmCham",
        "country": "South Africa",
        "status": "ORANGE",
        "directory_url": "https://amcham.co.za/membership-directory/corporate",
        "terms_url": "https://www.amcham.co.za/terms-and-conditions",
        "page_param": None,
        "start_page": 1,
        "max_pages": 1,
        "company_row_selector": None,
    },
}

# Sources deliberately blocked after terms review. Kept here so they cannot be
# accidentally added to the collector without an explicit policy change.
CHAMBER_SOURCE_BLOCKLIST = {
    "amcham_oman": "RED: directory rules prohibit database/list creation and mass marketing use.",
    "cci_france_international": "RED: site legal notice restricts content to private/non-commercial use without permission.",
    "british_chamber_dubai": "RED: member data may not be used for unsolicited bulk marketing/mass mailings.",
}


def _html_rows(url, params=None, timeout=20):
    """Return HTML table rows as (text, hrefs), using stdlib only."""
    from html.parser import HTMLParser

    class RowParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows, self.row_text, self.row_links = [], [], []
            self.in_tr = False
            self.current = []
            self.links = []
            self.href = None
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "tr":
                self.in_tr = True; self.current = []; self.links = []
            elif self.in_tr and tag == "a":
                self.href = attrs.get("href")
            elif self.in_tr and tag in ("td", "th"):
                self.current.append(" ")
        def handle_data(self, data):
            if self.in_tr:
                text = " ".join(data.split())
                if text:
                    self.current.append(text)
        def handle_endtag(self, tag):
            if tag == "a":
                if self.href:
                    self.links.append(self.href)
                self.href = None
            elif tag == "tr" and self.in_tr:
                txt = " ".join(self.current).strip()
                if txt:
                    self.rows.append((txt, self.links[:]))
                self.in_tr = False

    r = requests.get(url, params=params, timeout=timeout, headers={
        "User-Agent": "CorporateContactsResearch/3.0 (+respectful-rate-limit)"
    })
    r.raise_for_status()
    parser = RowParser()
    parser.feed(r.text)
    return parser.rows, r.url


def _absolute_url(base_url, href):
    from urllib.parse import urljoin
    return urljoin(base_url, href)


def _domain_from_url(url):
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _insert_chamber_company(conn, item, now):
    """Insert/update a company record from a chamber directory entry."""
    import hashlib
    company_id = str(uuid.uuid5(uuid.NAMESPACE_URL, item["source_url"]))
    conn.execute("""
        INSERT OR IGNORE INTO companies
        (company_id, legal_name, lei, jurisdiction, primary_domain, source_name,
         source_url, retrieved_at, registry_phone, registry_email, registry_website,
         hq_city, hq_state, hq_country)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        company_id, item["legal_name"], None, item.get("jurisdiction"),
        item.get("domain"), item["source_name"], item["source_url"], now,
        None, None, item.get("website"), item.get("hq_city"),
        item.get("hq_state"), item.get("hq_country"),
    ))
    return company_id


def _collect_amcham_za(max_pages: int = 1, sleep_seconds: float = 1.5):
    """Collect company names + public company website links from AmCham South Africa."""
    from urllib.parse import urljoin
    cfg = CHAMBER_SOURCE_POLICY["amcham_za"]
    conn = init_db()
    now = datetime.now(timezone.utc).isoformat()
    seen = set()
    inserted = 0
    rows, final_url = _html_rows(cfg["directory_url"])

    # The directory exposes individual corporate member profile URLs. We then
    # visit each profile to obtain the member's own company website link.
    profile_urls = []
    for row_text, hrefs in rows:
        for href in hrefs:
            absolute = urljoin(final_url, href)
            if "/membership-directory/corporate/" in absolute and absolute not in profile_urls:
                profile_urls.append(absolute)

    print(f"Chamber source: amcham_za [ORANGE] — {cfg['directory_url']}")
    print(f"Found {len(profile_urls)} public corporate member profile link(s)")

    for profile_url in profile_urls:
        try:
            detail_rows, detail_url = _html_rows(profile_url)
        except requests.RequestException as exc:
            print(f"  skip {profile_url}: {exc}")
            continue

        # Profile pages have the company name in the page/table text and a
        # clearly external company website link.
        all_text = " ".join(text for text, _ in detail_rows)
        name = None
        for label in ("Membership Directory - Corporate",):
            if label in all_text:
                tail = all_text.split(label, 1)[-1].strip()
                if tail:
                    name = tail.split(" About ", 1)[0].strip()
                    break
        if not name:
            # Conservative fallback: first substantial row.
            candidates = [t for t, _ in detail_rows if len(t) > 2]
            name = candidates[0] if candidates else None
        if not name:
            time.sleep(sleep_seconds)
            continue

        website = None
        for _, hrefs in detail_rows:
            for href in hrefs:
                absolute = urljoin(detail_url, href)
                host = _domain_from_url(absolute) or ""
                if host and "amcham.co.za" not in host:
                    website = absolute
                    break
            if website:
                break
        if not website:
            time.sleep(sleep_seconds)
            continue

        key = (name.lower(), profile_url.lower())
        if key in seen:
            time.sleep(sleep_seconds)
            continue
        seen.add(key)

        item = {
            "legal_name": name,
            "jurisdiction": "za",
            "domain": _domain_from_url(website),
            "website": website,
            "source_name": "chamber:amcham_za",
            "source_url": profile_url,
        }
        _insert_chamber_company(conn, item, now)
        inserted += 1
        time.sleep(sleep_seconds)

    conn.commit()
    conn.close()
    print(f"Done: {inserted} AmCham South Africa company record(s) processed into {DB_PATH}.")
    return inserted


def collect_chamber_members(source_id: str, max_pages: int = None, sleep_seconds: float = 1.5):
    """Collect public company-level member data from an approved GREEN/ORANGE source.

    No personal contact harvesting is performed here. The collector stores only
    company name, public website/domain, source URL and chamber provenance.
    """
    source_id = source_id.lower()
    if source_id in CHAMBER_SOURCE_BLOCKLIST:
        raise RuntimeError(f"Source blocked: {CHAMBER_SOURCE_BLOCKLIST[source_id]}")
    cfg = CHAMBER_SOURCE_POLICY.get(source_id)
    if not cfg:
        raise ValueError(f"Unknown chamber source '{source_id}'. Available: {', '.join(CHAMBER_SOURCE_POLICY)}")
    if cfg.get("status") not in {"GREEN", "ORANGE"}:
        raise RuntimeError(f"Source is not enabled: {source_id}")
    if source_id == "amcham_za":
        return _collect_amcham_za(max_pages=max_pages or 1, sleep_seconds=sleep_seconds)

    pages = min(max_pages or cfg["max_pages"], cfg["max_pages"])
    conn = init_db()
    now = datetime.now(timezone.utc).isoformat()
    seen = set()
    inserted = 0

    print(f"Chamber source: {source_id} [{cfg['status']}] — {cfg['directory_url']}")
    print(f"Policy: company-level public data only; rate limit={sleep_seconds:.1f}s/request")

    for page in range(cfg["start_page"], cfg["start_page"] + pages):
        params = {cfg["page_param"]: page} if cfg.get("page_param") else None
        rows, final_url = _html_rows(cfg["directory_url"], params=params)
        if not rows:
            break
        page_new = 0

        for row_text, hrefs in rows:
            lower = row_text.lower()
            if "member" in lower and ("country" in lower or "city" in lower):
                continue
            website = next((h for h in hrefs if h.startswith(("http://", "https://"))
                            and not any(x in h.lower() for x in ("ahk.de", "amcham.co.za"))), None)
            if not website:
                continue
            parts = [p.strip() for p in row_text.split(" | ") if p.strip()]
            name = parts[0] if parts else None
            if not name or len(name) < 2:
                continue
            website = _absolute_url(final_url, website)
            key = (name.lower(), website.lower())
            if key in seen:
                continue
            seen.add(key)
            item = {
                "legal_name": name,
                "jurisdiction": None,
                "domain": _domain_from_url(website),
                "website": website,
                "source_name": f"chamber:{source_id}",
                "source_url": final_url,
            }
            _insert_chamber_company(conn, item, now)
            inserted += 1
            page_new += 1

        conn.commit()
        print(f"  page {page}: {page_new} company record(s)")
        if page_new == 0 and page > cfg["start_page"]:
            break
        time.sleep(sleep_seconds)

    conn.close()
    print(f"Done: {inserted} new chamber company record(s) processed into {DB_PATH}.")
    return inserted


# ---------------------------------------------------------------------------
# 6. Module de référence : annuaires B2B et chambres de commerce
#
# Ces sources (issues de votre document) n'ont PAS d'API publique gratuite.
# Les seuls accès automatisés existants sont des scrapers tiers payants qui
# contournent leurs conditions d'utilisation — je n'écris pas ce type de code,
# et je vous déconseille de l'utiliser (risque contractuel, blocage IP).
#
# Ce module sert de pense-bête structuré : pour une région donnée, il indique
# quelles sources vérifier manuellement en complément du pipeline API ci-dessus.
# Vous pouvez consulter ces annuaires vous-même, ou vérifier au cas par cas
# s'ils proposent un accès partenaire officiel (généralement payant).
# ---------------------------------------------------------------------------

MANUAL_DIRECTORIES = {
    "europe": [
        {"name": "Europages", "url": "https://www.europages.fr", "note": "B2B, fabricants/fournisseurs, 21 langues"},
        {"name": "CompanyLens", "url": "https://www.companylens.com", "note": "Agrégateur de registres européens"},
    ],
    "africa": [
        {"name": "AfricaBizInfo", "url": "https://africabizinfo.net", "note": "~5M listings, 55 pays, contact direct annoncé"},
        {"name": "Businesses Africa", "url": "https://businesses.africa", "note": "Fiches avec email+téléphone+site direct"},
        {"name": "All Business Africa", "url": "https://allbusiness.africa", "note": "Startups, PME, corporates"},
    ],
    "asia": [
        {"name": "Asia Pages", "url": "https://www.asiapages.net", "note": "Couverture large : Chine, Inde, Japon, ASEAN..."},
        {"name": "Asian Business Directory", "url": "https://www.asianbusinessdirectory.net", "note": "Par catégorie"},
        {"name": "BizSouthAsia", "url": "https://www.bizsouthasia.com", "note": "1,1M listings, Inde/Asie du Sud"},
        {"name": "APEACC Business Directory", "url": "https://www.apeacc.org", "note": "Asie-Pacifique"},
    ],
    "caribbean": [
        {"name": "TodosBiz", "url": "https://www.todosbiz.com", "note": "810k listings, 39 pays"},
        {"name": "CB Connect Caribbean", "url": "https://www.cbconnect.biz", "note": "Entreprises vérifiées"},
        {"name": "CaribFind", "url": "https://www.caribfind.com", "note": "Par catégorie/pays"},
        {"name": "Caribbean & Latin America Trade Directory", "url": "https://www.tradedirectory.com", "note": "B2B, fournisseurs"},
    ],
}

# Chambres de commerce et associations professionnelles : souvent la meilleure
# source de VRAIS contacts nominatifs (Company, Contact person, Position, Email,
# Phone), mais à traiter pays par pays — pas d'agrégateur global fiable identifié.
# Exemples cités dans votre document, à étendre selon vos priorités géographiques :
CHAMBERS_OF_COMMERCE_EXAMPLES = [
    {"name": "Trinidad & Tobago Chamber", "url": "https://www.chamber.org.tt", "region": "caribbean"},
    {"name": "IAC Caribbean", "url": "https://www.iaccaribbean.com", "region": "caribbean"},
]


def print_manual_directories(region: str):
    """Affiche les annuaires et chambres de commerce à vérifier manuellement pour une région donnée."""
    region = region.lower()
    dirs = MANUAL_DIRECTORIES.get(region, [])
    chambers = [c for c in CHAMBERS_OF_COMMERCE_EXAMPLES if c["region"] == region]

    if not dirs and not chambers:
        print(f"Aucune source référencée pour la région '{region}'. "
              f"Régions disponibles : {list(MANUAL_DIRECTORIES.keys())}")
        return

    print(f"\nSources à vérifier manuellement pour la région '{region}' (pas d'API — accès direct requis) :")
    for d in dirs:
        print(f"  - {d['name']:35s} {d['url']:40s} {d['note']}")
    if chambers:
        print("\nChambres de commerce / associations professionnelles (souvent les meilleurs contacts nominatifs) :")
        for c in chambers:
            print(f"  - {c['name']:35s} {c['url']}")


# ---------------------------------------------------------------------------
# 7. Chambres allemandes (AHK) et américaines (AmCham) — pays où AGS est présent
#
# Sources : réseau AHK (DIHK/ahk.de, ~150 bureaux dans ~92 pays) et réseau AmCham
# (U.S. Chamber of Commerce, "AmChams and Partners", ~130 chambres). Ce sont des
# annuaires d'organisations (nom de la chambre, site, email/téléphone du
# secrétariat) publiés par DIHK et l'US Chamber eux-mêmes — pas des annuaires de
# membres à scraper. Les URLs et coordonnées ci-dessous sont reprises telles que
# publiées sur ahk.de/en/locations et uschamber.com/program/international-affairs.
#
# Couverture : ~97 pays où AGS Worldwide Movers est présent (agsmovers.com/branches),
# croisés avec ces deux réseaux. Beaucoup de petits pays africains/caribéens
# n'ont ni AHK ni AmCham — c'est une donnée réelle, pas un oubli.
# ---------------------------------------------------------------------------

AGS_PRESENCE_COUNTRIES = [
    # Afrique (54)
    "algeria", "angola", "benin", "botswana", "burkina faso", "burundi", "cameroon",
    "cape verde", "central african republic", "chad", "comoros", "congo", "drc",
    "djibouti", "egypt", "equatorial guinea", "eritrea", "ethiopia", "gabon",
    "gambia", "ghana", "guinea conakry", "guinea-bissau", "ivory coast", "kenya",
    "lesotho", "liberia", "libya", "madagascar", "malawi", "mali", "mauritania",
    "mauritius", "morocco", "mozambique", "namibia", "nigeria", "rwanda",
    "sao tome", "senegal", "seychelles", "sierra leone", "somalia", "south africa",
    "south sudan", "sudan", "swaziland", "tanzania", "togo", "tunisia", "uganda",
    "zambia", "zimbabwe",
    # Asie (16)
    "cambodia", "china", "hong kong", "india", "indonesia", "japan", "laos",
    "macau", "malaysia", "myanmar", "philippines", "singapore", "south korea",
    "taiwan", "thailand", "vietnam",
    # Caraïbes (7)
    "aruba", "bonaire", "curacao", "guadeloupe", "haiti", "martinique", "saint martin",
    # Europe (24)
    "albania", "austria", "belgium", "bosnia and herzegovina", "bulgaria", "croatia",
    "czech republic", "france", "germany", "hungary", "kosovo", "macedonia",
    "montenegro", "netherlands", "poland", "portugal", "romania", "russia",
    "serbia", "slovakia", "slovenia", "spain", "switzerland", "ukraine",
    "united kingdom",
    # Territoires français d'outre-mer (5 supplémentaires)
    "french guiana", "french polynesia", "mayotte", "new caledonia", "reunion",
    # Moyen-Orient (5)
    "bahrain", "kuwait", "oman", "qatar", "united arab emirates",
]

# Réseau AHK : pays -> URL de la chambre (source : ahk.de/en/locations, régions
# Europe, Asia-Pacific, Middle East/North Africa, Sub-Saharan Africa)
AHK_NETWORK = {
    "algeria": "https://www.ahk.de/en/locations/middle-east-north-africa/algeria",
    "angola": "https://www.ahk.de/en/locations/sub-saharan-africa/angola",
    "egypt": "https://www.ahk.de/en/locations/middle-east-north-africa/egypt",
    "ghana": "https://www.ahk.de/en/locations/sub-saharan-africa/ghana",
    "ivory coast": "https://www.ahk.de/en/locations/sub-saharan-africa/cote-d-ivoire",
    "kenya": "https://www.ahk.de/en/locations/sub-saharan-africa/kenya",
    "morocco": "https://www.ahk.de/en/locations/middle-east-north-africa/morocco",
    "mozambique": "https://www.ahk.de/en/locations/sub-saharan-africa/mozambique",
    "nigeria": "https://www.ahk.de/en/locations/sub-saharan-africa/nigeria",
    "south africa": "https://www.ahk.de/en/locations/sub-saharan-africa/south-africa",
    "tanzania": "https://www.ahk.de/en/locations/sub-saharan-africa/tanzania",
    "tunisia": "https://www.ahk.de/en/locations/middle-east-north-africa/tunisia",
    "zambia": "https://www.ahk.de/en/locations/sub-saharan-africa/zambia",
    "china": "https://www.ahk.de/en/locations/asia-pacific/china",
    "hong kong": "https://www.ahk.de/en/locations/asia-pacific/hongkong",
    "india": "https://www.ahk.de/en/locations/asia-pacific/india",
    "indonesia": "https://www.ahk.de/en/locations/asia-pacific/indonesia",
    "japan": "https://www.ahk.de/en/locations/asia-pacific/japan",
    "malaysia": "https://www.ahk.de/en/locations/asia-pacific/malaysia",
    "philippines": "https://www.ahk.de/en/locations/asia-pacific/philippines",
    "singapore": "https://www.ahk.de/en/locations/asia-pacific/singapore",
    "south korea": "https://www.ahk.de/en/locations/asia-pacific/korea",
    "taiwan": "https://www.ahk.de/en/locations/asia-pacific/taiwan",
    "thailand": "https://www.ahk.de/en/locations/asia-pacific/thailand",
    "vietnam": "https://www.ahk.de/en/locations/asia-pacific/vietnam-hanoi",
    # Cambodge et Laos : pas de bureau AHK dédié par pays, mais couverts par la
    # délégation régionale "AHK Myanmar" (Delegation of German Industry and
    # Commerce), mandatée depuis 2018/2023 pour le Cambodge, le Laos et le Myanmar.
    "cambodia": "https://myanmar.ahk.de/",
    "laos": "https://myanmar.ahk.de/",
    "austria": "https://www.ahk.de/en/locations/europe/austria",
    "belgium": "https://www.ahk.de/en/locations/europe/belgium",
    "bosnia and herzegovina": "https://www.ahk.de/en/locations/europe/bosnia-herzegovina",
    "bulgaria": "https://www.ahk.de/en/locations/europe/bulgaria",
    "croatia": "https://www.ahk.de/en/locations/europe/croatia",
    "czech republic": "https://www.ahk.de/en/locations/europe/czech-republic",
    "france": "https://www.ahk.de/en/locations/europe/france",
    "hungary": "https://www.ahk.de/en/locations/europe/hungary",
    "macedonia": "https://www.ahk.de/en/locations/europe/north-macedonia",
    "netherlands": "https://www.ahk.de/en/locations/europe/netherlands",
    "poland": "https://www.ahk.de/en/locations/europe/poland",
    "portugal": "https://www.ahk.de/en/locations/europe/portugal",
    "romania": "https://www.ahk.de/en/locations/europe/romania",
    "russia": "https://www.ahk.de/en/locations/europe/russia",
    "serbia": "https://www.ahk.de/en/locations/europe/serbia",
    "slovakia": "https://www.ahk.de/en/locations/europe/slovakia",
    "slovenia": "https://www.ahk.de/en/locations/europe/slovenia",
    "spain": "https://www.ahk.de/en/locations/europe/spain",
    "switzerland": "https://www.ahk.de/en/locations/europe/switzerland",
    "ukraine": "https://www.ahk.de/en/locations/europe/ukraine",
    "united kingdom": "https://www.ahk.de/en/locations/europe/great-britain",
    "oman": "https://www.ahk.de/en/locations/middle-east-north-africa/oman",
    "qatar": "https://www.ahk.de/en/locations/middle-east-north-africa/qatar",
    "united arab emirates": "https://www.ahk.de/en/locations/middle-east-north-africa/united-arab-emirates",
    # Note : l'Allemagne elle-même n'a pas d'AHK (les AHK sont "à l'étranger" -
    # pour l'Allemagne, utilisez plutôt le réseau IHK, déjà distinct).
}

# Réseau AmCham : pays -> {name, url, email, phone} (source : uschamber.com,
# page "AmChams and Partners" — champs manquants = non publiés par l'US Chamber)
AMCHAM_NETWORK = {
    "angola": {"name": "American Chamber of Commerce in Angola", "url": "http://amchamangola.org", "email": "amchamangola@amchamangola.org", "phone": "+244 227 280 516"},
    "botswana": {"name": "American Business Council in Botswana", "url": "http://www.abc.org.bw/"},
    "cameroon": {"name": "American Chamber of Commerce in Cameroon", "url": "http://amchamcam.org/ac/", "phone": "+237 242 08 05 43"},
    "egypt": {"name": "American Chamber of Commerce in Egypt", "url": "https://www.amcham.org.eg/", "email": "info@amcham.org.eg", "phone": "(+20-2) 3333-6900"},
    "ghana": {"name": "American Chamber of Commerce in Ghana", "url": "http://www.amchamghana.org", "phone": "+233 030 2247562"},
    "ivory coast": {"name": "American Chamber of Commerce in Cote d'Ivoire", "url": "https://amcham-ci.org/", "email": "info@amcham-ci.org", "phone": "+225 25 22 015 638"},
    "kenya": {"name": "American Chamber of Commerce in Kenya", "url": "http://amcham.co.ke/", "email": "info@amcham.co.ke", "phone": "(+254) 709 207 000"},
    "madagascar": {"name": "American Chamber of Commerce in Madagascar", "url": "http://www.amcham-madagascar.org/", "email": "es@amcham-madagascar.org", "phone": "+261 20 26 410 34"},
    "morocco": {"name": "American Chamber of Commerce in Morocco", "url": "http://www.amcham.ma/"},
    "mozambique": {"name": "AmCham Mozambique", "email": "levin.born@regentssquaregroup.com"},
    "nigeria": {"name": "American Business Council of Nigeria", "url": "http://www.abcnig.com/"},
    "sierra leone": {"name": "American Chamber of Commerce in Sierra Leone", "url": "http://usslcc.org.sl/"},
    "south africa": {"name": "American Chamber of Commerce in South Africa", "url": "http://www.amcham.co.za", "email": "amcham@amcham.co.za", "phone": "+27 11 788 0265"},
    "tanzania": {"name": "American Chamber of Commerce in Tanzania", "url": "http://amcham-tz.com/", "email": "info@amcham-tz.com", "phone": "+255 786 080 060"},
    "tunisia": {"name": "American Chamber of Commerce in Tunisia", "url": "http://www.amchamtunisia.org.tn/", "phone": "+216 71 883 226"},
    "uganda": {"name": "American Chamber of Commerce of Uganda", "url": "http://www.amchamuganda.co.ug/", "phone": "+256 (0)78 254 3825"},
    "zambia": {"name": "American Chamber of Commerce in Zambia", "url": "http://www.amchamzambia.com", "email": "info@amchamzambia.com", "phone": "+260 975 028 026"},
    "cambodia": {"name": "The American Chamber of Commerce in Cambodia", "url": "https://amchamcambodia.net/", "email": "admin@amchamcambodia.net", "phone": "+855 15 255 191"},
    "china": {"name": "American Chamber of Commerce in China (Beijing)", "url": "http://www.amchamchina.org", "email": "amcham@amchamchina.org", "phone": "+8610 8519 0800"},
    "hong kong": {"name": "American Chamber of Commerce in Hong Kong", "url": "http://www.amcham.org.hk", "email": "amcham@amcham.org.hk"},
    "india": {"name": "American Chamber of Commerce in India", "url": "http://www.amchamindia.com", "email": "amcham@amchamindia.com"},
    "indonesia": {"name": "American Chamber of Commerce in Indonesia", "url": "http://www.amcham.or.id/", "email": "info@amcham.or.id", "phone": "+(62-21) 506 45071"},
    "japan": {"name": "American Chamber of Commerce in Japan", "url": "http://www.accj.or.jp", "email": "info@accj.or.jp", "phone": "+81 03-3433-5381"},
    "macau": {"name": "American Chamber of Commerce Macau", "url": "http://www.amcham.org.mo/", "email": "info@amcham.org.mo", "phone": "+853 2857.5059"},
    "malaysia": {"name": "American Malaysian Chamber of Commerce", "url": "http://www.amcham.com.my", "email": "info@amcham.com.my", "phone": "+603 2727 0070"},
    "myanmar": {"name": "The American Chamber of Commerce in Myanmar", "url": "https://www.amchammyanmar.com/", "phone": "+95 1 9253313"},
    "philippines": {"name": "American Chamber of Commerce in the Philippines", "url": "http://www.amchamphilippines.com", "email": "amcham@amchamphilippines.com"},
    "singapore": {"name": "American Chamber of Commerce in Singapore", "url": "http://www.amcham.org.sg", "phone": "+65 6732 5917"},
    "south korea": {"name": "American Chamber of Commerce in Korea", "url": "https://www.amchamkorea.org/", "email": "amchamrsvp@amchamkorea.org"},
    "taiwan": {"name": "American Chamber of Commerce in Taiwan", "url": "http://www.amcham.com.tw"},
    "thailand": {"name": "American Chamber of Commerce in Thailand", "url": "http://www.amchamthailand.com", "email": "services@amchamthailand.com"},
    "vietnam": {"name": "American Chamber of Commerce in Ho Chi Minh City", "url": "https://www.amchamvietnam.com/", "email": "contact@amchamvietnam.com", "phone": "+84 28 3824 3562"},
    "haiti": {"name": "The American Chamber of Commerce in Haiti", "url": "https://www.aaccla.org/amchams/haiti/", "email": "directionexecutive@amchamhaiti.com", "phone": "+509 2940-3024"},
    "albania": {"name": "American Chamber of Commerce in Albania", "url": "http://www.amcham.com.al/", "email": "floreta@amcham.com.al", "phone": "+355 (0)4 2259779"},
    "austria": {"name": "American Chamber of Commerce in Austria", "url": "http://www.amcham.at/", "email": "office@amcham.or.at", "phone": "+43 1 319 57 51"},
    "belgium": {"name": "American Chamber of Commerce in Belgium", "url": "http://www.amcham.be/", "email": "mclaes@amcham.be", "phone": "+32 (0)2 513 67 70"},
    "bosnia and herzegovina": {"name": "American Chamber of Commerce Bosnia & Herzegovina", "url": "http://www.amcham.ba/", "email": "amcham@amcham.ba", "phone": "+387 33 295 501"},
    "bulgaria": {"name": "American Chamber of Commerce in Bulgaria", "url": "http://www.amcham.bg", "email": "amcham@amcham.bg"},
    "croatia": {"name": "American Chamber of Commerce in Croatia", "url": "http://www.amcham.hr", "email": "info@amcham.hr"},
    "czech republic": {"name": "American Chamber of Commerce in the Czech Republic", "url": "http://www.amcham.cz", "email": "amcham@amcham.cz"},
    "france": {"name": "American Chamber of Commerce in France", "url": "http://www.amchamfrance.org"},
    "germany": {"name": "American Chamber of Commerce in Germany", "url": "http://www.amcham.de"},
    "hungary": {"name": "American Chamber of Commerce in Hungary", "url": "http://www.amcham.hu", "email": "info@amcham.hu"},
    "kosovo": {"name": "American Chamber of Commerce in Kosovo", "url": "http://www.amchamksv.org"},
    "macedonia": {"name": "American Chamber of Commerce in North Macedonia", "url": "http://amcham.com.mk/", "email": "info@amcham.com.mk"},
    "montenegro": {"name": "The American Chamber of Commerce in Montenegro", "url": "http://www.amcham.me/", "email": "info@amcham.me"},
    "netherlands": {"name": "American Chamber of Commerce in Netherlands", "url": "http://www.amcham.nl", "email": "office@amcham.nl"},
    "poland": {"name": "American Chamber of Commerce in Poland", "url": "http://www.amcham.com.pl", "email": "office@amcham.pl"},
    "portugal": {"name": "American Chamber of Commerce in Portugal", "url": "https://amchamportugal.pt/", "email": "amchamportugal@mail.telepac.pt"},
    "romania": {"name": "American Chamber of Commerce in Romania", "url": "http://www.amcham.ro"},
    "russia": {"name": "American Chamber of Commerce in Russia", "url": "http://www.amcham.ru"},
    "serbia": {"name": "The American Chamber of Commerce in Serbia", "email": "info@amcham.rs"},
    "slovakia": {"name": "The American Chamber of Commerce in Slovakia", "url": "http://www.amcham.sk/home", "email": "office@amcham.sk"},
    "slovenia": {"name": "American Chamber of Commerce in Slovenia", "url": "http://www.amcham.si", "email": "office@amcham.si"},
    "spain": {"name": "American Chamber of Commerce in Spain", "url": "http://www.amchamspain.com/", "email": "amcham@amchamspain.com"},
    "switzerland": {"name": "Swiss-American Chamber of Commerce", "url": "http://www.amcham.ch/", "email": "info@amcham.ch"},
    "ukraine": {"name": "American Chamber of Commerce in Ukraine, Inc.", "url": "http://www.chamber.ua", "email": "chamber@chamber.ua"},
    "united kingdom": {"name": "British American Business, Inc.", "url": "http://www.babinc.org"},
    "bahrain": {"name": "American Chamber of Commerce in Bahrain", "url": "http://www.amchambahrain.org", "email": "info@amchambahrain.org"},
    "kuwait": {"name": "American Business Council of Kuwait", "url": "http://abckw.org/"},
    "oman": {"name": "AmCham Oman", "email": "info@oabc.org", "phone": "+968 94189500"},
    "qatar": {"name": "American Chamber of Commerce in Qatar", "phone": "+974 4020 6038"},
    "united arab emirates": {"name": "AmCham Dubai", "url": "https://amchamdubai.org", "email": "info@amchamdubai.org", "phone": "+971 4 429 5812"},
    "drc": {"name": "American Chamber of Commerce in DRC (AmCham DRC)", "url": "https://www.amchamdrc.com/"},
    "senegal": {"name": "American Chamber of Commerce in Senegal (AmCham Senegal)", "url": "https://amchamsenegal.org/"},
}


def print_chambers_for_ags_countries(country: str = None):
    """
    Affiche la chambre allemande (AHK) et américaine (AmCham) pour un pays où AGS
    est présent, ou pour tous les pays si aucun n'est précisé.
    """
    countries = [country.lower()] if country else AGS_PRESENCE_COUNTRIES
    unknown = [c for c in countries if c not in AGS_PRESENCE_COUNTRIES]
    if unknown:
        print(f"Pays non reconnu dans la liste de présence AGS : {unknown}")
        return

    found_any = False
    for c in countries:
        ahk = AHK_NETWORK.get(c)
        amcham = AMCHAM_NETWORK.get(c)
        if not ahk and not amcham:
            continue
        found_any = True
        print(f"\n{c.title()} :")
        if ahk:
            print(f"  AHK (chambre allemande)     : {ahk}")
        if amcham:
            details = amcham.get("url") or ""
            extra = " | ".join(filter(None, [amcham.get("email"), amcham.get("phone")]))
            print(f"  AmCham (chambre américaine) : {amcham['name']}"
                  f"{' - ' + details if details else ''}{' | ' + extra if extra else ''}")

    if not found_any:
        print(f"Aucune AHK ni AmCham identifiée pour : {countries}")


# ---------------------------------------------------------------------------
# 8. Chambres françaises (CCI France International) et britanniques
#    (British Chambers of Commerce International Network) — pays où AGS est présent
#
# CCI France International : liste officielle et complète par continent sur
# ccifrance-international.org/notre-reseau/les-cci-fi.html (121 chambres, 98 pays).
# Couverture très bonne pour l'Afrique et quasi totale pour l'Asie côté AGS.
# Balkans : Croatie couverte par une chambre franco-croate dédiée et active
# depuis 20+ ans (chambrefrancocroate.com) ; Bosnie et Monténégro n'ont pas de
# CCI FI propre mais la CCI France Serbie (CCIFS) se présente explicitement
# comme "l'accélérateur business pour les Balkans occidentaux : Serbie,
# Bosnie-Herzégovine, Monténégro, Macédoine du Nord" — d'où le même lien pour
# ces 3 pays. Traitez donc Bosnie/Monténégro comme un point d'entrée régional,
# pas une chambre 100% dédiée au pays.
#
# British Chambers of Commerce : contrairement à AHK/AmCham/CCI FI, il n'y a PAS
# d'annuaire centralisé unique — ce sont des chambres bilatérales indépendantes.
# Sources : 5 pages de britishchambers.org.uk/locations-category/international/
# + recherches ciblées pays par pays.
#
# Asie AGS : COMPLÈTE (15/16) — seul Taïwan n'a pas de chambre bilatérale
# britannique dédiée (AmCham/AHK y sont présents, pas de British Chamber).
#
# Balkans AGS : Albanie, Bosnie, Croatie, Macédoine du Nord couvertes. Seul le
# Monténégro reste sans chambre britannique ET sans AHK trouvés (seul AmCham
# y est présent — voir tableau croisé --chambers).
#
# Confirmé ABSENT (pas juste non cherché), après vérification pays par pays :
# Taïwan, Oman, Koweït — aucune chambre bilatérale britannique dédiée n'existe,
# contrairement à AHK/AmCham qui y sont présents.
#
# Afrique : Angola, Côte d'Ivoire, Madagascar, Mozambique ont eu des chambres
# britanniques lancées entre 2014-2016 (annonces gov.uk) mais aucun site actif
# retrouvé aujourd'hui — probablement inactives, non ajoutées pour éviter des
# liens morts. Ces 4 pays ont déjà AHK et/ou AmCham (voir tableau croisé).
# ---------------------------------------------------------------------------

CCI_FRANCE_NETWORK = {
    # Afrique
    "south africa": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-afrique-sud.html",
    "algeria": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-algerie.html",
    "angola": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-simplanter-en-angola.html",
    "ivory coast": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-cote-ivoire.html",
    "ethiopia": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-simplanter-en-ethiopie.html",
    "ghana": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-ghana.html",
    "kenya": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-kenya.html",
    "madagascar": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-madagascar.html",
    "morocco": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-maroc.html",
    "mauritius": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-maurice.html",
    "mozambique": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-mozambique.html",
    "nigeria": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-nigeria.html",
    "uganda": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-simplanter-en-ouganda.html",
    "drc": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-congo.html",
    "tanzania": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-simplanter-en-tanzanie.html",
    "tunisia": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/afrique/exporter-s-implanter-tunisie.html",
    # Europe
    "albania": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-albanie.html",
    "germany": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-allemagne.html",
    "austria": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-autriche.html",
    "belgium": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-belgique.html",
    "bulgaria": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-bulgarie.html",
    "spain": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-espagne.html",
    "united kingdom": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-grande-bretagne.html",
    "hungary": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-hongrie.html",
    "netherlands": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-pays-bas.html",
    "poland": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-pologne.html",
    "portugal": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-portugal.html",
    "czech republic": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-republique-tcheque.html",
    "romania": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-roumanie.html",
    "russia": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-russie.html",
    "serbia": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-serbie.html",
    "slovakia": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-slovaquie.html",
    "switzerland": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-simplanter-en-suisse.html",
    "ukraine": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-ukraine.html",
    # Croatie : pas de CCI FI dédiée, mais une chambre franco-croate indépendante
    # de longue date, toujours active (fondée avec le soutien de 50 entreprises françaises).
    "croatia": "https://www.chambrefrancocroate.com/index.php/en/",
    # Bosnie et Monténégro : pas de CCI FI propre, mais la CCI France Serbie (CCIFS)
    # se présente explicitement comme "l'accélérateur business pour les Balkans
    # occidentaux : Serbie, Bosnie-Herzégovine, Monténégro, Macédoine du Nord".
    "bosnia and herzegovina": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-serbie.html",
    "montenegro": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/europe/exporter-s-implanter-serbie.html",
    # Asie-Océanie (couverture quasi complète des pays AGS)
    "cambodia": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-cambodge.html",
    "china": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-chine.html",
    "south korea": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-coreesud.html",
    "hong kong": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-hongkong-chine.html",
    "india": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-inde.html",
    "indonesia": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-indonesie.html",
    "japan": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-japon.html",
    "laos": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-simplanter-au-laos.html",
    "macau": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-macao.html",
    "malaysia": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-malaisie.html",
    "myanmar": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-myanmar.html",
    "philippines": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-philippines.html",
    "singapore": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-singapour.html",
    "taiwan": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-taiwan.html",
    "thailand": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-thailande.html",
    "vietnam": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/asie-oceanie/exporter-s-implanter-vietnam.html",
    # Moyen-Orient
    "bahrain": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/moyen-orient/exporter-s-implanter-bahrein.html",
    "egypt": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/moyen-orient/exporter-s-implanter-egypte.html",
    "united arab emirates": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/moyen-orient/exporter-simplanter-aux-emirats-arabes-unis.html",
    "kuwait": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/moyen-orient/exporter-s-implanter-koweit.html",
    "qatar": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/moyen-orient/exporter-s-implanter-qatar.html",
    # Caraïbes
    "haiti": "https://www.ccifrance-international.org/notre-reseau/les-cci-fi/amerique-du-sud/exporter-s-implanter-haiti.html",
}

# Réseau British Chambers of Commerce International — {name, url} par pays.
# Couverture PARTIELLE (voir note ci-dessus) : basée sur 5 pages parcourues sur
# britishchambers.org.uk/locations-category/international/, pas un annuaire complet.
BRITISH_CHAMBERS_NETWORK = {
    "germany": {"name": "British Chamber of Commerce in Germany", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-in-germany/"},
    "ghana": {"name": "UK-Ghana Chamber of Commerce", "url": "https://www.britishchambers.org.uk/locations/uk-ghana-chamber-of-commerce/"},
    "qatar": {"name": "British Chamber of Commerce Qatar", "url": "https://www.britishchambers.org.uk/locations/qatar-british-business-forum/"},
    "portugal": {"name": "British-Portuguese Chamber of Commerce", "url": "https://www.britishchambers.org.uk/stores/british-portuguese-chamber-of-commerce/"},
    "nigeria": {"name": "Nigerian-British Chamber of Commerce", "url": "https://www.britishchambers.org.uk/stores/nigerian-british-chamber-of-commerce/"},
    "netherlands": {"name": "Netherlands British Chamber of Commerce", "url": "https://www.britishchambers.org.uk/stores/netherlands-british-chamber-of-commerce/"},
    "ukraine": {"name": "British Ukrainian Chamber of Commerce", "url": "https://www.britishchambers.org.uk/locations/british-ukrainian-chamber-of-commerce/"},
    "switzerland": {"name": "British Swiss Chamber of Commerce", "url": "https://www.britishchambers.org.uk/locations/british-swiss-chamber-of-commerce/"},
    "slovenia": {"name": "British-Slovenian Chamber of Commerce", "url": "https://www.britishchambers.org.uk/locations/british-slovenian-chamber-of-commerce/"},
    "serbia": {"name": "British Serbian Chamber of Commerce", "url": "https://www.britishchambers.org.uk/locations/british-serbian-chamber-of-commerce/"},
    "romania": {"name": "British Romanian Chamber of Commerce", "url": "https://www.britishchambers.org.uk/locations/british-romanian-chamber-of-commerce/"},
    "poland": {"name": "British Polish Chamber of Commerce", "url": "https://www.britishchambers.org.uk/locations/british-polish-chamber-of-commerce/"},
    "malaysia": {"name": "British Malaysian Chamber of Commerce", "url": "https://www.britishchambers.org.uk/locations/british-malaysian-chamber-of-commerce/"},
    "china": {"name": "British Chambers of Commerce in China", "url": "https://www.britishchambers.org.uk/locations/british-chambers-of-commerce-in-china/"},
    "thailand": {"name": "British Chamber of Commerce Thailand", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-thailand/"},
    "singapore": {"name": "British Chamber of Commerce Singapore", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-singapore/"},
    "myanmar": {"name": "British Chamber of Commerce Myanmar", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-myanmar/"},
    "kenya": {"name": "British Chamber of Commerce Kenya", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-kenya/"},
    "indonesia": {"name": "British Chamber of Commerce Indonesia", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-indonesia/"},
    "vietnam": {"name": "British Chamber of Commerce in Vietnam", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-in-vietnam/"},
    "spain": {"name": "British Chamber of Commerce in Spain", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-in-spain/"},
    "slovakia": {"name": "British Chamber of Commerce in Slovakia", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-in-slovakia/"},
    "south korea": {"name": "British Chamber of Commerce in Korea", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-in-korea/"},
    "hong kong": {"name": "British Chamber of Commerce in Hong Kong", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-in-hong-kong/"},
    "czech republic": {"name": "British Chamber of Commerce in Czech Republic", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-in-czech-republic/"},
    "belgium": {"name": "British Chamber of Commerce in Belgium", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-in-belgium/"},
    "south africa": {"name": "British Chamber of Business in Southern Africa", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-business-in-southern-africa/"},
    "united arab emirates": {"name": "British Chamber of Commerce Dubai", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-dubai/"},
    "bulgaria": {"name": "British Bulgarian Chamber of Commerce", "url": "https://www.britishchambers.org.uk/locations/british-bulgarian-chamber-of-commerce/"},
    "france": {"name": "Franco British Chamber of Commerce", "url": "https://www.britishchambers.org.uk/locations/franco-british-chamber-of-commerce/"},
    "japan": {"name": "British Chamber of Commerce in Japan (BCCJ)", "url": "https://www.bccjapan.com/"},
    "india": {"name": "UK India Business Council (UKIBC)", "url": "https://www.ukibc.com/"},
    "egypt": {"name": "The Egyptian-British Chamber of Commerce (EBCC)", "url": "https://theebcc.com/"},
    "kosovo": {"name": "British Chamber of Commerce in Kosovo (BCCK)", "url": "https://bcck.co.uk/"},
    "morocco": {"name": "British Chamber of Commerce for Morocco", "url": "https://www.britishchambers.org.uk/locations/british-chamber-of-commerce-for-morocco/"},
    "tanzania": {"name": "British Business Group Tanzania (BBG)", "url": "https://bbg.co.tz/"},
    "philippines": {"name": "British Chamber of Commerce Philippines (BCCP)", "url": "https://britcham.org.ph/"},
    "tunisia": {"name": "Tunisian British Chamber of Commerce (TBCC)", "url": "https://www.linkedin.com/company/tbcc-tunisian-british-chamber-of-commerce/"},
    "cambodia": {"name": "British Chamber of Commerce in Cambodia (BritCham)", "url": "https://www.britchamcambodia.org/"},
    "laos": {"name": "British Chamber of Commerce in Laos (BCCL)", "url": "https://britchamlaos.org/"},
    "macau": {"name": "British Chamber of Commerce in Macao (Britcham Macao)", "url": "https://www.britchammacao.org/"},
    "bahrain": {"name": "British Bahraini Business Forum (British Chamber of Commerce Bahrain)", "url": "https://bbbforum.org/"},
    "cameroon": {"name": "UK-Cameroon Chamber of Commerce (UKCCC)", "url": "https://uk-ccc.org/"},
    "algeria": {"name": "Algeria British Business Council (ABBC)", "url": "https://abbc.org.uk/"},
    "uganda": {"name": "British Chamber of Commerce Uganda (BCCU)", "url": "https://bccuganda.org/"},
    "zambia": {"name": "British Chamber of Commerce in Zambia (BCCZ)", "url": "https://www.britishchamberzambia.org/"},
    "ethiopia": {"name": "British Ethiopian Chamber of Commerce (BECC)", "url": "https://britishethiopianchamber.com/"},
    "bosnia and herzegovina": {"name": "British Bosnian and Herzegovinian Chamber of Commerce (BBHCC)", "url": "https://bbhcc.uk/"},
    "croatia": {"name": "British Croatian Chamber of Commerce", "url": "https://britishcroatiancc.co.uk/"},
    "macedonia": {"name": "British Business Association in North Macedonia (BBA)", "url": "https://bba.mk/"},
    "albania": {"name": "British Albanian Chamber of Commerce and Industry", "url": "https://abcci.com/"},
}


def print_full_chamber_report(country: str = None):
    """
    Vue complète : AHK, AmCham, CCI France International et British Chambers,
    pour un pays AGS donné ou pour tous les pays où au moins une chambre existe.
    """
    countries = [country.lower()] if country else AGS_PRESENCE_COUNTRIES
    unknown = [c for c in countries if c not in AGS_PRESENCE_COUNTRIES]
    if unknown:
        print(f"Pays non reconnu dans la liste de présence AGS : {unknown}")
        return

    found_any = False
    for c in countries:
        networks = {
            "AHK (Allemagne)": AHK_NETWORK.get(c),
            "AmCham (États-Unis)": AMCHAM_NETWORK.get(c),
            "CCI France International": CCI_FRANCE_NETWORK.get(c),
            "British Chambers (Royaume-Uni)": BRITISH_CHAMBERS_NETWORK.get(c),
        }
        if not any(networks.values()):
            continue
        found_any = True
        print(f"\n{c.title()} :")
        for label, value in networks.items():
            if not value:
                continue
            if isinstance(value, dict):
                url = value.get("url") or ""
                extra = " | ".join(filter(None, [value.get("email"), value.get("phone")]))
                print(f"  {label:32s} : {value.get('name', '')}"
                      f"{' - ' + url if url else ''}{' | ' + extra if extra else ''}")
            else:
                print(f"  {label:32s} : {value}")

    if not found_any:
        print(f"Aucune chambre identifiée (AHK/AmCham/CCI FI/British Chambers) pour : {countries}")



# ---------------------------------------------------------------------------
# 9. V5 — ENTITY RESOLUTION / PROVENANCE / CORPORATE GRAPH / EVENTS
# ---------------------------------------------------------------------------
# The engine deliberately separates company-level facts from personal contacts.
# Every enrichment can carry source + confidence metadata.

GLOBAL_REGISTRY_CATALOG = {
    # Europe
    "denmark_cvr": {"country":"DK","name":"Denmark CVR","type":"registry","url":"https://datacvr.virk.dk/","status":"ORANGE"},
    "estonia_ebr": {"country":"EE","name":"Estonia e-Business Register","type":"registry","url":"https://ariregister.rik.ee/eng","status":"ORANGE"},
    "ireland_cro": {"country":"IE","name":"Ireland CRO","type":"registry","url":"https://opendata.cro.ie/","status":"GREEN"},
    "switzerland_zefix": {"country":"CH","name":"Switzerland Zefix","type":"registry","url":"https://www.zefix.ch/","status":"ORANGE"},
    "netherlands_kvk": {"country":"NL","name":"Netherlands KVK","type":"registry","url":"https://www.kvk.nl/","status":"ORANGE"},
    "spain_borme": {"country":"ES","name":"Spain BORME / Registro Mercantil","type":"registry","url":"https://www.boe.es/diario_borme/","status":"ORANGE"},
    "italy_registro_imprese": {"country":"IT","name":"Italy Registro Imprese","type":"registry","url":"https://www.registroimprese.it/","status":"ORANGE"},
    "portugal_company_registry": {"country":"PT","name":"Portugal Empresa Online / Registo Comercial","type":"registry","url":"https://justica.gov.pt/Servicos/Empresa-Online","status":"ORANGE"},
    "poland_krs": {"country":"PL","name":"Poland KRS","type":"registry","url":"https://ekrs.ms.gov.pl/","status":"ORANGE"},
    "czech_ares": {"country":"CZ","name":"Czech ARES","type":"registry","url":"https://ares.gov.cz/","status":"ORANGE"},
    "finland_prh": {"country":"FI","name":"Finland PRH","type":"registry","url":"https://www.prh.fi/","status":"ORANGE"},
    "sweden_bolagsverket": {"country":"SE","name":"Sweden Bolagsverket","type":"registry","url":"https://bolagsverket.se/","status":"ORANGE"},
    "luxembourg_lbr": {"country":"LU","name":"Luxembourg RCS/LBR","type":"registry","url":"https://www.lbr.lu/","status":"ORANGE"},
    "austria_firmenbuch": {"country":"AT","name":"Austria Firmenbuch","type":"registry","url":"https://www.justiz.gv.at/","status":"ORANGE"},
    "romania_onrc": {"country":"RO","name":"Romania ONRC","type":"registry","url":"https://www.onrc.ro/","status":"ORANGE"},
    # Africa
    "south_africa_cipc": {"country":"ZA","name":"South Africa CIPC","type":"registry","url":"https://www.cipc.co.za/","status":"ORANGE"},
    "kenya_brs": {"country":"KE","name":"Kenya Business Registration Service","type":"registry","url":"https://brs.go.ke/","status":"ORANGE"},
    "nigeria_cac": {"country":"NG","name":"Nigeria Corporate Affairs Commission","type":"registry","url":"https://www.cac.gov.ng/","status":"ORANGE"},
    "ghana_orc": {"country":"GH","name":"Ghana Office of the Registrar of Companies","type":"registry","url":"https://orc.gov.gh/","status":"ORANGE"},
    "rwanda_rdb": {"country":"RW","name":"Rwanda Development Board","type":"registry","url":"https://org.rdb.rw/","status":"ORANGE"},
    "mauritius_cbrd": {"country":"MU","name":"Mauritius CBRD","type":"registry","url":"https://companies.govmu.org/","status":"ORANGE"},
    "botswana_cipa": {"country":"BW","name":"Botswana CIPA","type":"registry","url":"https://www.cipa.co.bw/","status":"ORANGE"},
    "namibia_bipa": {"country":"NA","name":"Namibia BIPA","type":"registry","url":"https://www.bipa.na/","status":"ORANGE"},
    "tanzania_brela": {"country":"TZ","name":"Tanzania BRELA","type":"registry","url":"https://www.brela.go.tz/","status":"ORANGE"},
    "uganda_ursb": {"country":"UG","name":"Uganda URSB","type":"registry","url":"https://ursb.go.ug/","status":"ORANGE"},
    "morocco_ompic": {"country":"MA","name":"Morocco OMPIC","type":"registry","url":"https://www.ompic.ma/","status":"ORANGE"},
    "tunisia_rne": {"country":"TN","name":"Tunisia RNE","type":"registry","url":"https://www.registre-entreprises.tn/","status":"ORANGE"},
    # Asia / Middle East
    "japan_corporate_number": {"country":"JP","name":"Japan Corporate Number","type":"registry","url":"https://www.houjin-bangou.nta.go.jp/","status":"ORANGE"},
    "singapore_acra": {"country":"SG","name":"Singapore ACRA","type":"registry","url":"https://www.acra.gov.sg/","status":"ORANGE"},
    "hong_kong_cr": {"country":"HK","name":"Hong Kong Companies Registry","type":"registry","url":"https://www.cr.gov.hk/","status":"ORANGE"},
    "india_mca": {"country":"IN","name":"India MCA","type":"registry","url":"https://www.mca.gov.in/","status":"ORANGE"},
    "malaysia_ssm": {"country":"MY","name":"Malaysia SSM","type":"registry","url":"https://www.ssm.com.my/","status":"ORANGE"},
    "thailand_dbd": {"country":"TH","name":"Thailand DBD","type":"registry","url":"https://www.dbd.go.th/","status":"ORANGE"},
    "philippines_sec": {"country":"PH","name":"Philippines SEC","type":"registry","url":"https://www.sec.gov.ph/","status":"ORANGE"},
    "indonesia_ahu": {"country":"ID","name":"Indonesia AHU","type":"registry","url":"https://ahu.go.id/","status":"ORANGE"},
    "south_korea_registry": {"country":"KR","name":"South Korea Corporate Registry / Open Data","type":"registry","url":"https://www.data.go.kr/","status":"ORANGE"},
    "uae_business_registries": {"country":"AE","name":"UAE Economic Department Registries","type":"registry","url":"https://u.ae/en/information-and-services/business","status":"ORANGE"},
    "saudi_moc": {"country":"SA","name":"Saudi Ministry of Commerce","type":"registry","url":"https://mc.gov.sa/","status":"ORANGE"},
    "qatar_moci": {"country":"QA","name":"Qatar MOCI","type":"registry","url":"https://www.moci.gov.qa/","status":"ORANGE"},
    "bahrain_sijilat": {"country":"BH","name":"Bahrain Sijilat","type":"registry","url":"https://www.sijilat.bh/","status":"ORANGE"},
    "oman_mociip": {"country":"OM","name":"Oman MOCIIP","type":"registry","url":"https://tejarah.gov.om/","status":"ORANGE"},
    # Americas / Oceania
    "us_sec": {"country":"US","name":"US SEC EDGAR","type":"filings","url":"https://www.sec.gov/edgar","status":"GREEN"},
    "brazil_cnpj": {"country":"BR","name":"Brazil CNPJ Open Data","type":"registry","url":"https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/dados-abertos/cadastros","status":"ORANGE"},
    "canada_federal": {"country":"CA","name":"Canada Corporations Canada","type":"registry","url":"https://ised-isde.canada.ca/site/corporations-canada/","status":"ORANGE"},
    "mexico_rpc": {"country":"MX","name":"Mexico Registro Público de Comercio","type":"registry","url":"https://rpc.economia.gob.mx/","status":"ORANGE"},
    "colombia_rues": {"country":"CO","name":"Colombia RUES","type":"registry","url":"https://www.rues.org.co/","status":"ORANGE"},
    "chile_company_registry": {"country":"CL","name":"Chile Registro de Empresas","type":"registry","url":"https://www.registrodeempresasysociedades.cl/","status":"ORANGE"},
    "new_zealand_companies": {"country":"NZ","name":"New Zealand Companies Register","type":"registry","url":"https://companies-register.companiesoffice.govt.nz/","status":"ORANGE"},
    "australia_abr": {"country":"AU","name":"Australia ABR / ABN Lookup","type":"registry","url":"https://abr.business.gov.au/","status":"ORANGE"},
    # Cross-border intelligence
    "gleif": {"country":"GLOBAL","name":"GLEIF LEI Data","type":"entity_resolution","url":"https://www.gleif.org/en/lei-data/gleif-api","status":"GREEN"},
    "gleif_oc_mapping": {"country":"GLOBAL","name":"GLEIF OpenCorporates ID-to-LEI Mapping","type":"entity_resolution","url":"https://www.gleif.org/en/lei-data/lei-mapping/download-oc-to-lei-relationship-files","status":"GREEN"},
    "open_ownership": {"country":"GLOBAL","name":"Open Ownership Register","type":"ownership","url":"https://register.openownership.org/","status":"ORANGE"},
    "ted": {"country":"EU","name":"TED Procurement API","type":"procurement","url":"https://docs.ted.europa.eu/api/latest/","status":"GREEN"},
    "world_bank_procurement": {"country":"GLOBAL","name":"World Bank Procurement","type":"procurement","url":"https://projects.worldbank.org/","status":"GREEN"},
    "opensanctions": {"country":"GLOBAL","name":"OpenSanctions","type":"risk","url":"https://www.opensanctions.org/","status":"ORANGE"},
}


def _ensure_column(conn, table, column, definition):
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_v5_schema(conn):
    """Non-destructive schema migration for the V5 data engine."""
    for sql in [
        """CREATE TABLE IF NOT EXISTS company_identifiers (
            identifier_id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
            identifier_type TEXT NOT NULL, identifier_value TEXT NOT NULL,
            source_name TEXT, source_url TEXT, confidence REAL,
            retrieved_at TEXT, UNIQUE(identifier_type, identifier_value, company_id))""",
        """CREATE TABLE IF NOT EXISTS company_sources (
            evidence_id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
            field_name TEXT NOT NULL, field_value TEXT, source_name TEXT,
            source_url TEXT, confidence REAL, retrieved_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS company_events (
            event_id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
            event_type TEXT NOT NULL, event_date TEXT, description TEXT,
            source_name TEXT, source_url TEXT, raw_json TEXT, retrieved_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS financial_metrics (
            metric_id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
            metric_name TEXT NOT NULL, value TEXT, unit TEXT, period_end TEXT,
            fiscal_year INTEGER, source_name TEXT, source_url TEXT, retrieved_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS company_domains (
            domain_id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
            domain TEXT NOT NULL, domain_type TEXT, verified INTEGER DEFAULT 0,
            source_name TEXT, source_url TEXT, confidence REAL, retrieved_at TEXT,
            UNIQUE(company_id, domain))""",
        """CREATE TABLE IF NOT EXISTS entity_matches (
            match_id TEXT PRIMARY KEY, company_id_a TEXT NOT NULL,
            company_id_b TEXT NOT NULL, score REAL, match_level TEXT,
            reasons TEXT, source_name TEXT, created_at TEXT,
            UNIQUE(company_id_a, company_id_b))""",
        """CREATE TABLE IF NOT EXISTS corporate_groups (
            group_id TEXT PRIMARY KEY, root_company_id TEXT,
            root_lei TEXT, group_name TEXT, confidence REAL,
            source_name TEXT, retrieved_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS licenses (
            license_id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
            license_type TEXT, license_number TEXT, status TEXT,
            issuing_authority TEXT, issue_date TEXT, expiry_date TEXT,
            source_name TEXT, source_url TEXT, retrieved_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS risk_events (
            risk_id TEXT PRIMARY KEY, company_id TEXT NOT NULL,
            risk_type TEXT, severity TEXT, status TEXT, description TEXT,
            source_name TEXT, source_url TEXT, event_date TEXT, raw_json TEXT,
            retrieved_at TEXT)""",
    ]:
        conn.execute(sql)
    _ensure_column(conn, "companies", "company_number", "TEXT")
    _ensure_column(conn, "companies", "normalized_name", "TEXT")
    _ensure_column(conn, "companies", "confidence_score", "REAL")
    _ensure_column(conn, "companies", "last_verified_at", "TEXT")
    # CRITIQUE pour discover_gleif_bulk : sans cet index, le "SELECT ... WHERE
    # lei=?" fait un scan complet de la table à CHAQUE ligne du fichier GLEIF
    # (plusieurs millions de lignes) -- le run devient O(n²) et ne termine
    # jamais dans un temps raisonnable (constaté : timeout à 5h). Avec l'index,
    # c'est O(n log n).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_companies_lei ON companies(lei)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_companies_jurisdiction ON companies(jurisdiction)")
    conn.commit()


def init_db_v5():
    conn = init_db()
    init_v5_schema(conn)
    return conn


def normalize_company_name(name):
    import re, unicodedata
    if not name:
        return ""
    x = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii").lower()
    x = re.sub(r"[^a-z0-9 ]+", " ", x)
    stop = {"ltd","limited","llc","inc","incorporated","corp","corporation","co","company","plc","sa","sarl","gmbh","ag","nv","bv","spa","pte","pty"}
    return " ".join(w for w in x.split() if w not in stop)


def _similarity(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, normalize_company_name(a), normalize_company_name(b)).ratio()


def calculate_entity_match(a, b):
    """Return a deterministic 0-100 entity match score and reasons."""
    score, reasons = 0.0, []
    an, bn = a.get("legal_name") or "", b.get("legal_name") or ""
    name_score = _similarity(an, bn)
    score += min(name_score * 30, 30)
    if name_score >= .90: reasons.append("name>=90%")
    elif name_score >= .75: reasons.append("name>=75%")
    if a.get("company_number") and b.get("company_number") and str(a["company_number"]).strip() == str(b["company_number"]).strip():
        score += 45; reasons.append("registration-number-exact")
    if a.get("lei") and b.get("lei") and a["lei"].upper() == b["lei"].upper():
        score += 25; reasons.append("lei-exact")
    ad, bd = (a.get("primary_domain") or "").lower(), (b.get("primary_domain") or "").lower()
    if ad and bd and ad == bd:
        score += 20; reasons.append("domain-exact")
    aa, ba = normalize_company_name(a.get("hq_address") or ""), normalize_company_name(b.get("hq_address") or "")
    if aa and ba and aa == ba:
        score += 10; reasons.append("address-exact")
    score = min(100.0, score)
    level = "confirmed" if score >= 95 else "high" if score >= 85 else "probable" if score >= 70 else "review"
    return round(score, 2), level, reasons


def add_company_evidence(conn, company_id, field_name, field_value, source_name, source_url=None, confidence=50):
    conn.execute("INSERT INTO company_sources VALUES (?,?,?,?,?,?,?,?)", (
        str(uuid.uuid4()), company_id, field_name, str(field_value) if field_value is not None else None,
        source_name, source_url, float(confidence), datetime.now(timezone.utc).isoformat()))


def add_identifier(conn, company_id, identifier_type, identifier_value, source_name, source_url=None, confidence=90):
    if not identifier_value:
        return
    conn.execute("INSERT OR IGNORE INTO company_identifiers VALUES (?,?,?,?,?,?,?,?)", (
        str(uuid.uuid4()), company_id, identifier_type, str(identifier_value), source_name,
        source_url, float(confidence), datetime.now(timezone.utc).isoformat()))


def store_entity_match(conn, company_id_a, company_id_b, score, level, reasons, source_name="entity-resolution"):
    if company_id_a == company_id_b:
        return
    a, b = sorted([company_id_a, company_id_b])
    conn.execute("INSERT OR REPLACE INTO entity_matches VALUES (?,?,?,?,?,?,?,?)", (
        str(uuid.uuid4()), a, b, score, level, json.dumps(reasons), source_name,
        datetime.now(timezone.utc).isoformat()))


def resolve_company_candidates(conn, company):
    """Compare a candidate against the local master table and persist strong matches."""
    init_v5_schema(conn)
    rows = conn.execute("SELECT company_id,legal_name,lei,primary_domain,company_number,hq_city,hq_state,hq_country FROM companies WHERE company_id<>?", (company.get("company_id", ""),)).fetchall()
    matches = []
    for r in rows:
        other = dict(zip(["company_id","legal_name","lei","primary_domain","company_number","hq_city","hq_state","hq_country"], r))
        score, level, reasons = calculate_entity_match(company, other)
        if score >= 70:
            store_entity_match(conn, company.get("company_id"), other["company_id"], score, level, reasons)
            matches.append((other["company_id"], score, level, reasons))
    conn.commit()
    return sorted(matches, key=lambda x: x[1], reverse=True)


def get_gleif_relationship_records(lei):
    """Fetch parent/child relationship records from the public GLEIF API."""
    out=[]
    for rel in ("direct-parent","ultimate-parent","direct-child","ultimate-child"):
        try:
            r=requests.get(f"https://api.gleif.org/api/v1/lei-records/{quote(lei)}/relationships/{rel}", params={"page[size]":100}, timeout=20)
            if r.status_code == 404: continue
            r.raise_for_status()
            for item in r.json().get("data", []):
                attrs=item.get("attributes", {})
                out.append({"relationship_type":rel, "lei":attrs.get("lei") or item.get("id"), "attributes":attrs})
        except requests.RequestException:
            continue
    return out


def build_corporate_group(company_id, lei, conn=None):
    conn = conn or init_db_v5()
    records = get_gleif_relationship_records(lei) if lei else []
    root_lei = next((x["lei"] for x in records if x["relationship_type"] == "ultimate-parent"), None) or lei
    gid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"corporate-group:{root_lei}")) if root_lei else str(uuid.uuid4())
    conn.execute("INSERT OR REPLACE INTO corporate_groups VALUES (?,?,?,?,?,?,?)", (
        gid, company_id, root_lei, None, 95 if root_lei else 50, "GLEIF", datetime.now(timezone.utc).isoformat()))
    for rec in records:
        conn.execute("INSERT OR IGNORE INTO corporate_relationships VALUES (?,?,?,?,?,?,?)", (
            str(uuid.uuid4()), company_id, rec["lei"], rec["relationship_type"], "GLEIF",
            f"https://api.gleif.org/api/v1/lei-records/{lei}", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return records


def fetch_gleif_oc_lei_mapping(url=None, timeout=60):
    """Download the latest GLEIF OpenCorporates->LEI mapping ZIP to a local file."""
    url = url or "https://www.gleif.org/en/lei-data/lei-mapping/download-oc-to-lei-relationship-files"
    r=requests.get(url, timeout=timeout, headers={"User-Agent":"CorporateDataEngine/5.0"})
    r.raise_for_status()
    # The GLEIF page is intentionally returned as a page; the direct ZIP URL can be supplied by caller.
    return r.content


def sec_companyfacts(cik):
    ua=os.environ.get("SEC_USER_AGENT")
    if not ua: raise RuntimeError("SEC_USER_AGENT manquant")
    cik=str(cik).zfill(10)
    r=requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers={"User-Agent":ua}, timeout=30)
    r.raise_for_status()
    return r.json()


def import_sec_financials(conn, company_id, cik, max_metrics=40):
    data=sec_companyfacts(cik)
    facts=data.get("facts", {})
    imported=0
    for taxonomy, tags in facts.items():
        for tag, obj in tags.items():
            label=obj.get("label") or tag
            units=obj.get("units", {})
            for unit, observations in units.items():
                if not observations: continue
                latest=observations[-1]
                conn.execute("INSERT INTO financial_metrics VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    str(uuid.uuid4()), company_id, label, str(latest.get("val")), unit,
                    latest.get("end"), None, "SEC EDGAR XBRL",
                    f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(cik).zfill(10)}.json",
                    datetime.now(timezone.utc).isoformat()))
                imported += 1
                if imported >= max_metrics: conn.commit(); return imported
    conn.commit(); return imported


def import_sec_events(conn, company_id, cik, limit=25):
    ua=os.environ.get("SEC_USER_AGENT")
    if not ua: raise RuntimeError("SEC_USER_AGENT manquant")
    cik=str(cik).zfill(10)
    r=requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers={"User-Agent":ua}, timeout=30)
    r.raise_for_status(); data=r.json(); recent=data.get("filings",{}).get("recent",{})
    n=0
    forms=recent.get("form",[]); dates=recent.get("filingDate",[]); acc=recent.get("accessionNumber",[]); docs=recent.get("primaryDocument",[])
    for i in range(min(limit,len(forms))):
        url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc[i].replace('-','')}/{docs[i]}" if i < len(acc) and i < len(docs) else "https://www.sec.gov/edgar"
        conn.execute("INSERT INTO company_events VALUES (?,?,?,?,?,?,?,?,?)", (
            str(uuid.uuid4()), company_id, "SEC_FILING", dates[i] if i < len(dates) else None,
            forms[i], "SEC EDGAR", url, json.dumps({"form":forms[i],"accession":acc[i] if i<len(acc) else None}), datetime.now(timezone.utc).isoformat()))
        n+=1
    conn.commit(); return n


def enrich_company_master(company_id, conn=None):
    """Recalculate confidence from identifiers, evidence, domain and recency."""
    conn=conn or init_db_v5()
    row=conn.execute("SELECT legal_name,lei,primary_domain,company_number FROM companies WHERE company_id=?",(company_id,)).fetchone()
    if not row: return 0
    legal,lei,domain,number=row
    score=30
    if number: score+=25
    if lei: score+=25
    if domain: score+=10
    evidence_count=conn.execute("SELECT COUNT(*) FROM company_sources WHERE company_id=?",(company_id,)).fetchone()[0]
    score=min(100, score+min(10,evidence_count*2))
    conn.execute("UPDATE companies SET normalized_name=?,confidence_score=?,last_verified_at=? WHERE company_id=?",(
        normalize_company_name(legal),score,datetime.now(timezone.utc).isoformat(),company_id))
    conn.commit(); return score


def register_v5_sources(conn):
    now=datetime.now(timezone.utc).isoformat()
    for sid,cfg in GLOBAL_REGISTRY_CATALOG.items():
        conn.execute("INSERT OR REPLACE INTO source_registry (source_id,source_type,source_name,jurisdiction,source_url,source_license,collection_method,automated_access,personal_data,commercial_use_allowed,marketing_allowed,policy_class,terms_checked_at,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
            sid,cfg["type"],cfg["name"],cfg["country"],cfg["url"],"Review source terms","API/bulk/search","review","company-level","review","review",cfg["status"],now,"V5 global registry catalog; implement connector only after access/terms verification."))
    conn.commit()

def init_global_data_engine():
    conn=init_db_v5()
    register_open_sources(conn)
    register_v5_sources(conn)
    conn.close()



# ---------------------------------------------------------------------------
# V8 — ZERO-ARG GLOBAL DISCOVERY ENGINE
# ---------------------------------------------------------------------------
# Default mode: python agent_corporate_contacts_v8.py
# Builds a resumable corporate master from public structured sources.
# It does NOT scrape arbitrary websites and does not bypass authentication.

GLEIF_PUBLISHES_URL = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes"


def init_v8_schema(conn):
    for sql in [
        """CREATE TABLE IF NOT EXISTS officers (
            officer_id TEXT PRIMARY KEY,
            company_id TEXT,
            person_name TEXT NOT NULL,
            position TEXT,
            officer_uid TEXT,
            jurisdiction TEXT,
            start_date TEXT,
            end_date TEXT,
            address TEXT,
            source_name TEXT,
            source_url TEXT,
            raw_json TEXT,
            retrieved_at TEXT,
            UNIQUE(company_id, person_name, position, officer_uid, source_name)
        )""",
        """CREATE TABLE IF NOT EXISTS discovery_state (
            state_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            cursor TEXT,
            page INTEGER DEFAULT 0,
            records_seen INTEGER DEFAULT 0,
            records_imported INTEGER DEFAULT 0,
            last_run TEXT,
            status TEXT,
            notes TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS discovery_runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT,
            finished_at TEXT,
            source_name TEXT,
            records_seen INTEGER DEFAULT 0,
            records_imported INTEGER DEFAULT 0,
            status TEXT,
            error TEXT
        )""",
    ]:
        conn.execute(sql)
    conn.commit()


def _discovery_state(conn, source_name):
    return conn.execute("SELECT state_id,cursor,page,records_seen,records_imported FROM discovery_state WHERE source_name=?", (source_name,)).fetchone()


def _upsert_discovery_state(conn, source_name, cursor=None, page=0, seen=0, imported=0, status="running", notes=None):
    now=datetime.now(timezone.utc).isoformat()
    row=_discovery_state(conn, source_name)
    if row:
        conn.execute("UPDATE discovery_state SET cursor=?,page=?,records_seen=records_seen+?,records_imported=records_imported+?,last_run=?,status=?,notes=? WHERE source_name=?",
                     (cursor,page,seen,imported,now,status,notes,source_name))
    else:
        conn.execute("INSERT INTO discovery_state VALUES (?,?,?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()),source_name,cursor,page,seen,imported,now,status,notes))
    conn.commit()


def _latest_gleif_publish(file_type="lei2", extension="csv"):
    """Return the official direct Golden Copy endpoint; GLEIF redirects it to the current ZIP."""
    if extension not in {"csv", "json", "xml"}:
        raise ValueError("Unsupported GLEIF format")
    return f"https://goldencopy.gleif.org/api/v2/golden-copies/publishes/{file_type}/latest.{extension}"


def _gleif_csv_field(row, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def discover_gleif_bulk(conn, max_records=None):
    """Import the complete/latest GLEIF Level-1 Golden Copy CSV, resumably by LEI."""
    source="GLEIF Golden Copy"
    started=datetime.now(timezone.utc).isoformat(); run_id=str(uuid.uuid4())
    conn.execute("INSERT INTO discovery_runs VALUES (?,?,?,?,?,?,?,?)",(run_id,started,None,source,0,0,"running",None)); conn.commit()
    url=_latest_gleif_publish("lei2","csv")
    if not url:
        raise RuntimeError("Impossible de trouver automatiquement le fichier CSV GLEIF Golden Copy.")
    imported=seen=0
    last_lei=None
    try:
        # GLEIF's CSV endpoint redirects to a ZIP archive. Download to a temporary
        # file and stream the CSV member so the whole dataset is never loaded in RAM.
        import tempfile, zipfile
        with tempfile.NamedTemporaryFile(prefix="gleif_", suffix=".zip", delete=False) as tmp:
            tmp_path=tmp.name
            with requests.get(url, timeout=120, stream=True, allow_redirects=True) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=1024*1024):
                    if chunk: tmp.write(chunk)
        try:
            with zipfile.ZipFile(tmp_path) as zf:
                members=[n for n in zf.namelist() if n.lower().endswith(".csv")]
                if not members:
                    raise RuntimeError("Le fichier GLEIF téléchargé ne contient aucun CSV.")
                with zf.open(members[0]) as raw_bin:
                    text=io.TextIOWrapper(raw_bin, encoding="utf-8", errors="replace", newline="")
                    reader=csv.DictReader(text)
                    for row in reader:
                        seen += 1
                        lei=_gleif_csv_field(row,"LEI","lei")
                        if not lei: continue
                        last_lei=lei
                        legal=_gleif_csv_field(row,"Entity.LegalName","Entity.LegalName.x0020","LegalName","Entity.LegalName.Value") or ""
                        country=_gleif_csv_field(row,"Entity.LegalAddress.Country","Entity.LegalAddress.CountryCode","Entity.LegalAddress.CountryCode.Value")
                        city=_gleif_csv_field(row,"Entity.LegalAddress.City","Entity.LegalAddress.City.Value")
                        reg=_gleif_csv_field(row,"Entity.RegistrationAuthority.RegistrationAuthorityEntityID","Entity.RegistrationAuthorityEntityID")
                        status=_gleif_csv_field(row,"Entity.EntityStatus","Entity.Status")
                        # LEI is the stable global key: reuse an existing company whenever possible.
                        existing=conn.execute("SELECT company_id FROM companies WHERE lei=? LIMIT 1",(lei,)).fetchone()
                        cid=existing[0] if existing else str(uuid.uuid4())
                        if existing:
                            conn.execute("UPDATE companies SET legal_name=COALESCE(NULLIF(?,''),legal_name),jurisdiction=COALESCE(?,jurisdiction),hq_city=COALESCE(?,hq_city),hq_country=COALESCE(?,hq_country),last_verified_at=? WHERE company_id=?",(legal,country.lower() if country else None,city,country,datetime.now(timezone.utc).isoformat(),cid))
                        else:
                            conn.execute("INSERT INTO companies (company_id,legal_name,lei,jurisdiction,primary_domain,source_name,source_url,retrieved_at,registry_phone,registry_email,registry_website,hq_city,hq_state,hq_country,company_number,normalized_name,confidence_score,last_verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                         (cid,legal,lei,country.lower() if country else None,None,source,"https://www.gleif.org/en/lei-data/gleif-golden-copy",datetime.now(timezone.utc).isoformat(),None,None,None,city,None,country,reg,normalize_company_name(legal),99.0,datetime.now(timezone.utc).isoformat()))
                            imported += 1
                        add_identifier(conn,cid,"LEI",lei,source,"https://www.gleif.org/en/lei-data/gleif-golden-copy",99)
                        if reg: add_identifier(conn,cid,"registration_authority_id",reg,source,"https://www.gleif.org/en/lei-data/gleif-golden-copy",80)
                        add_company_evidence(conn,cid,"legal_name",legal,source,"https://www.gleif.org/en/lei-data/gleif-golden-copy",99)
                        if max_records and seen>=max_records: break
                        if seen % 1000 == 0:
                            conn.commit(); _upsert_discovery_state(conn,source,last_lei,0,1000,imported,status="running",notes=url); imported=0
                            print(f"[GLEIF] {seen:,} lignes parcourues; nouveaux enregistrements: cumulés dans la base")
                conn.commit()
        finally:
            try: os.unlink(tmp_path)
            except Exception: pass
        conn.commit()
        _upsert_discovery_state(conn,source,last_lei,0,seen % 1000,imported,status="complete",notes=url)
        conn.execute("UPDATE discovery_runs SET finished_at=?,records_seen=?,records_imported=?,status=? WHERE run_id=?",(datetime.now(timezone.utc).isoformat(),seen,imported,"complete",run_id)); conn.commit()
        return seen, imported, url
    except Exception as e:
        conn.rollback()
        conn.execute("UPDATE discovery_runs SET finished_at=?,records_seen=?,records_imported=?,status=?,error=? WHERE run_id=?",(datetime.now(timezone.utc).isoformat(),seen,imported,"error",str(e),run_id)); conn.commit()
        raise


def discover_gleif_relationships_sample(conn, max_companies=1000):
    """Enrich a bounded number of discovered LEIs with parent/child relationships."""
    rows=conn.execute("SELECT company_id,lei FROM companies WHERE lei IS NOT NULL ORDER BY last_verified_at DESC LIMIT ?",(int(max_companies),)).fetchall()
    total=0
    for cid,lei in rows:
        try: total += collect_gleif_relationships(cid,lei,conn)
        except Exception: pass
    return total


def discover_global_zero_arg():
    """Default V8 run: initialise and harvest a global corporate master without CLI parameters."""
    conn=init_db_v5(); init_v8_schema(conn); register_open_sources(conn); register_v5_sources(conn)
    print("\n=== GLOBAL CORPORATE DISCOVERY V8 ===")
    print("Mode: ZERO PARAMETER — découverte automatique de données corporate publiques structurées")
    print("Sources prioritaires: GLEIF Golden Copy + relations corporate; autres connecteurs restent conditionnés à leur accès officiel.")
    try:
        seen,new,url=discover_gleif_bulk(conn)
        print(f"[GLEIF] Terminé: {seen:,} enregistrements parcourus; nouveaux: {new:,}")
        rel=discover_gleif_relationships_sample(conn,1000)
        print(f"[GLEIF] Relations corporate enrichies: {rel:,}")
    except Exception as e:
        print(f"[GLEIF] Erreur: {e}")
    conn.close()
    print(f"\nBase: {DB_PATH}")
    print("Le prochain lancement reprend le principe de mise à jour; il n'est pas nécessaire de fournir un nom d'entreprise.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Global Corporate Data Engine")
    parser.add_argument("--name", help="Nom de l'entreprise")
    parser.add_argument("--country", help="Code pays ISO (ex: FR, DE, SG, ZA)")
    parser.add_argument("--domain", help="Nom de domaine de l'entreprise (ex: danone.com)")
    parser.add_argument("--list-directories", metavar="REGION",
                         help="Affiche les annuaires B2B/chambres de commerce à vérifier manuellement "
                              "pour une région (europe, africa, asia, caribbean), sans lancer le pipeline")
    parser.add_argument("--ags-chambers", metavar="PAYS", nargs="?", const="__all__",
                         help="Affiche l'AHK (chambre allemande) et l'AmCham (chambre américaine) pour un pays "
                              "où AGS est présent (ex: 'kenya'), ou pour tous les pays si omis")
    parser.add_argument("--chambers", metavar="PAYS", nargs="?", const="__all__",
                         help="Vue complète : AHK + AmCham + CCI France International + British Chambers "
                              "pour un pays où AGS est présent, ou pour tous les pays si omis")
    parser.add_argument("--chamber-members", metavar="SOURCE",
                         help="Collecte les entreprises d'une source GREEN/ORANGE autorisée "
                              "(ex: ahk_uae, ahk_saudi, amcham_za)")
    parser.add_argument("--max-pages", type=int, default=None,
                         help="Nombre maximal de pages pour --chamber-members")
    parser.add_argument("--sleep", type=float, default=1.5,
                         help="Pause entre requêtes chambre (secondes, défaut: 1.5)")
    parser.add_argument("--init-global", action="store_true",
                         help="Initialise la source registry et les nouvelles tables globales")
    parser.add_argument("--gleif-relationships", metavar="LEI",
                         help="Récupère les relations parent/enfant GLEIF pour un LEI")
    parser.add_argument("--ted-search", metavar="QUERY",
                         help="Recherche les avis TED publiés et les stocke dans procurement_events")
    parser.add_argument("--ted-pages", type=int, default=1, help="Nombre de pages TED à importer (max 250 avis/page)")
    parser.add_argument("--ted-limit", type=int, default=100, help="Nombre d'avis TED par page, max 250")
    parser.add_argument("--ted-iterate", action="store_true", help="Utilise le mode ITERATION TED pour parcourir les résultats sans limite globale")
    parser.add_argument("--worldbank-search", metavar="QUERY", help="Recherche les avis de marchés World Bank")
    parser.add_argument("--worldbank-country", metavar="ISO3", help="Filtre World Bank par code pays ISO3 (ex: KEN)")
    parser.add_argument("--worldbank-pages", type=int, default=1, help="Nombre de pages World Bank à importer")
    parser.add_argument("--worldbank-rows", type=int, default=100, help="Nombre d'avis World Bank par page")
    parser.add_argument("--sec-search", metavar="NAME",
                         help="Recherche une société dans SEC EDGAR (SEC_USER_AGENT requis)")
    parser.add_argument("--sec-facts", metavar="CIK", help="Importe les derniers indicateurs XBRL SEC dans financial_metrics")
    parser.add_argument("--sec-events", metavar="CIK", help="Importe les derniers dépôts SEC dans company_events")
    parser.add_argument("--gleif-group", metavar="LEI", help="Construit le groupe corporate GLEIF pour un LEI")
    parser.add_argument("--resolve-entities", action="store_true", help="Calcule les rapprochements d'entités locaux >=70/100")
    parser.add_argument("--source-catalog", action="store_true", help="Affiche le catalogue global des sources V5")
    parser.add_argument("--company-confidence", metavar="COMPANY_ID", help="Recalcule le score de confiance d'une société")
    parser.add_argument("--discovery-limit", type=int, default=None, help="Limite optionnelle de lignes GLEIF pour un test; sans paramètre, le mode global parcourt le fichier complet")
    args = parser.parse_args()

    if args.init_global:
        init_global_data_engine()
        print(f"Source registry initialisée dans {DB_PATH} ({len(GLOBAL_REGISTRY_CATALOG)} sources globales)")
    elif args.gleif_relationships:
        conn=init_db()
        lei=args.gleif_relationships
        company_id=str(uuid.uuid4())
        n=collect_gleif_relationships(company_id,lei,conn)
        conn.close()
        print(f"GLEIF: {n} relation(s) enregistrée(s)")
    elif args.ted_search:
        conn=init_db_v5()
        n=import_ted_query(conn,args.ted_search,pages=args.ted_pages,limit=args.ted_limit,iteration=args.ted_iterate)
        conn.close()
        print(f"TED: {n} avis enregistré(s)")
    elif args.worldbank_search is not None:
        conn=init_db_v5()
        n=import_world_bank_query(conn,args.worldbank_search,args.worldbank_country,pages=args.worldbank_pages,rows=args.worldbank_rows)
        conn.close()
        print(f"World Bank: {n} avis enregistré(s)")
    elif args.gleif_group:
        conn=init_db_v5()
        # Find an existing company with this LEI; if none, create a lightweight anchor.
        row=conn.execute("SELECT company_id FROM companies WHERE lei=?",(args.gleif_group,)).fetchone()
        cid=row[0] if row else str(uuid.uuid4())
        n=build_corporate_group(cid,args.gleif_group,conn)
        conn.close(); print(f"GLEIF group: {len(n)} relation(s) enregistrée(s)")
    elif args.sec_facts:
        conn=init_db_v5(); cid=str(uuid.uuid4())
        n=import_sec_financials(conn,cid,args.sec_facts); conn.close(); print(f"SEC XBRL: {n} métrique(s) importée(s)")
    elif args.sec_events:
        conn=init_db_v5(); cid=str(uuid.uuid4())
        n=import_sec_events(conn,cid,args.sec_events); conn.close(); print(f"SEC events: {n} événement(s) importé(s)")
    elif args.discovery_limit is not None:
        conn=init_db_v5(); init_v8_schema(conn); register_open_sources(conn); register_v5_sources(conn)
        seen,new,url=discover_gleif_bulk(conn,args.discovery_limit); conn.close()
        print(f"Discovery test: {seen:,} lignes parcourues; {new:,} nouvelles sociétés")
    elif args.source_catalog:
        print(json.dumps(GLOBAL_REGISTRY_CATALOG,ensure_ascii=False,indent=2))
    elif args.company_confidence:
        conn=init_db_v5(); print(f"Confidence: {enrich_company_master(args.company_confidence,conn):.1f}/100"); conn.close()
    elif args.resolve_entities:
        conn=init_db_v5(); rows=conn.execute("SELECT company_id,legal_name,lei,primary_domain,company_number,hq_city,hq_state,hq_country FROM companies").fetchall(); n=0
        for r in rows:
            c=dict(zip(["company_id","legal_name","lei","primary_domain","company_number","hq_city","hq_state","hq_country"],r)); n += len(resolve_company_candidates(conn,c))
        conn.close(); print(f"Entity resolution: {n} rapprochement(s) >=70/100")
    elif args.gleif_group:
        conn=init_db_v5()
        row=conn.execute("SELECT company_id FROM companies WHERE lei=?",(args.gleif_group,)).fetchone()
        cid=row[0] if row else str(uuid.uuid4())
        n=build_corporate_group(cid,args.gleif_group,conn)
        conn.close(); print(f"GLEIF group: {len(n)} relation(s) enregistrée(s)")
    elif args.sec_facts:
        conn=init_db_v5(); cid=str(uuid.uuid4())
        n=import_sec_financials(conn,cid,args.sec_facts); conn.close(); print(f"SEC XBRL: {n} métrique(s) importée(s)")
    elif args.sec_events:
        conn=init_db_v5(); cid=str(uuid.uuid4())
        n=import_sec_events(conn,cid,args.sec_events); conn.close(); print(f"SEC events: {n} événement(s) importé(s)")
    elif args.source_catalog:
        print(json.dumps(GLOBAL_REGISTRY_CATALOG,ensure_ascii=False,indent=2))
    elif args.company_confidence:
        conn=init_db_v5(); print(f"Confidence: {enrich_company_master(args.company_confidence,conn):.1f}/100"); conn.close()
    elif args.resolve_entities:
        conn=init_db_v5(); rows=conn.execute("SELECT company_id,legal_name,lei,primary_domain,company_number,hq_city,hq_state,hq_country FROM companies").fetchall(); n=0
        for r in rows:
            c=dict(zip(["company_id","legal_name","lei","primary_domain","company_number","hq_city","hq_state","hq_country"],r)); n += len(resolve_company_candidates(conn,c))
        conn.close(); print(f"Entity resolution: {n} rapprochement(s) >=70/100")
    elif args.sec_search:
        result=search_company_sec(args.sec_search)
        print(json.dumps(result,ensure_ascii=False,indent=2) if result else "Aucun résultat SEC")
    elif args.list_directories:
        print_manual_directories(args.list_directories)
    elif args.chamber_members:
        collect_chamber_members(args.chamber_members, max_pages=args.max_pages, sleep_seconds=args.sleep)
    elif args.chambers:
        print_full_chamber_report(None if args.chambers == "__all__" else args.chambers)
    elif args.ags_chambers:
        print_chambers_for_ags_countries(None if args.ags_chambers == "__all__" else args.ags_chambers)
    elif args.name:
        run_pipeline(args.name, jurisdiction=args.country, domain=args.domain)
    else:
        # V8: no parameter required. Run the global discovery engine.
        discover_global_zero_arg()
