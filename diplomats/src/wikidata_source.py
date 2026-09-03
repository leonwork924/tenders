"""
wikidata_source.py — Source complémentaire : ambassadeurs sur Wikidata
=======================================================================

Interroge le point de terminaison SPARQL public de Wikidata pour récupérer,
pour chaque pays de `data/sources.json`, la liste des personnes ayant occupé
un poste d'ambassadeur (ou assimilé : haut-commissaire, chargé d'affaires)
EN PARTANCE de ce pays, avec — quand l'information existe sur Wikidata — la
date de prise de fonction et, le cas échéant, la date de fin.

⚠️ À LIRE AVANT UTILISATION
---------------------------
- Comme pour agent.py, ce script n'a JAMAIS été exécuté contre le vrai point
  de terminaison Wikidata (https://query.wikidata.org/sparql) : l'environnement
  où il a été écrit n'a pas accès au web ouvert. La requête SPARQL a été
  construite avec soin et suit la documentation publique du modèle de données
  Wikidata pour les postes diplomatiques, mais elle doit être vérifiée et
  ajustée chez toi (voir section "Limites" plus bas).
- Wikidata impose un User-Agent identifiable et des limites de débit
  raisonnables sur son point de terminaison public : voir
  https://www.wikidata.org/wiki/Wikidata:Data_access#Query_Service
  RENSEIGNE tenders@overseasam.com dans USER_AGENT avant utilisation réelle.
- Les données de Wikidata sont sous licence CC0 (domaine public) pour les
  données structurées elles-mêmes ; il reste néanmoins recommandé de citer
  Wikidata comme source (ce que fait déjà le champ source_url ci-dessous,
  qui pointe vers l'item Wikidata précis).
- Wikidata est une base collaborative : elle peut être incomplète, en retard
  sur la réalité (un nouvel ambassadeur nommé n'y sera pas forcément saisi
  tout de suite), ou contenir des erreurs. Traite-la comme une source
  SECONDAIRE, à recouper avec le site officiel — pas comme une vérité
  absolue. Le champ data_source="wikidata" dans la sortie permet de la
  distinguer du scraping direct des MAE.

## Limites connues de la requête

- Le modèle Wikidata pour "ambassadeur" est hétérogène : certains items sont
  "instance of" un poste générique (Q121998 = ambassador) directement dans
  P39 (position held), d'autres utilisent un poste spécifique par pays/pays
  hôte ("Ambassador of France to Germany", lui-même sous-classe de Q121998),
  d'autres encore utilisent un item par ambassade. La requête ci-dessous
  couvre le cas le plus courant (P39 -> item qui est instance-of ou
  sous-classe de Q121998, "ambassadeur"), mais ne prétend pas être exhaustive.
- La qualification "de quel pays vers quel pays" n'est pas toujours présente
  ou pas toujours sous la même propriété (P1001 "applies to jurisdiction"
  côté déclaration, ou déductible du libellé du poste lui-même). On tente
  les deux et on garde ce qu'on trouve, avec le pays hôte en best-effort.
- Beaucoup d'ambassadeurs n'ont tout simplement pas de fiche Wikidata
  (notamment pour de petits pays ou des postes récents) : ne pas s'attendre
  à une couverture complète, loin de là. C'est un complément, pas un
  substitut au scraping des sites officiels.
- Les dates (P580 début, P582 fin) manquent sur une bonne partie des
  déclarations même quand la personne elle-même est correctement identifiée.

Usage:
    python src/wikidata_source.py --sources data/sources.json \
        --out data/diplomats_wikidata.json [--limit N] [--current-only]
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import requests

# Renseigne une adresse de contact réelle : Wikidata peut limiter ou bloquer
# les clients dont le User-Agent n'identifie personne, voir la politique
# d'accès citée plus haut.
USER_AGENT = "MobilitasAGS-DiplomaticSourceAgent/1.0 (+contact: tenders@overseasam.com)"
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
RATE_LIMIT_SECONDS = 2.0   # une requête par pays, avec pause entre chacune
REQUEST_TIMEOUT = 60       # les requêtes SPARQL peuvent être lentes

# Q121998 = "ambassador" sur Wikidata. On récupère aussi les postes qui en
# sont des sous-classes (ex : "Ambassador of France to Germany").
SPARQL_QUERY_TEMPLATE = """
SELECT ?person ?personLabel ?position ?positionLabel ?start ?end ?hostLabel WHERE {{
  ?person p:P39 ?statement.
  ?statement ps:P39 ?position.
  ?position wdt:P279* wd:Q121998.

  # Le pays d'envoi : soit qualificatif direct sur la déclaration
  # (P1001 "applies to jurisdiction" côté pays d'origine dans certains
  # modèles), soit propriété du poste lui-même.
  {{
    ?statement pq:P1001 ?country.
  }} UNION {{
    ?position wdt:P1001 ?country.
  }}
  ?country rdfs:label ?countryLabelRaw.
  FILTER(LANG(?countryLabelRaw) = "en")
  FILTER(STR(?countryLabelRaw) = "{country_en}")

  OPTIONAL {{ ?statement pq:P580 ?start. }}
  OPTIONAL {{ ?statement pq:P582 ?end. }}
  OPTIONAL {{ ?position wdt:P17 ?host. }}

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "fr,en". }}
}}
ORDER BY DESC(?start)
LIMIT 50
"""


def query_wikidata_for_country(country_name_en: str) -> list[dict]:
    """Interroge le point de terminaison SPARQL pour un pays donné (nom
    anglais, tel qu'il apparaît dans data/sources.json).

    Retourne une liste de dicts bruts (résultats SPARQL) — potentiellement
    vide si le pays n'est pas trouvé sous ce libellé exact sur Wikidata
    (voir limites ci-dessus : un nom de pays peut être écrit différemment,
    ex. "Ivory Coast" vs "Côte d'Ivoire" ; à corriger au cas par cas si le
    pays ne renvoie aucun résultat).
    """
    query = SPARQL_QUERY_TEMPLATE.format(country_en=country_name_en)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/sparql-results+json",
    }
    try:
        resp = requests.get(
            SPARQL_ENDPOINT,
            params={"query": query, "format": "json"},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("results", {}).get("bindings", [])
    except requests.RequestException as e:
        print(f"  [erreur] requête Wikidata échouée pour {country_name_en}: {e}")
        return []
    except ValueError as e:
        print(f"  [erreur] réponse Wikidata non-JSON pour {country_name_en}: {e}")
        return []


def _get(binding: dict, key: str) -> str | None:
    return binding.get(key, {}).get("value")


def _to_date(value: str | None) -> str | None:
    """Wikidata renvoie des dates au format ISO avec heure (ex:
    2021-05-03T00:00:00Z). On ne garde que la partie date."""
    if not value:
        return None
    return value.split("T")[0]


def bindings_to_entries(
    bindings: list[dict],
    country: str,
    region: str,
    current_only: bool,
) -> list[dict]:
    now_iso = datetime.now(timezone.utc).isoformat()
    entries = []
    seen = set()
    for b in bindings:
        person_label = _get(b, "personLabel")
        position_label = _get(b, "positionLabel")
        person_uri = _get(b, "person")
        start = _to_date(_get(b, "start"))
        end = _to_date(_get(b, "end"))
        host_label = _get(b, "hostLabel")

        if not person_label or not person_uri:
            continue

        if current_only and end:
            # Une date de fin renseignée signifie (en général) que la
            # personne n'est plus en poste.
            continue

        dedup_key = (person_uri, position_label, start)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        entries.append(
            {
                "country_source": country,
                "region": region,
                "name": person_label,
                "title": position_label or "Ambassadeur (Wikidata, poste non précisé)",
                "raw_line": (
                    f"{person_label} — {position_label or 'poste non précisé'}"
                    + (f" (auprès de : {host_label})" if host_label else "")
                ),
                "source_url": person_uri,
                "scraped_at": now_iso,
                "start_date": start,
                "end_date": end,
                "data_source": "wikidata",
            }
        )
    return entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="data/sources.json")
    parser.add_argument("--out", default="data/diplomats_wikidata.json")
    parser.add_argument("--limit", type=int, default=None,
                         help="Ne traiter que les N premiers pays (pour tester)")
    parser.add_argument("--current-only", action="store_true",
                         help="Exclut les entrées avec une date de fin de fonction connue")
    args = parser.parse_args()

    sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    if args.limit:
        sources = sources[: args.limit]

    all_entries: list[dict] = []
    for entry in sources:
        country = entry["country"]
        region = entry["region"]
        print(f"[Wikidata] {country}...")
        bindings = query_wikidata_for_country(country)
        found = bindings_to_entries(bindings, country, region, args.current_only)
        print(f"  -> {len(found)} entrée(s)")
        all_entries.extend(found)
        time.sleep(RATE_LIMIT_SECONDS)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(all_entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nTerminé : {len(all_entries)} entrées Wikidata écrites dans {out_path}")


if __name__ == "__main__":
    main()
