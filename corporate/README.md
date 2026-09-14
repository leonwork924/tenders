# Corporate contacts — recherche ciblée

## Ce qui a changé (11/09/2026)

L'ancienne version lançait `agent_corporate_contacts_v8.py` **sans argument**,
ce qui déclenche son mode "discovery global" : téléchargement et parsing de
l'intégralité du GLEIF Golden Copy (plusieurs millions d'entités légales dans
le monde), avec un timeout de 5h configuré sur le workflow. Résultat après
plusieurs runs : `site/corporate_contacts.json` à 0 entreprise — soit le run
n'a jamais fini dans le temps imparti, soit il a échoué silencieusement.

Ce n'est de toute façon pas ce dont Tender Radar a besoin : l'objectif n'est
pas de dupliquer GLEIF en entier, mais d'avoir une fiche contact quand une
entreprise précise devient pertinente (ex. un nouveau tender remporté, un
nouveau sponsor IND, une entreprise mentionnée dans la newsletter).

**Nouveau fonctionnement** : le workflow `corporate-contacts.yml` prend
maintenant un nom d'entreprise en entrée (`workflow_dispatch`, à lancer
depuis l'onglet Actions) et exécute uniquement le pipeline ciblé
(`run_pipeline` dans `agent_corporate_contacts_v8.py`) :

1. Recherche dans le registre national approprié (ou OpenCorporates en repli)
2. Résolution du LEI via GLEIF
3. Si un domaine est fourni ET que `HUNTER_API_KEY` est configuré en secret
   GitHub : enrichissement de contacts via Hunter.io

Chaque recherche s'ajoute à `corporate/data/corporate_contacts.db` (commité,
comme `data/tenders.sqlite3` pour les tenders), puis est exportée vers
`site/corporate_contacts.json` pour l'onglet "Entreprises" de la page
Contact.

## Bug corrigé au passage

`run_pipeline` faisait un `INSERT INTO companies VALUES (...)` positionnel
avec seulement 14 valeurs, alors que la table en a 18 (4 colonnes ajoutées
par `init_v5_schema`, appelée avant l'insert). Ça faisait planter **tout**
run ciblé sur une base neuve avec `sqlite3.OperationalError: table companies
has 18 columns but 14 values were supplied`. Passé à un INSERT avec liste de
colonnes explicite — robuste peu importe l'évolution du schéma.

## Fichiers retirés

`corporate/src/export_by_country.py` et `corporate/src/export_stats.py`
étaient construits pour le mode global (pagination par pays, stats sur des
millions de lignes) et n'ont plus d'usage. Supprime-les si tu appliques ce
patch à la main :

```bash
rm corporate/src/export_by_country.py corporate/src/export_stats.py
```

## Pour relancer le mode global plus tard

Le mode `discover_global_zero_arg()` existe toujours dans
`agent_corporate_contacts_v8.py` (appelable sans argument). Si l'envie
revient, il vaut mieux le lancer en local (pas dans Actions) pour surveiller
la progression en direct, avec un `--discovery-limit` d'abord pour valider
que ça tourne avant de lancer sur le fichier complet.
