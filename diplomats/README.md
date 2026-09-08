# Registre du corps diplomatique — Mobilitas / AGS

Un petit agent qui parcourt les sites des ministères des Affaires étrangères
listés dans ton fichier de sources, tente d'y repérer la liste diplomatique
officielle, en extrait les noms et titres, puis affiche le tout dans un site
web consultable et filtrable.

## Structure du projet

```
diplo_agent/
├── data/
│   ├── sources_raw.txt          # ta liste de sources d'origine (corrigée)
│   ├── sources.json             # la même liste, structurée (générée)
│   ├── diplomats.sample.json    # jeu de données FICTIF pour tester le site
│   └── diplomats.json           # sortie réelle de l'agent (à générer chez toi)
├── src/
│   ├── parse_sources.py         # convertit le .txt en .json structuré
│   ├── agent.py                 # agent de scraping / extraction (ambassadeurs + consuls honoraires)
│   ├── wikidata_source.py       # source complémentaire : ambassadeurs + dates via Wikidata
│   ├── org_delegations.py       # source complémentaire : délégations ONU / UA / OSCE
│   └── merge_sources.py         # fusionne les trois sources ci-dessus
├── web/
│   ├── index.html               # la page de consultation
│   ├── style.css
│   └── app.js
├── requirements.txt
└── README.md                    # ce fichier
```

## ⚠️ Important : ce qui a été testé, ce qui ne l'a pas été

L'environnement dans lequel ce code a été écrit n'a **pas accès à Internet**
en dehors de quelques registres de paquets (PyPI, npm, GitHub). Concrètement :

- ✅ Le parseur `parse_sources.py` a été exécuté et testé : il extrait
  correctement 117 sources depuis ton fichier texte.
- ✅ La fonction d'extraction de noms (`extract_names_from_text`) a été
  testée unitairement sur un exemple de liste diplomatique reconstitué à la
  main, avec de bons résultats.
- ❌ L'agent **n'a jamais été lancé pour de vrai contre les sites des MAE**.
  Je ne peux donc pas garantir qu'il fonctionnera du premier coup sur chacun
  des ~100 sites — c'est structurellement impossible à garantir vu à quel
  point les formats diffèrent d'un pays à l'autre (HTML, PDF, Excel, contenu
  généré en JavaScript, listes scannées en image, etc.).
- Le site web (`web/`) fonctionne dès maintenant avec des **données fictives
  de démonstration** (`diplomats.sample.json`), pour que tu puisses voir le
  rendu tout de suite.

## Installation et premier lancement

```bash
cd diplo_agent
pip install -r requirements.txt

# 1. (déjà fait, mais tu peux relancer si tu modifies sources_raw.txt)
python src/parse_sources.py data/sources_raw.txt data/sources.json

# 2. Lance l'agent sur quelques sources d'abord, pour voir ce qui marche
python src/agent.py --sources data/sources.json --out data/diplomats.json --limit 5

# 3. Une fois satisfait, lance sur toutes les sources
python src/agent.py --sources data/sources.json --out data/diplomats.json

# 4. (optionnel) Complète avec Wikidata : ambassadeurs identifiés + dates
#    de prise de fonction quand elles existent. Teste d'abord sur peu de
#    pays, la requête SPARQL n'a jamais été validée en conditions réelles
#    (voir l'en-tête de wikidata_source.py pour le détail des limites).
python src/wikidata_source.py --sources data/sources.json --out data/diplomats_wikidata.json --limit 5
python src/wikidata_source.py --sources data/sources.json --out data/diplomats_wikidata.json

# 5. (optionnel) Délégations auprès des organisations internationales
#    (ONU, Union africaine, OSCE — voir org_delegations.py pour le détail
#    des sources et de leurs limites, très différentes d'une organisation
#    à l'autre). Teste organisation par organisation d'abord :
python src/org_delegations.py --org un --out data/org_un.json
python src/org_delegations.py --org au --out data/org_au.json
python src/org_delegations.py --org osce --out data/org_osce.json
python src/org_delegations.py --org un_ngo --out data/org_un_ngo.json
# Puis, une fois chacune vérifiée, en une seule fois :
python src/org_delegations.py --org all --out data/org_delegations.json

# 6. Fusionne les sources en un seul fichier consultable
python src/merge_sources.py \
    --scraped data/diplomats.json \
    --wikidata data/diplomats_wikidata.json \
    --org data/org_delegations.json \
    --out data/diplomats_merged.json
```

Puis ouvre `web/index.html` dans un navigateur (ou sers le dossier avec
`python -m http.server` depuis la racine du projet), et dans `web/app.js`
remplace :

```js
const DATA_URL = "../data/diplomats.sample.json";
const IS_DEMO_DATA = true;
```

par :

```js
const DATA_URL = "../data/diplomats_merged.json";
const IS_DEMO_DATA = false;
```

Si tu ne veux pas utiliser Wikidata, tu peux pointer directement sur
`data/diplomats.json` (sortie du scraping seul) — les colonnes "Arrivée" et
"Provenance" du site resteront simplement vides / à "Site officiel".

Le site propose plusieurs filtres combinables : région, pays source, type
(ambassadeur / consul honoraire / délégation internationale), **organisation
internationale** (ONU, Union africaine, OSCE — n'apparaît que si le jeu de
données contient des délégations internationales), ancienneté au poste (voir
plus bas) et provenance des données.

## Pourquoi ça ne marchera pas partout du premier coup

L'agent utilise une **heuristique générique** : il cherche un lien dont le
texte évoque une "liste diplomatique" (dans plusieurs langues), puis repère
dans le texte des motifs du type `Titre + Nom Propre` (S.E. M./Mme, H.E. Mr.,
Ambassadeur, Dr., etc.). Cette approche fonctionne raisonnablement bien sur
des listes bien structurées (PDF texte, page HTML propre), mais échouera ou
donnera de faux positifs sur :

- les listes publiées comme **images scannées** (nécessite de l'OCR — voir
  la piste `pytesseract` ci-dessous) ;
- les pages dont le contenu est **chargé en JavaScript** après coup (il
  faudrait alors un outil comme Playwright ou Selenium plutôt que
  `requests`) ;
- les alphabets non-latins (cyrillique, arabe, chinois, etc.) : le motif de
  détection de nom (`NAME_PATTERN`) suppose des mots commençant par une
  majuscule latine, ce qui ne s'applique pas à ces écritures ;
- les sites qui bloquent les requêtes automatisées (certains renvoient une
  page de vérification ou un blocage anti-bot).

**Pour aller plus loin**, la bonne approche est incrémentale : lance l'agent
avec `--limit`, regarde ce qui marche, et ajoute un petit parseur dédié par
pays dans `agent.py` (une fonction qui sait lire spécifiquement le format du
site X) plutôt que de chercher une solution universelle — il n'y en a pas,
vu la fragmentation des formats.

## Source complémentaire : Wikidata

`src/wikidata_source.py` interroge le point de terminaison SPARQL public de
Wikidata (`https://query.wikidata.org/sparql`) pour retrouver, pour chaque
pays de `sources.json`, les personnes ayant occupé un poste d'ambassadeur
(ou assimilé), avec la date de prise de fonction (et de fin, si connue et
déjà passée) quand cette information est renseignée sur Wikidata.

Points importants :

- **Jamais testé en conditions réelles**, pour la même raison que pour
  `agent.py` (pas d'accès web dans l'environnement où le code a été écrit).
  La requête SPARQL suit le modèle de données documenté par Wikidata pour
  les postes diplomatiques, mais à valider et ajuster chez toi — voir les
  limites détaillées en en-tête de `wikidata_source.py`.
- **Couverture partielle** : Wikidata ne connaît pas tous les ambassadeurs
  en poste, loin de là, en particulier pour les petits pays ou les
  nominations récentes. C'est un complément au scraping, pas un substitut.
- **Fiabilité** : base collaborative, potentiellement en retard sur la
  réalité ou contenant des erreurs. Traite-la comme une source secondaire à
  recouper, jamais comme une vérité absolue — le site affiche d'ailleurs
  une provenance ("Site officiel" / "Wikidata") pour chaque entrée, afin
  que ça reste visible pour qui consulte le registre.
- **Licence** : les données structurées de Wikidata sont sous licence CC0
  (domaine public), mais il reste d'usage de citer la source — c'est ce que
  fait le champ `source_url` (lien direct vers l'item Wikidata concerné).
- **Débit** : une requête par pays, avec une pause de 2 secondes entre
  chacune (`RATE_LIMIT_SECONDS`), et un `User-Agent` identifiable comme
  demandé par la politique d'accès de Wikidata. Renseigne une vraie adresse
  de contact dans `USER_AGENT` avant un usage au-delà d'un test.
- **Rapprochement avec le scraping** (`merge_sources.py`) : heuristique par
  nom normalisé + pays, volontairement prudente (en cas de doute, les deux
  entrées sont gardées séparées plutôt que fusionnées à tort sur un
  homonyme). Les entrées Wikidata sans correspondance côté scraping sont
  conservées telles quelles, avec `data_source: "wikidata"`.

## Coordonnées (téléphone / email)

`agent.py` capture désormais un email et/ou un téléphone quand ils
apparaissent DANS LES QUELQUES LIGNES QUI SUIVENT un nom repéré dans le
texte source (voir `_find_contact_near`, fenêtre de 3 lignes par défaut) —
pratique fréquente sur les listes diplomatiques (tél/fax/email de la
chancellerie juste après le nom du chef de mission). `org_delegations.py`
fait de même si le CSV ONU contient des colonnes de ce type (peu probable
pour ce dataset historique, mais le code est prêt si ça change).

**Point d'attention important** : dans l'écrasante majorité des cas, il
s'agit de coordonnées **institutionnelles** (standard de l'ambassade, pas
la ligne personnelle de l'individu) — sauf pour une partie des consuls
honoraires, où l'adresse professionnelle communiquée peut être une adresse
personnelle (ce sont souvent des particuliers, pas des fonctionnaires).
Cette source ne cherche PAS activement des coordonnées personnelles au-delà
de ce qui est déjà publié sur la même page que le nom : pas de recherche
inversée, pas de croisement avec d'autres bases pour compléter des fiches.
Utilise ces coordonnées pour du contact officiel légitime, pas pour
constituer un fichier de prospection ou d'envoi en masse — voir aussi la
section RGPD ci-dessous, qui s'applique avec d'autant plus de rigueur à des
coordonnées de contact directes.

## Consuls honoraires (corps consulaire)

Depuis cette version, `agent.py` cherche AUSSI, sur chaque site de MAE déjà
listé dans `sources.json`, un lien vers la liste des **consuls honoraires**
(souvent une page séparée du corps diplomatique proprement dit — mots-clés
recherchés : "honorary consul", "consuls honoraires", "corps consulaire",
etc., voir `CONSULAR_LINK_KEYWORDS` dans `agent.py`). Chaque entrée trouvée
est classée `role: "ambassador"` ou `role: "consul"` par une heuristique
simple par mots-clés (`classify_role()`), affichée comme filtre "Type" dans
le site.

Deux limites importantes à connaître :

- **Beaucoup de MAE ne publient pas d'annuaire nominatif centralisé de leurs
  propres consuls honoraires à l'étranger** — c'est souvent chaque
  ambassade/consulat général de rattachement qui gère cette information
  localement, pas le ministère au niveau central. Le taux de succès de
  cette extraction sera probablement plus faible que pour les ambassadeurs.
- **Un fichier `data/consular_sources_seed.json`** a été ajouté avec 2-3
  URLs réelles et vérifiées (via recherche web) — pas 117 comme pour
  `sources.json` : construire une couverture large demanderait le même
  travail de recherche minutieux que pour la liste originale, non fait ici
  faute de budget. Étends ce fichier au même format que `sources.json` au
  fur et à mesure de tes besoins, puis passe-le à `agent.py --sources`.

## Délégations auprès des organisations internationales

`src/org_delegations.py` couvre trois organisations qui publient (à des
degrés très divers de qualité) une liste de leurs représentants permanents,
évitant d'avoir à scraper un site par pays :

| Organisation | Source | Fiabilité |
|---|---|---|
| ONU (New York) — États membres | Dataset officiel CSV (Dag Hammarskjöld Library) | Bonne — structuré et officiel, mais colonnes non vérifiées (voir docstring) |
| ONU (New York) — États non-membres et **OIG/ONG observatrices** (Croix-Rouge, CIO, Union interparlementaire, Ordre de Malte, Union africaine, Ligue arabe, etc.) | Tableaux Wikipedia de la même page que ci-dessus (`--org un_ngo`) | **La plus fiable du fichier** — structure vérifiée par une consultation réelle lors de la préparation du script (contrairement au reste du projet) |
| Union africaine (PRC) | Page officielle `au.int/en/prc` | Partielle — donne le Bureau du PRC (5 personnes), pas les ~55 membres |
| OSCE (Vienne) | Flux de légendes photo `osce.org/node/108218` | Partielle — événements récents de présentation de créances, pas une liste exhaustive des 57 délégations à un instant T |
| Union européenne (Coreper) | — | **Non implémenté**, aucune page centralisée fiable identifiée ; voir la docstring d'`org_delegations.py` pour la piste à suivre (une source par représentation permanente nationale, comme `sources.json`) |

Comme pour tout le reste de ce projet : **jamais testé en conditions
réelles**. Teste organisation par organisation (`--org un`, `--org au`,
`--org osce`) et vérifie les premières sorties avant de faire confiance à
l'ensemble — voir la docstring du script pour le détail complet des
limites propres à chaque organisation.

## Précautions légales et éthiques

- **robots.txt** : l'agent vérifie `robots.txt` avant chaque requête et
  saute la source si l'accès automatisé est explicitement interdit. Ne
  contourne pas cette limite.
- **Conditions d'utilisation** : certains sites de MAE interdisent
  explicitement le moissonnage automatisé dans leurs CGU même quand
  `robots.txt` ne le précise pas. Vérifie au cas par cas pour un usage
  au-delà d'un test personnel.
- **Débit** : un délai de 2 secondes entre requêtes est appliqué par
  défaut (`RATE_LIMIT_SECONDS`). Ne le réduis pas sur des infrastructures
  gouvernementales, souvent peu dimensionnées.
- **Données personnelles** : même publiées publiquement par les
  gouvernements eux-mêmes (c'est précisément l'objet d'une liste
  diplomatique), les noms et titres de personnes physiques restent des
  données personnelles. Bonnes pratiques :
  - conserve la date et l'URL de la source pour chaque entrée (déjà fait
    dans le schéma de sortie) ;
  - ne conserve pas indéfiniment des données devenues obsolètes (un
    diplomate change de poste, la donnée doit être rafraîchie ou expirée) ;
  - prévois un moyen de retirer une entrée si une ambassade le demande ;
  - si tu comptes publier ce registre à des tiers (pas seulement un usage
    interne), et si tu es dans l'UE, ce traitement relève du RGPD — la base
    légale la plus pertinente est généralement l'intérêt légitime pour des
    données déjà rendues publiques par un gouvernement pour ces fins
    précises (protocole diplomatique), mais ce n'est pas un blanc-seing :
    informe-toi sur les obligations de minimisation et d'information des
    personnes concernées.

## Prochaines étapes possibles

- Ajouter l'OCR (`pytesseract` + `pdf2image`) pour les PDF scannés en image.
- Ajouter Playwright pour les pages qui chargent leur contenu en JavaScript.
- Écrire des parseurs dédiés pour les pays à fort enjeu pour ton usage,
  plutôt que de viser une couverture à 100 sites d'un coup.
- Ajouter une tâche planifiée (cron) pour relancer l'agent et
  `wikidata_source.py` périodiquement et détecter les changements (nouveau
  diplomate, départ, nouvelle date trouvée sur Wikidata, etc.).
- Élargir la requête Wikidata aux autres noms de pays possibles (ex.
  variantes anglaises/officielles) si un pays ne renvoie aucun résultat
  sous le libellé actuel de `sources.json`.
