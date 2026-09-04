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

# Titres qui précèdent typiquement un nom dans une liste diplomatique
TITLE_PATTERN = (
    r"(?:H\.?E\.?|S\.?E\.?|Son Excellence|His Excellency|Her Excellency|"
    r"Ambassador|Ambassadeur|Ambassadrice|Amb\.|Chargé d'Affaires|"
    r"Chargée d'Affaires|Chargé d'affaires a\.i\.|High Commissioner|"
    r"Haut[- ]Commissaire|Dr\.?|Mr\.?|Mrs\.?|Ms\.?|M\.|Mme\.?)"
)
# Un "nom" = 2 à 4 mots commençant par une majuscule (heuristique simple,
# fonctionne raisonnablement en alphabet latin ; à adapter pour d'autres
# systèmes d'écriture)
NAME_PATTERN = r"([A-ZÀ-Ý][\wÀ-ÿ'’\.-]*(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'’\.-]*){1,3})"

NAME_LINE_RE = re.compile(TITLE_PATTERN + r"\s+" + NAME_PATTERN)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Téléphone : assez permissif pour couvrir les formats internationaux
# courants, mais on exige un préfixe +NN ou plusieurs groupes séparés
# (espace/point/tiret) pour limiter les faux positifs sur des suites de
# chiffres qui n'en sont pas (dates, codes postaux...).
PHONE_RE = re.compile(
    r"\+\d{1,3}[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?){2,5}\d{2,4}"
    r"|\b\d{2,4}(?:[\s.-]\d{2,4}){3,6}\b"
)

# Nb de lignes regardées après un nom pour y chercher un email/téléphone.
# Sur une liste diplomatique, les coordonnées de l'ambassade suivent
# souvent immédiatement le nom/titre dans le même bloc d'adresse.
CONTACT_WINDOW_LINES = 6


@dataclass
class DiplomatEntry:
    country_source: str      # pays dont le site a été consulté
    region: str
    name: str
    title: str
    raw_line: str
    source_url: str
    scraped_at: str
    # Champs optionnels, remplis notamment par la source Wikidata
    # (voir wikidata_source.py) — absents ou vides pour le scraping HTML/PDF
    # brut, qui ne donne quasiment jamais de date exploitable.
    start_date: str | None = None   # date de prise de fonction (ISO YYYY-MM-DD), si connue
    end_date: str | None = None     # date de fin de fonction (ISO YYYY-MM-DD), si connue et déjà passée
    data_source: str = "web_scrape"  # "web_scrape" | "wikidata"
    # Coordonnées trouvées à proximité du nom -- presque toujours le
    # standard institutionnel de l'ambassade plutôt qu'une ligne
    # personnelle, mais ce n'est pas garanti (voir CONTACT_WINDOW_LINES).
    email: str | None = None
    phone: str | None = None


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


def find_diplomatic_list_link(html: str, base_url: str) -> str | None:
    """Cherche dans la page un lien dont le texte ou l'URL évoque la liste
    diplomatique officielle (souvent un PDF)."""
    soup = BeautifulSoup(html, "html.parser")
    best_candidate = None
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").lower()
        href = a["href"].lower()
        if any(kw in text or kw in href for kw in LIST_LINK_KEYWORDS):
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


def find_nearby_contact(lines: list[str], start_idx: int) -> tuple[str | None, str | None]:
    """Cherche un email et un téléphone dans les quelques lignes qui suivent
    une entrée nom+titre. Heuristique : peut rater le bon contact (absent
    des CONTACT_WINDOW_LINES suivantes) ou, si plusieurs diplomates sont
    listés sans bloc de contact individuel bien délimité, attribuer par
    erreur les coordonnées d'une entrée voisine."""
    email = None
    phone = None
    for line in lines[start_idx: start_idx + CONTACT_WINDOW_LINES]:
        if email is None:
            m = EMAIL_RE.search(line)
            if m:
                email = m.group(0)
        if phone is None:
            m = PHONE_RE.search(line)
            if m:
                candidate = m.group(0).strip()
                if sum(c.isdigit() for c in candidate) >= 7:  # anti faux-positif grossier
                    phone = candidate
        if email and phone:
            break
    return email, phone


def extract_names_from_text(text: str) -> list[tuple[str, str, str, str | None, str | None]]:
    """Retourne une liste de (titre_devine, nom, ligne_brute, email, telephone)."""
    lines = text.splitlines()
    results = []
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line or len(line) > 200:
            continue
        for m in NAME_LINE_RE.finditer(line):
            title_guess = line[: m.start()].strip()[-30:]  # contexte avant le nom
            name = m.group(1).strip()
            # Filtre anti faux-positifs grossiers (trop court, tout en maj, etc.)
            if len(name.split()) < 2:
                continue
            email, phone = find_nearby_contact(lines, idx)
            results.append((title_guess, name, line, email, phone))
    return results


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

    content_type = resp.headers.get("Content-Type", "")
    now = datetime.now(timezone.utc).isoformat()
    found: list[DiplomatEntry] = []

    if "pdf" in content_type or url.lower().endswith(".pdf"):
        text = extract_text_from_pdf(resp.content)
        source_used = url
    else:
        html = resp.text
        list_link = find_diplomatic_list_link(html, url)
        if list_link:
            print(f"  -> lien de liste diplomatique détecté : {list_link}")
            time.sleep(RATE_LIMIT_SECONDS)
            resp2 = fetch(list_link)
            if resp2 is None:
                return []
            if list_link.lower().endswith(".pdf") or "pdf" in resp2.headers.get("Content-Type", ""):
                text = extract_text_from_pdf(resp2.content)
            else:
                text = BeautifulSoup(resp2.text, "html.parser").get_text("\n")
            source_used = list_link
        else:
            text = BeautifulSoup(html, "html.parser").get_text("\n")
            source_used = url

    for title_guess, name, raw_line, email, phone in extract_names_from_text(text):
        found.append(
            DiplomatEntry(
                country_source=country,
                region=region,
                name=name,
                title=title_guess,
                raw_line=raw_line,
                source_url=source_used,
                scraped_at=now,
                email=email,
                phone=phone,
            )
        )

    print(f"  -> {len(found)} entrée(s) candidate(s) extraite(s)")
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
