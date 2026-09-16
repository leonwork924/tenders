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
- MàJ : l'agent élargit maintenant la recherche de titre/rôle aux lignes
  voisines (avant puis après) quand la même ligne que le nom ne donne rien
  d'exploitable -- fréquent sur les PDF en tableau. Il tente aussi de
  deviner le PAYS REPRÉSENTÉ (`represented_country`, ex. "Ambassadeur de
  France" -> "France", via la table COUNTRY_NAME_MAP) et une DATE
  (`start_date`, via DATE_RE) dans le même voisinage. Comme pour le reste de
  ce fichier : jamais testé contre un vrai site, donc à vérifier après le
  premier run réel -- voir le point suivant.
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

# Combien de lignes AVANT et APRÈS la ligne du nom on regarde pour retrouver
# un titre/rôle/pays quand la même ligne ne donne rien d'exploitable --
# fréquent dans les PDF où chaque info (nom, titre, pays) est sur sa propre
# ligne plutôt que "Titre Nom" sur une seule ligne comme le suppose
# NAME_LINE_RE. Volontairement plus large en amont (les tableaux/PDF
# gouvernementaux mettent le plus souvent le titre AVANT le nom).
TITLE_LOOKAROUND_BEFORE = 3
TITLE_LOOKAROUND_AFTER = 2

# Motifs de date usuels sur ces listes : numérique (12/03/2022, 2022-03-12)
# ou mois en toutes lettres, français ou anglais ("depuis mars 2022",
# "since March 2022"). Best-effort -- beaucoup de sites ne donneront jamais
# de date exploitable, voir avertissement en tête de fichier.
_MONTHS_FR = ("janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
              "septembre|octobre|novembre|décembre|decembre")
_MONTHS_EN = ("january|february|march|april|may|june|july|august|"
              "september|october|november|december")
DATE_RE = re.compile(
    r"\b(\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2}|"
    rf"\d{{1,2}}(?:er)?\s+(?:{_MONTHS_FR}|{_MONTHS_EN})\s+\d{{4}}|"
    rf"(?:{_MONTHS_FR}|{_MONTHS_EN})\s+\d{{4}})\b",
    re.IGNORECASE,
)
_MONTH_NUM = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def _normalize_date(raw: str) -> str | None:
    """Best-effort : convertit un texte de date repéré en ISO YYYY-MM-DD.
    Quand le jour est absent (juste "mars 2022"), retourne YYYY-MM-01 --
    approximatif par construction, à traiter comme "environ cette date-là",
    pas une date exacte."""
    raw = raw.strip().lower()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", raw)
    if m:
        return raw
    m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", raw)
    if m:
        d, mo, y = m.groups()
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})(?:er)?\s+(\w+)\s+(\d{4})$", raw)
    if m:
        d, month_name, y = m.groups()
        mo = _MONTH_NUM.get(month_name)
        if mo:
            return f"{int(y):04d}-{mo:02d}-{int(d):02d}"
        return None
    m = re.match(r"(\w+)\s+(\d{4})$", raw)
    if m:
        month_name, y = m.groups()
        mo = _MONTH_NUM.get(month_name)
        if mo:
            return f"{int(y):04d}-{mo:02d}-01"
    return None


def _find_date_near(lines: list[str], idx: int) -> str | None:
    """Même principe que _find_contact_near : cherche une date plausible
    dans le voisinage immédiat du nom, pas sur tout le document."""
    window = lines[max(0, idx - TITLE_LOOKAROUND_BEFORE): idx + 1 + CONTACT_LOOKAHEAD_LINES]
    for line in window:
        m = DATE_RE.search(line)
        if m:
            iso = _normalize_date(m.group(0))
            if iso:
                return iso
    return None

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


# Generated from pycountry + babel(fr) - 454 entries.
# Maps a lowercased country-name variant (English or French) to a
# canonical display name. Sorted longest-key-first at use time so
# 'Equatorial Guinea' matches before a shorter overlapping variant.
COUNTRY_NAME_MAP: dict[str, str] = {
    'afghanistan': 'Afghanistan',
    'afrique du sud': 'South Africa',
    'albania': 'Albania',
    'albanie': 'Albania',
    'algeria': 'Algeria',
    'algérie': 'Algeria',
    'allemagne': 'Germany',
    'american samoa': 'American Samoa',
    'andorra': 'Andorra',
    'andorre': 'Andorra',
    'angola': 'Angola',
    'anguilla': 'Anguilla',
    'antarctica': 'Antarctica',
    'antarctique': 'Antarctica',
    'antigua and barbuda': 'Antigua and Barbuda',
    'antigua-et-barbuda': 'Antigua and Barbuda',
    'arabie saoudite': 'Saudi Arabia',
    'argentina': 'Argentina',
    'argentine': 'Argentina',
    'armenia': 'Armenia',
    'arménie': 'Armenia',
    'aruba': 'Aruba',
    'australia': 'Australia',
    'australie': 'Australia',
    'austria': 'Austria',
    'autriche': 'Austria',
    'azerbaijan': 'Azerbaijan',
    'azerbaïdjan': 'Azerbaijan',
    'bahamas': 'Bahamas',
    'bahrain': 'Bahrain',
    'bahreïn': 'Bahrain',
    'bangladesh': 'Bangladesh',
    'barbade': 'Barbados',
    'barbados': 'Barbados',
    'belarus': 'Belarus',
    'belgique': 'Belgium',
    'belgium': 'Belgium',
    'belize': 'Belize',
    'benin': 'Benin',
    'bermuda': 'Bermuda',
    'bermudes': 'Bermuda',
    'bhoutan': 'Bhutan',
    'bhutan': 'Bhutan',
    'biélorussie': 'Belarus',
    'bolivia': 'Bolivia, Plurinational State of',
    'bolivia, plurinational state of': 'Bolivia, Plurinational State of',
    'bolivie': 'Bolivia, Plurinational State of',
    'bonaire, sint eustatius and saba': 'Bonaire, Sint Eustatius and Saba',
    'bosnia and herzegovina': 'Bosnia and Herzegovina',
    'bosnie-herzégovine': 'Bosnia and Herzegovina',
    'botswana': 'Botswana',
    'bouvet island': 'Bouvet Island',
    'brazil': 'Brazil',
    'british indian ocean territory': 'British Indian Ocean Territory',
    'brunei': 'Brunei Darussalam',
    'brunei darussalam': 'Brunei Darussalam',
    'brésil': 'Brazil',
    'bulgaria': 'Bulgaria',
    'bulgarie': 'Bulgaria',
    'burkina faso': 'Burkina Faso',
    'burma': 'Myanmar',
    'burundi': 'Burundi',
    'bénin': 'Benin',
    'cabo verde': 'Cabo Verde',
    'cambodge': 'Cambodia',
    'cambodia': 'Cambodia',
    'cameroon': 'Cameroon',
    'cameroun': 'Cameroon',
    'canada': 'Canada',
    'cap-vert': 'Cabo Verde',
    'cape verde': 'Cabo Verde',
    'cayman islands': 'Cayman Islands',
    'central african republic': 'Central African Republic',
    'chili': 'Chile',
    'china': 'China',
    'chine': 'China',
    'christmas island': 'Christmas Island',
    'chypre': 'Cyprus',
    'cocos (keeling) islands': 'Cocos (Keeling) Islands',
    'colombia': 'Colombia',
    'colombie': 'Colombia',
    'comores': 'Comoros',
    'comoros': 'Comoros',
    'congo': 'Congo',
    'congo, the democratic republic of the': 'Congo, The Democratic Republic of the',
    'congo-brazzaville': 'Congo',
    'congo-kinshasa': 'Congo, The Democratic Republic of the',
    'cook islands': 'Cook Islands',
    'coree du nord': "Korea, Democratic People's Republic of",
    'coree du sud': 'Korea, Republic of',
    'corée du nord': "Korea, Democratic People's Republic of",
    'corée du sud': 'Korea, Republic of',
    'costa rica': 'Costa Rica',
    "cote d'ivoire": "Côte d'Ivoire",
    'croatia': 'Croatia',
    'croatie': 'Croatia',
    'cuba': 'Cuba',
    'curaçao': 'Curaçao',
    'cyprus': 'Cyprus',
    'czech republic': 'Czechia',
    'czechia': 'Czechia',
    "côte d'ivoire": "Côte d'Ivoire",
    'côte d’ivoire': "Côte d'Ivoire",
    'danemark': 'Denmark',
    'denmark': 'Denmark',
    'djibouti': 'Djibouti',
    'dominica': 'Dominica',
    'dominican republic': 'Dominican Republic',
    'dominique': 'Dominica',
    'ecuador': 'Ecuador',
    'egypt': 'Egypt',
    'el salvador': 'El Salvador',
    'equatorial guinea': 'Equatorial Guinea',
    'eritrea': 'Eritrea',
    'espagne': 'Spain',
    'estonia': 'Estonia',
    'estonie': 'Estonia',
    'eswatini': 'Eswatini',
    'etats-unis': 'United States',
    'ethiopia': 'Ethiopia',
    'falkland islands (malvinas)': 'Falkland Islands (Malvinas)',
    'faroe islands': 'Faroe Islands',
    'fidji': 'Fiji',
    'fiji': 'Fiji',
    'finland': 'Finland',
    'finlande': 'Finland',
    'france': 'France',
    'french guiana': 'French Guiana',
    'french polynesia': 'French Polynesia',
    'french southern territories': 'French Southern Territories',
    'gabon': 'Gabon',
    'gambia': 'Gambia',
    'gambie': 'Gambia',
    'georgia': 'Georgia',
    'germany': 'Germany',
    'ghana': 'Ghana',
    'gibraltar': 'Gibraltar',
    'grande-bretagne': 'United Kingdom',
    'great britain': 'United Kingdom',
    'great-britain': 'United Kingdom',
    'greece': 'Greece',
    'greenland': 'Greenland',
    'grenada': 'Grenada',
    'grenade': 'Grenada',
    'groenland': 'Greenland',
    'grèce': 'Greece',
    'guadeloupe': 'Guadeloupe',
    'guam': 'Guam',
    'guatemala': 'Guatemala',
    'guernesey': 'Guernsey',
    'guernsey': 'Guernsey',
    'guinea-bissau': 'Guinea-Bissau',
    'guinée': 'Guinea',
    'guinée équatoriale': 'Equatorial Guinea',
    'guinée-bissau': 'Guinea-Bissau',
    'guyana': 'Guyana',
    'guyane française': 'French Guiana',
    'géorgie': 'Georgia',
    'géorgie du sud-et-les îles sandwich du sud': 'South Georgia and the South Sandwich Islands',
    'haiti': 'Haiti',
    'haïti': 'Haiti',
    'heard island and mcdonald islands': 'Heard Island and McDonald Islands',
    'holy see': 'Holy See (Vatican City State)',
    'holy see (vatican city state)': 'Holy See (Vatican City State)',
    'honduras': 'Honduras',
    'hong kong': 'Hong Kong',
    'hongrie': 'Hungary',
    'hungary': 'Hungary',
    'iceland': 'Iceland',
    'inde': 'India',
    'india': 'India',
    'indonesia': 'Indonesia',
    'indonésie': 'Indonesia',
    'irak': 'Iraq',
    'iran': 'Iran, Islamic Republic of',
    'iran, islamic republic of': 'Iran, Islamic Republic of',
    'iraq': 'Iraq',
    'ireland': 'Ireland',
    'irlande': 'Ireland',
    'islande': 'Iceland',
    'isle of man': 'Isle of Man',
    'israel': 'Israel',
    'israël': 'Israel',
    'italie': 'Italy',
    'italy': 'Italy',
    'ivory coast': "Côte d'Ivoire",
    'jamaica': 'Jamaica',
    'jamaïque': 'Jamaica',
    'japan': 'Japan',
    'japon': 'Japan',
    'jersey': 'Jersey',
    'jordanie': 'Jordan',
    'kazakhstan': 'Kazakhstan',
    'kenya': 'Kenya',
    'kirghizstan': 'Kyrgyzstan',
    'kiribati': 'Kiribati',
    "korea, democratic people's republic of": "Korea, Democratic People's Republic of",
    'korea, republic of': 'Korea, Republic of',
    'koweït': 'Kuwait',
    'kuwait': 'Kuwait',
    'kyrgyzstan': 'Kyrgyzstan',
    'la réunion': 'Réunion',
    "lao people's democratic republic": "Lao People's Democratic Republic",
    'laos': "Lao People's Democratic Republic",
    'latvia': 'Latvia',
    'lebanon': 'Lebanon',
    'lesotho': 'Lesotho',
    'lettonie': 'Latvia',
    'liban': 'Lebanon',
    'liberia': 'Liberia',
    'libya': 'Libya',
    'libye': 'Libya',
    'liechtenstein': 'Liechtenstein',
    'lithuania': 'Lithuania',
    'lituanie': 'Lithuania',
    'luxembourg': 'Luxembourg',
    'macao': 'Macao',
    'macedonia': 'North Macedonia',
    'macédoine du nord': 'North Macedonia',
    'madagascar': 'Madagascar',
    'malaisie': 'Malaysia',
    'malawi': 'Malawi',
    'malaysia': 'Malaysia',
    'maldives': 'Maldives',
    'malta': 'Malta',
    'malte': 'Malta',
    'maroc': 'Morocco',
    'marshall islands': 'Marshall Islands',
    'martinique': 'Martinique',
    'maurice': 'Mauritius',
    'mauritania': 'Mauritania',
    'mauritanie': 'Mauritania',
    'mauritius': 'Mauritius',
    'mayotte': 'Mayotte',
    'mexico': 'Mexico',
    'mexique': 'Mexico',
    'micronesia, federated states of': 'Micronesia, Federated States of',
    'micronésie': 'Micronesia, Federated States of',
    'moldavie': 'Moldova, Republic of',
    'moldova': 'Moldova, Republic of',
    'moldova, republic of': 'Moldova, Republic of',
    'monaco': 'Monaco',
    'mongolia': 'Mongolia',
    'mongolie': 'Mongolia',
    'montenegro': 'Montenegro',
    'montserrat': 'Montserrat',
    'monténégro': 'Montenegro',
    'morocco': 'Morocco',
    'mozambique': 'Mozambique',
    'myanmar': 'Myanmar',
    'myanmar (birmanie)': 'Myanmar',
    'namibia': 'Namibia',
    'namibie': 'Namibia',
    'nauru': 'Nauru',
    'nepal': 'Nepal',
    'netherlands': 'Netherlands',
    'new caledonia': 'New Caledonia',
    'new zealand': 'New Zealand',
    'nicaragua': 'Nicaragua',
    'nigeria': 'Nigeria',
    'niue': 'Niue',
    'norfolk island': 'Norfolk Island',
    'north korea': "Korea, Democratic People's Republic of",
    'north macedonia': 'North Macedonia',
    'northern mariana islands': 'Northern Mariana Islands',
    'norvège': 'Norway',
    'norway': 'Norway',
    'nouvelle-calédonie': 'New Caledonia',
    'nouvelle-zélande': 'New Zealand',
    'népal': 'Nepal',
    'oman': 'Oman',
    'ouganda': 'Uganda',
    'ouzbékistan': 'Uzbekistan',
    'pakistan': 'Pakistan',
    'palaos': 'Palau',
    'palau': 'Palau',
    'palestine, state of': 'Palestine, State of',
    'panama': 'Panama',
    'papouasie-nouvelle-guinée': 'Papua New Guinea',
    'papua new guinea': 'Papua New Guinea',
    'paraguay': 'Paraguay',
    'pays-bas': 'Netherlands',
    'pays-bas caribéens': 'Bonaire, Sint Eustatius and Saba',
    'peru': 'Peru',
    'philippines': 'Philippines',
    'pitcairn': 'Pitcairn',
    'poland': 'Poland',
    'pologne': 'Poland',
    'polynésie française': 'French Polynesia',
    'porto rico': 'Puerto Rico',
    'portugal': 'Portugal',
    'puerto rico': 'Puerto Rico',
    'pérou': 'Peru',
    'qatar': 'Qatar',
    'r.a.s. chinoise de hong kong': 'Hong Kong',
    'r.a.s. chinoise de macao': 'Macao',
    'romania': 'Romania',
    'roumanie': 'Romania',
    'royaume-uni': 'United Kingdom',
    'russia': 'Russian Federation',
    'russian federation': 'Russian Federation',
    'russie': 'Russian Federation',
    'rwanda': 'Rwanda',
    'république centrafricaine': 'Central African Republic',
    'république dominicaine': 'Dominican Republic',
    'réunion': 'Réunion',
    'sahara occidental': 'Western Sahara',
    'saint barthélemy': 'Saint Barthélemy',
    'saint helena, ascension and tristan da cunha': 'Saint Helena, Ascension and Tristan da Cunha',
    'saint kitts and nevis': 'Saint Kitts and Nevis',
    'saint lucia': 'Saint Lucia',
    'saint martin (french part)': 'Saint Martin (French part)',
    'saint pierre and miquelon': 'Saint Pierre and Miquelon',
    'saint vincent and the grenadines': 'Saint Vincent and the Grenadines',
    'saint-barthélemy': 'Saint Barthélemy',
    'saint-christophe-et-niévès': 'Saint Kitts and Nevis',
    'saint-marin': 'San Marino',
    'saint-martin': 'Saint Martin (French part)',
    'saint-martin (partie néerlandaise)': 'Sint Maarten (Dutch part)',
    'saint-pierre-et-miquelon': 'Saint Pierre and Miquelon',
    'saint-vincent-et-les grenadines': 'Saint Vincent and the Grenadines',
    'sainte-hélène': 'Saint Helena, Ascension and Tristan da Cunha',
    'sainte-lucie': 'Saint Lucia',
    'salvador': 'El Salvador',
    'samoa': 'Samoa',
    'samoa américaines': 'American Samoa',
    'san marino': 'San Marino',
    'sao tome and principe': 'Sao Tome and Principe',
    'sao tomé-et-principe': 'Sao Tome and Principe',
    'saudi arabia': 'Saudi Arabia',
    'senegal': 'Senegal',
    'serbia': 'Serbia',
    'serbie': 'Serbia',
    'seychelles': 'Seychelles',
    'sierra leone': 'Sierra Leone',
    'singapore': 'Singapore',
    'singapour': 'Singapore',
    'sint maarten (dutch part)': 'Sint Maarten (Dutch part)',
    'slovakia': 'Slovakia',
    'slovaquie': 'Slovakia',
    'slovenia': 'Slovenia',
    'slovénie': 'Slovenia',
    'solomon islands': 'Solomon Islands',
    'somalia': 'Somalia',
    'somalie': 'Somalia',
    'soudan': 'Sudan',
    'soudan du sud': 'South Sudan',
    'south africa': 'South Africa',
    'south georgia and the south sandwich islands': 'South Georgia and the South Sandwich Islands',
    'south korea': 'Korea, Republic of',
    'south sudan': 'South Sudan',
    'spain': 'Spain',
    'sri lanka': 'Sri Lanka',
    'sudan': 'Sudan',
    'suisse': 'Switzerland',
    'suriname': 'Suriname',
    'suède': 'Sweden',
    'svalbard and jan mayen': 'Svalbard and Jan Mayen',
    'svalbard et jan mayen': 'Svalbard and Jan Mayen',
    'swaziland': 'Eswatini',
    'sweden': 'Sweden',
    'switzerland': 'Switzerland',
    'syria': 'Syrian Arab Republic',
    'syrian arab republic': 'Syrian Arab Republic',
    'syrie': 'Syrian Arab Republic',
    'sénégal': 'Senegal',
    'tadjikistan': 'Tajikistan',
    'taiwan': 'Taiwan, Province of China',
    'taiwan, province of china': 'Taiwan, Province of China',
    'tajikistan': 'Tajikistan',
    'tanzania': 'Tanzania, United Republic of',
    'tanzania, united republic of': 'Tanzania, United Republic of',
    'tanzanie': 'Tanzania, United Republic of',
    'taïwan': 'Taiwan, Province of China',
    'tchad': 'Chad',
    'tchéquie': 'Czechia',
    'terres australes françaises': 'French Southern Territories',
    'territoire britannique de l’océan indien': 'British Indian Ocean Territory',
    'territoires palestiniens': 'Palestine, State of',
    'thailand': 'Thailand',
    'thaïlande': 'Thailand',
    'timor oriental': 'Timor-Leste',
    'timor-leste': 'Timor-Leste',
    'togo': 'Togo',
    'tokelau': 'Tokelau',
    'tonga': 'Tonga',
    'trinidad and tobago': 'Trinidad and Tobago',
    'trinité-et-tobago': 'Trinidad and Tobago',
    'tunisia': 'Tunisia',
    'tunisie': 'Tunisia',
    'turkmenistan': 'Turkmenistan',
    'turkménistan': 'Turkmenistan',
    'turks and caicos islands': 'Turks and Caicos Islands',
    'turquie': 'Türkiye',
    'tuvalu': 'Tuvalu',
    'türkiye': 'Türkiye',
    'u.a.e.': 'United Arab Emirates',
    'u.k.': 'United Kingdom',
    'u.s.a.': 'United States',
    'uae': 'United Arab Emirates',
    'uganda': 'Uganda',
    'uk': 'United Kingdom',
    'ukraine': 'Ukraine',
    'united arab emirates': 'United Arab Emirates',
    'united kingdom': 'United Kingdom',
    'united states': 'United States',
    'united states minor outlying islands': 'United States Minor Outlying Islands',
    'uruguay': 'Uruguay',
    'usa': 'United States',
    'uzbekistan': 'Uzbekistan',
    'vanuatu': 'Vanuatu',
    'vatican': 'Holy See (Vatican City State)',
    'venezuela': 'Venezuela, Bolivarian Republic of',
    'venezuela, bolivarian republic of': 'Venezuela, Bolivarian Republic of',
    'viet nam': 'Viet Nam',
    'vietnam': 'Viet Nam',
    'virgin islands, british': 'Virgin Islands, British',
    'virgin islands, u.s.': 'Virgin Islands, U.S.',
    'viêt nam': 'Viet Nam',
    'wallis and futuna': 'Wallis and Futuna',
    'wallis-et-futuna': 'Wallis and Futuna',
    'western sahara': 'Western Sahara',
    'yemen': 'Yemen',
    'yémen': 'Yemen',
    'zambia': 'Zambia',
    'zambie': 'Zambia',
    'zimbabwe': 'Zimbabwe',
    'åland islands': 'Åland Islands',
    'égypte': 'Egypt',
    'émirats arabes unis': 'United Arab Emirates',
    'équateur': 'Ecuador',
    'érythrée': 'Eritrea',
    'état de la cité du vatican': 'Holy See (Vatican City State)',
    'états-unis': 'United States',
    'éthiopie': 'Ethiopia',
    'île bouvet': 'Bouvet Island',
    'île christmas': 'Christmas Island',
    'île de man': 'Isle of Man',
    'île norfolk': 'Norfolk Island',
    'îles caïmans': 'Cayman Islands',
    'îles cocos': 'Cocos (Keeling) Islands',
    'îles cook': 'Cook Islands',
    'îles féroé': 'Faroe Islands',
    'îles heard-et-macdonald': 'Heard Island and McDonald Islands',
    'îles malouines': 'Falkland Islands (Malvinas)',
    'îles mariannes du nord': 'Northern Mariana Islands',
    'îles marshall': 'Marshall Islands',
    'îles mineures éloignées des états-unis': 'United States Minor Outlying Islands',
    'îles pitcairn': 'Pitcairn',
    'îles salomon': 'Solomon Islands',
    'îles turques-et-caïques': 'Turks and Caicos Islands',
    'îles vierges britanniques': 'Virgin Islands, British',
    'îles vierges des états-unis': 'Virgin Islands, U.S.',
    'îles åland': 'Åland Islands',
}


_COUNTRY_KEYS_BY_LEN = sorted(COUNTRY_NAME_MAP, key=len, reverse=True)


def guess_represented_country(text: str) -> str | None:
    """Cherche un nom de pays (FR ou EN) dans le texte donné -- le plus
    long match d'abord, pour qu'"Equatorial Guinea" ne se fasse pas voler
    par un sous-match plus court. Best-effort, voir COUNTRY_NAME_MAP."""
    t = text.lower()
    for key in _COUNTRY_KEYS_BY_LEN:
        if key in t:
            return COUNTRY_NAME_MAP[key]
    return None


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
    # start_date reste souvent vide : le scraping HTML/PDF brut donne rarement
    # une date de prise de fonction exploitable, mais _find_date_near() en
    # retrouve maintenant certaines (best-effort -- voir DATE_RE). end_date
    # reste quasi toujours vide : aucune liste consultée n'annonce à l'avance
    # la fin d'un mandat.
    start_date: str | None = None   # date de prise de fonction (ISO YYYY-MM-DD), si connue
    end_date: str | None = None     # date de fin de fonction (ISO YYYY-MM-DD), si connue et déjà passée
    data_source: str = "web_scrape"  # "web_scrape" | "org_official"
    role: str = "ambassador"        # "ambassador" | "consul" | "diplomatic_staff" | "other" (voir classify_role)
    phone: str | None = None        # coordonnée institutionnelle si trouvée à proximité du nom
    email: str | None = None        # idem — voir avertissement en en-tête de fichier
    # Pays que la personne représente (déduit du titre, ex. "Ambassadeur de
    # France" -> "France") -- PAS le pays du site consulté (voir
    # country_source ci-dessus), qui est le pays où elle est en poste.
    # Best-effort via guess_represented_country() ; None si pas identifié.
    represented_country: str | None = None


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



# Un nom "nu", seul sur sa ligne (mise en page en tableau/colonnes ou le
# titre est sur une autre ligne que le nom). Ancre debut/fin de ligne
# (contrairement a NAME_PATTERN utilise seul, qui matcherait n'importe ou) --
# c'est ce qui borne le risque de faux positifs : on n'accepte une ligne
# "nue" que si elle ne contient QUE ca, rien d'autre autour.
_BARE_NAME_RE = re.compile(r"^" + NAME_PATTERN + r"$")

# Pour retirer un mot de titre qui aurait ete aspire par erreur DANS le
# groupe nom (TITLE_PATTERN et NAME_PATTERN acceptent tous les deux "un mot
# qui commence par une majuscule", donc "H.E. Ambassador Maria Rodriguez"
# peut capturer name="Ambassador Maria Rodriguez" si un 2e mot-titre suit le
# premier sur la meme ligne). Retire un ou plusieurs mots-titre en tete du
# nom capture, tant qu'il en reste.
_LEADING_TITLE_WORDS_RE = re.compile(r"^(?:" + TITLE_PATTERN + r")\s+")


def _strip_leaking_title(name: str) -> str:
    while True:
        new_name = _LEADING_TITLE_WORDS_RE.sub("", name, count=1)
        if new_name == name or len(new_name.split()) < 2:
            return name
        name = new_name


def _is_plausible_name(name: str) -> bool:
    # Filtre anti faux-positifs grossiers :
    # - trop court (un seul mot)
    # - tout en majuscules (quasi toujours un en-tete/pied de page de PDF
    #   happe par erreur -- "CZECH REPUBLIC", "MINISTRY OF..." -- jamais un
    #   vrai nom de personne mis en forme ainsi sur ces listes. Le
    #   commentaire d'origine pretendait deja filtrer ce cas mais ne le
    #   faisait pas -- corrige ici.
    return len(name.split()) >= 2 and name != name.upper()


def _title_context_near(all_lines: list[str], idx: int) -> tuple[str, str, str | None]:
    """Cherche titre/role/pays dans les lignes voisines de idx (avant puis
    apres) -- frequent dans les tableaux/PDF ou chaque info est sur sa
    propre ligne plutot que "Titre Nom" en une seule ligne. Retourne
    (title_guess, role, represented_country) ; role="other" si rien trouve."""
    before = all_lines[max(0, idx - TITLE_LOOKAROUND_BEFORE): idx]
    after = all_lines[idx + 1: idx + 1 + TITLE_LOOKAROUND_AFTER]
    title_guess, role, represented_country = "", "other", None
    for candidate_line in list(reversed(before)) + after:
        candidate = candidate_line.strip()
        if not candidate or len(candidate) > 200:
            continue
        candidate_role = classify_role(candidate)
        if candidate_role != "other" and role == "other":
            role = candidate_role
            title_guess = candidate[-60:]
        if not represented_country:
            represented_country = guess_represented_country(candidate)
        if role != "other" and represented_country:
            break
    return title_guess, role, represented_country


def extract_names_from_text(text: str) -> list[tuple[str, str, str, str, str | None, str | None, str | None, str | None]]:
    """Retourne une liste de
    (titre_devine, nom, ligne_brute, role, phone, email, represented_country, date_devinee).

    Deux passes :
    1. "Titre Nom" sur la meme ligne (cas le plus fiable, ex. "H.E. Ambassador
       Jane Doe").
    2. Nom seul sur sa ligne, avec un titre confirme sur une ligne voisine
       (mise en page en tableau/colonnes). Volontairement plus strict : on
       n'accepte l'entree QUE si un vrai mot-titre est trouve a proximite,
       pour ne pas transformer toute ligne de 2-4 mots capitalises en faux
       diplomate.
    """
    results = []
    matched_lines: set[int] = set()
    all_lines = text.splitlines()

    # --- Passe 1 : "Titre Nom" meme ligne ---
    for idx, line_raw in enumerate(all_lines):
        line = line_raw.strip()
        if not line or len(line) > 200:
            continue
        for m in NAME_LINE_RE.finditer(line):
            name = _strip_leaking_title(m.group(1).strip())
            if not _is_plausible_name(name):
                continue

            # Cherche où le nom (éventuellement raccourci par le strip
            # ci-dessus) démarre réellement dans la ligne, pour que tout mot-
            # titre qu'on vient de lui retirer (ex. "Ambassador" dans "H.E.
            # Ambassador Jane Doe") reste dans title_guess au lieu de se
            # perdre entre les deux.
            name_start = line.find(name, m.start())
            title_guess = (line[:name_start] if name_start != -1 else line[: m.start()]).strip()[-30:]
            role = classify_role(title_guess)
            represented_country = guess_represented_country(title_guess)

            if role == "other" or not title_guess:
                ctx_title, ctx_role, ctx_country = _title_context_near(all_lines, idx)
                if ctx_role != "other":
                    role = ctx_role
                    title_guess = title_guess or ctx_title
                represented_country = represented_country or ctx_country

            phone, email = _find_contact_near(all_lines, idx)
            date_guess = _find_date_near(all_lines, idx)
            results.append((title_guess, name, line, role, phone, email, represented_country, date_guess))
            matched_lines.add(idx)

    # --- Passe 2 : nom seul sur sa ligne, titre confirme a proximite ---
    for idx, line_raw in enumerate(all_lines):
        if idx in matched_lines:
            continue
        line = line_raw.strip()
        if not line or len(line) > 80:
            continue
        m = _BARE_NAME_RE.match(line)
        if not m:
            continue
        name = _strip_leaking_title(m.group(1).strip())
        if not _is_plausible_name(name):
            continue

        title_guess, role, represented_country = _title_context_near(all_lines, idx)
        if role == "other":
            continue  # aucun titre confirme a proximite -- trop risque, on saute plutot que d'inventer

        phone, email = _find_contact_near(all_lines, idx)
        date_guess = _find_date_near(all_lines, idx)
        results.append((title_guess, name, line, role, phone, email, represented_country, date_guess))

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
        for title_guess, name, raw_line, role, phone, email, represented_country, date_guess in extract_names_from_text(text):
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
                    represented_country=represented_country,
                    start_date=date_guess,
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
