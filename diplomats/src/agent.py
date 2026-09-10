"""
agent.py — Agent d'agrégation des listes diplomatiques
=======================================================

Ce script parcourt les sources listées dans data/sources.json (sites des
ministères des Affaires étrangères), tente de repérer la page ou le PDF
contenant la "liste diplomatique" officielle, puis en extrait les noms et
titres des personnes qui y figurent.

⚠️ À LIRE AVANT UTILISATION
---------------------------
- Ce script doit être exécuté DEPUIS TON PROPRE ORDINATEUR (ou un serveur que
  tu contrôles) : l'environnement dans lequel Claude a écrit ce code n'a pas
  accès à Internet en dehors de quelques registres de paquets, donc ce script
  n'a jamais été testé "en vrai" contre les sites gouvernementaux listés.
- Chaque site de MAE a une structure différente (page HTML, PDF, Excel,
  parfois contenu chargé en JavaScript). L'extraction générique ci-dessous
  (extract_names_from_text) est une HEURISTIQUE qui repère des motifs de
  type "Titre + Nom Propre" (S.E. M./Mme, Ambassadeur, H.E., Dr, etc.).
  Elle fonctionnera bien sur certains sites, mal ou pas du tout sur d'autres
  (notamment ceux dont la liste est une image scannée, ou générée en JS).
  → Pour un site donné qui ne marche pas, il faut écrire un petit parseur
    dédié (voir la fonction CUSTOM_PARSERS en bas de fichier, à compléter).
- Respecte le robots.txt et les conditions d'utilisation de chaque site.
  Le script vérifie robots.txt automatiquement et saute la source si
  l'accès automatisé est interdit (voir check_robots()).
- Ajoute un délai entre les requêtes (RATE_LIMIT_SECONDS) pour ne pas
  surcharger des sites gouvernementaux, souvent peu robustes.
- Ces listes diplomatiques concernent des personnes physiques (noms, titres,
  parfois coordonnées). Même publiques, ces données restent des données
  personnelles : limite la conservation à ce qui est nécessaire, indique
  la source et la date de collecte, et prévois un moyen de retirer une
  entrée sur demande justifiée.
- Depuis la dernière version, l'agent cherche AUSSI un lien vers la liste
  des consuls honoraires (souvent une page séparée du corps diplomatique
  sur le même site de MAE) et classe chaque entrée trouvée en "ambassador"
  ou "consul" via classify_role() — une heuristique par mots-clés, pas une
  vérité absolue (voir le champ `role` en sortie).
- Coordonnées (téléphone/email) : quand elles apparaissent DANS LE MÊME
  BLOC DE TEXTE que le nom (pratique fréquente sur ces listes — tél/fax/
  email de la chancellerie ou du consulat), elles sont capturées dans les
  champs `phone` / `email`. Il s'agit presque toujours de coordonnées
  INSTITUTIONNELLES (standard de l'ambassade), pas du numéro ou de
  l'adresse personnelle de l'individu — cette distinction n'est pas
  garantie à 100%, en particulier pour les consuls honoraires (souvent des
  particuliers dont l'adresse professionnelle communiquée EST leur adresse
  personnelle). Redouble de prudence sur l'usage qui en est fait (voir
  "Précautions légales et éthiques" plus bas) : ne construis pas de fichier
  de prospection ou de contact de masse à partir de ces données.

Usage:
    pip install -r requirements.txt
    python agent.py --sources data/sources.json --out data/diplomats.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
except ImportError:  # pdfplumber est optionnel tant qu'on ne traite pas de PDF
    pdfplumber = None

USER_AGENT = "MobilitasAGS-DiplomaticSourceAgent/1.0 (+contact: tenders@overseasam.com)"
RATE_LIMIT_SECONDS = 2.0
REQUEST_TIMEOUT = 20

# Mots-clés (multi-langues) qui signalent un lien vers la liste diplomatique
LIST_LINK_KEYWORDS = [
    "diplomatic list", "diplomatic corps", "diplomatic directory",
    "corps diplomatique", "liste diplomatique", "annuaire diplomatique",
    "foreign missions", "foreign representatives", "diplomatic and consular",
    "diplomatic & consular", "heads of mission", "accredited diplomats",
    "diplomatic protocol", "protocol department",
]

# Mots-clés spécifiques aux consuls HONORAIRES (souvent une page/section à
# part du corps diplomatique proprement dit — voir README, section "Corps
# consulaire honoraire"). On les cherche séparément car beaucoup de sites de
# MAE ont deux pages distinctes : une pour les ambassadeurs accrédités chez
# eux, une pour LEUR PROPRE réseau de consuls honoraires à l'étranger.
CONSULAR_LINK_KEYWORDS = [
    "honorary consul", "honorary consuls", "consul honoraire",
    "consuls honoraires", "corps consulaire", "consular corps",
    "cónsul honorario", "cónsules honorarios", "console onorario",
    "consoli onorari", "honorarkonsul",
]

# Titres qui précèdent typiquement un nom dans une liste diplomatique
TITLE_PATTERN = (
    r"(?:H\.?E\.?|S\.?E\.?|Son Excellence|His Excellency|Her Excellency|"
    r"Ambassador|Ambassadeur|Ambassadrice|Amb\.|Chargé d'Affaires|"
    r"Chargée d'Affaires|Chargé d'affaires a\.i\.|High Commissioner|"
    r"Haut[- ]Commissaire|"
    r"Consul[- ]General(?:e)?|Consul[- ]Général(?:e)?|Honorary Consul(?:[- ]General)?|"
    r"Consul[- ]Honoraire|Consule[- ]Honoraire|Consul Général Honoraire|"
    r"Vice[- ]Consul(?:[- ]Honoraire| Honorary)?|Cónsul[- ]Honorari[oa]|"
    r"Console Onorari[oa]|Honorarkonsul|Consul|Consule|Consulesa|"
    # Reste du personnel diplomatique (rangs classiques, hors ambassadeur/consul) :
    r"Deputy Head of Mission|Deputy Chief of Mission|Chef Adjoint de Mission|"
    r"Minister[- ]Counsell?or|Ministre[- ]Conseill[eè]r|Ministre Plénipotentiaire|"
    r"Minister Plenipotentiary|"
    r"Counsell?or|Conseill[eè]r(?:e)?|"
    r"First Secretary|Second Secretary|Third Secretary|"
    r"Premi[eè]re? Secrétaire|Deuxième Secrétaire|Troisième Secrétaire|"
    r"(?:Military|Defence|Defense|Naval|Air|Cultural|Commercial|Press|Economic)\s+Attaché|"
    r"Attaché(?:e)?\s+(?:militaire|de défense|naval|de l'air|culturel(?:le)?|"
    r"commercial(?:e)?|de presse|économique)|"
    r"Attaché(?:e)?|"
    r"Dr\.?|Mr\.?|Mrs\.?|Ms\.?|M\.|Mme\.?)"
)
# Un "nom" = 2 à 4 mots commençant par une majuscule (heuristique simple,
# fonctionne raisonnablement en alphabet latin ; à adapter pour d'autres
# systèmes d'écriture)
NAME_PATTERN = r"([A-ZÀ-Ý][\wÀ-ÿ'’\.-]*(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'’\.-]*){1,3})"

NAME_LINE_RE = re.compile(TITLE_PATTERN + r"\s+" + NAME_PATTERN)

# Motifs de coordonnées, cherchés dans le voisinage immédiat d'un nom trouvé
# (même ligne + quelques lignes suivantes) — PAS sur tout le document, pour
# éviter d'associer par erreur le téléphone/email d'une tierce entrée.
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+\d{1,3}[\s.-]?)?(?:\(0?\d{1,4}\)[\s.-]?)?(?:\d[\s.-]?){6,12}\d"
)
# Un numéro de téléphone plausible a au moins 7 chiffres ; on filtre les
# faux positifs (années, codes postaux isolés) après coup avec ce minimum.
MIN_PHONE_DIGITS = 7
CONTACT_LOOKAHEAD_LINES = 3  # combien de lignes après le nom on regarde

# Mots-clés utilisés pour classer une entrée entre "ambassadeur/diplomate"
# et "consul honoraire" a posteriori, à partir du titre repéré. Une entrée
# qui ne matche ni l'un ni l'autre est classée "other" (ex : simple "Mr."
# sans autre contexte, capturé par erreur — à vérifier manuellement).
_CONSUL_KEYWORDS = (
    "consul", "consule", "consulesa", "cónsul", "console", "konsul",
)
_AMBASSADOR_KEYWORDS = (
    "ambassad", "excellency", "excellence", "high commissioner",
    "haut-commissaire", "haut commissaire", "chargé d'affaires",
    "chargée d'affaires",
)
_STAFF_KEYWORDS = (
    "counsell", "conseill", "secretary", "secrétaire", "attaché", "attachee",
    "attachée", "deputy head of mission", "deputy chief of mission",
    "chef adjoint", "plenipotentiary", "plénipotentiaire",
)


def classify_role(title_guess: str) -> str:
    """Classe grossièrement une entrée à partir du titre repéré autour du
    nom. Heuristique simple par mots-clés, pas une vérité absolue — utile
    surtout pour filtrer/afficher séparément ambassadeurs, consuls
    honoraires et reste du personnel diplomatique dans le site de
    consultation."""
    t = title_guess.lower()
    if any(k in t for k in _CONSUL_KEYWORDS):
        return "consul"
    if any(k in t for k in _AMBASSADOR_KEYWORDS):
        return "ambassador"
    if any(k in t for k in _STAFF_KEYWORDS):
        return "diplomatic_staff"
    return "other"


@dataclass
class DiplomatEntry:
    country_source: str      # pays dont le site a été consulté
    region: str
    name: str
    title: str
    raw_line: str
    source_url: str
    scraped_at: str
    # start_date/end_date restent généralement vides : le scraping HTML/PDF
    # brut donne quasiment jamais de date de prise de fonction exploitable.
    start_date: str | None = None   # date de prise de fonction (ISO YYYY-MM-DD), si connue
    end_date: str | None = None     # date de fin de fonction (ISO YYYY-MM-DD), si connue et déjà passée
    data_source: str = "web_scrape"  # "web_scrape" | "org_official"
    role: str = "ambassador"        # "ambassador" | "consul" | "diplomatic_staff" | "other" (voir classify_role)
    phone: str | None = None        # coordonnée institutionnelle si trouvée à proximité du nom
    email: str | None = None        # idem — voir avertissement en en-tête de fichier


def check_robots(url: str) -> bool:
    """Retourne True si l'accès automatisé est autorisé par robots.txt."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = robotparser.RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        # Si robots.txt est inaccessible, on avance avec prudence (True)
        # mais tu peux choisir de passer à False pour être conservateur.
        return True


def fetch(url: str) -> requests.Response | None:
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        print(f"  [erreur] impossible de récupérer {url}: {e}")
        return None


def find_list_link(html: str, base_url: str, keywords: list[str]) -> str | None:
    """Cherche dans la page un lien dont le texte ou l'URL évoque l'une des
    listes recherchées (diplomatique OU consulaire honoraire, selon les
    `keywords` passés). Souvent un PDF."""
    soup = BeautifulSoup(html, "html.parser")
    best_candidate = None
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").lower()
        href = a["href"].lower()
        if any(kw in text or kw in href for kw in keywords):
            candidate = urljoin(base_url, a["href"])
            # On préfère un PDF si plusieurs candidats existent
            if candidate.endswith(".pdf"):
                return candidate
            best_candidate = best_candidate or candidate
    return best_candidate


def extract_text_from_pdf(content: bytes) -> str:
    if pdfplumber is None:
        print("  [avertissement] pdfplumber non installé, PDF ignoré.")
        return ""
    import io
    text_parts = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
    except Exception as e:
        print(f"  [erreur] lecture PDF impossible: {e}")
    return "\n".join(text_parts)


def _find_contact_near(lines: list[str], idx: int) -> tuple[str | None, str | None]:
    """Cherche un email et un téléphone plausibles sur la ligne du nom (idx)
    et les CONTACT_LOOKAHEAD_LINES suivantes seulement — pas sur tout le
    document, pour éviter d'associer par erreur les coordonnées d'une autre
    entrée située plus loin dans la liste."""
    email, phone = None, None
    window = lines[idx: idx + 1 + CONTACT_LOOKAHEAD_LINES]
    for line in window:
        if email is None:
            m = EMAIL_RE.search(line)
            if m:
                email = m.group(0)
        if phone is None:
            for m in PHONE_RE.finditer(line):
                digits = re.sub(r"\D", "", m.group(0))
                if len(digits) >= MIN_PHONE_DIGITS:
                    phone = m.group(0).strip()
                    break
        if email and phone:
            break
    return phone, email


def extract_names_from_text(text: str) -> list[tuple[str, str, str, str, str | None, str | None]]:
    """Retourne une liste de (titre_devine, nom, ligne_brute, role, phone, email)."""
    results = []
    all_lines = text.splitlines()
    for idx, line in enumerate(all_lines):
        line = line.strip()
        if not line or len(line) > 200:
            continue
        for m in NAME_LINE_RE.finditer(line):
            title_guess = line[: m.start()].strip()[-30:]  # contexte avant le nom
            name = m.group(1).strip()
            # Filtre anti faux-positifs grossiers (trop court, tout en maj, etc.)
            if len(name.split()) < 2:
                continue
            role = classify_role(line)
            phone, email = _find_contact_near(all_lines, idx)
            results.append((title_guess, name, line, role, phone, email))
    return results


def _extract_from_page(resp: requests.Response, url: str) -> str:
    """Convertit une réponse HTTP (HTML ou PDF) en texte brut exploitable."""
    if url.lower().endswith(".pdf") or "pdf" in resp.headers.get("Content-Type", ""):
        return extract_text_from_pdf(resp.content)
    return BeautifulSoup(resp.text, "html.parser").get_text("\n")


def process_source(entry: dict) -> list[DiplomatEntry]:
    country = entry["country"]
    region = entry["region"]
    url = entry["url"]
    print(f"[{region}] {country} -> {url}")

    if not check_robots(url):
        print("  [ignoré] robots.txt interdit l'accès automatisé.")
        return []

    resp = fetch(url)
    if resp is None:
        return []

    now = datetime.now(timezone.utc).isoformat()
    found: list[DiplomatEntry] = []

    if url.lower().endswith(".pdf") or "pdf" in resp.headers.get("Content-Type", ""):
        # La source elle-même est déjà le PDF/texte à analyser : pas de lien
        # de liste distinct à chercher (ni diplomatique, ni consulaire).
        text_sources = [(extract_text_from_pdf(resp.content), url)]
    else:
        html = resp.text
        text_sources = []

        diplo_link = find_list_link(html, url, LIST_LINK_KEYWORDS)
        if diplo_link:
            print(f"  -> lien de liste diplomatique détecté : {diplo_link}")
            time.sleep(RATE_LIMIT_SECONDS)
            resp2 = fetch(diplo_link)
            if resp2 is not None:
                text_sources.append((_extract_from_page(resp2, diplo_link), diplo_link))
        else:
            # Pas de lien dédié trouvé : on analyse la page elle-même,
            # certains sites listent tout sur une seule page.
            text_sources.append((BeautifulSoup(html, "html.parser").get_text("\n"), url))

        consular_link = find_list_link(html, url, CONSULAR_LINK_KEYWORDS)
        if consular_link and consular_link not in {u for _, u in text_sources}:
            print(f"  -> lien de liste consulaire (consuls honoraires) détecté : {consular_link}")
            time.sleep(RATE_LIMIT_SECONDS)
            resp3 = fetch(consular_link)
            if resp3 is not None:
                text_sources.append((_extract_from_page(resp3, consular_link), consular_link))

    for text, source_used in text_sources:
        for title_guess, name, raw_line, role, phone, email in extract_names_from_text(text):
            found.append(
                DiplomatEntry(
                    country_source=country,
                    region=region,
                    name=name,
                    title=title_guess,
                    raw_line=raw_line,
                    source_url=source_used,
                    scraped_at=now,
                    role=role,
                    phone=phone,
                    email=email,
                )
            )

    n_ambassadors = sum(1 for e in found if e.role == "ambassador")
    n_consuls = sum(1 for e in found if e.role == "consul")
    n_other = len(found) - n_ambassadors - n_consuls
    print(
        f"  -> {len(found)} entrée(s) candidate(s) "
        f"({n_ambassadors} ambassadeur(s), {n_consuls} consul(s), {n_other} autre(s))"
    )
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="data/sources.json")
    parser.add_argument("--out", default="data/diplomats.json")
    parser.add_argument("--limit", type=int, default=None,
                         help="Ne traiter que les N premières sources (pour tester)")
    args = parser.parse_args()

    sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    if args.limit:
        sources = sources[: args.limit]

    all_entries: list[DiplomatEntry] = []
    for entry in sources:
        try:
            all_entries.extend(process_source(entry))
        except Exception as e:
            print(f"  [erreur inattendue] {entry['country']}: {e}")
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
