# Newsletter -- comment produire une nouvelle edition chaque semaine

Ce n'est PAS automatise comme le fetch des tenders (ca demanderait une cle
API IA payante, ecarte volontairement). C'est une recherche ponctuelle a
relancer chaque semaine dans une conversation Claude normale (claude.ai),
avec la recherche web activee.

## Marche a suivre (5 minutes)

1. Ouvrez une nouvelle conversation sur claude.ai (recherche web activee).
2. Copiez-collez le bloc de prompt ci-dessous tel quel.
3. Claude produit un JSON. Enregistrez-le tel quel dans `site/newsletter.json`
   (remplace le fichier existant).
4. `git add site/newsletter.json`, commit, push -- Vercel republie tout seul,
   comme pour le reste du site.

Pas besoin de moi (Claude Code) pour cette etape -- n'importe qui dans
l'equipe ayant acces a claude.ai peut le faire.

## Le prompt a copier-coller

```
Tu es un analyste qui prepare une newsletter hebdomadaire de veille pour une
entreprise de demenagement international, logistique d'archives, patrimoine
et amenagement hotelier. Fais une recherche web sur les 7 derniers jours et
compile un rapport structure en JSON STRICT (rien d'autre que le JSON, pas de
texte avant/apres, pas de blocs markdown) suivant EXACTEMENT ce schema :

{
  "generated": "AAAA-MM-JJ",
  "edition": "Mois AAAA",
  "newly_signed": [
    {"date_signed": "", "status": "", "deal": "", "parties": "", "type": "",
     "details": "", "source": {"label": "", "url": ""}}
  ],
  "hospitality": {
    "Africa": [ {"project": "", "status": "", "group": "", "summary": "",
                 "contact": "", "source": {"label": "", "url": ""}} ],
    "Middle East": [ ... meme structure ... ],
    "Asia": [ ... meme structure ... ],
    "Europe": [ ... meme structure ... ],
    "Caribbean": [ ... meme structure ... ]
  },
  "investments": [
    {"deal": "", "status": "", "parties": "", "type": "", "amount": "",
     "scope": "", "source": {"label": "", "url": ""}}
  ],
  "arts": [
    {"event": "", "status": "", "organization": "", "people": "",
     "region": "", "summary": "", "source": {"label": "", "url": ""}}
  ]
}

Contenu attendu par section :
1. newly_signed : accords/MOU/contrats de gestion/financements signes dans
   les ~4 dernieres semaines, toutes categories confondues, les plus recents
   d'abord.
2. hospitality : projets de developpement hotelier et de resorts, par
   region (Afrique, Moyen-Orient, Asie, Europe, Caraibes) -- qui est
   derriere le projet (groupe hotelier, developpeur), un resume, et si une
   personne est citee nommement dans un article public, son nom + role +
   organisation.
3. investments : investissements, partenariats et fusions-acquisitions
   cross-border entre Europe / Afrique / Asie / Ameriques -- type
   d'operation (JV, acquisition, usine, financement...), montant si connu,
   objet/portee.
4. arts : projets et expositions d'art majeurs -- organisation, personnes
   cles nommees publiquement, region, resume.

Regles strictes :
- Chaque ligne doit avoir une source reelle et verifiable (url cliquable).
  N'invente jamais une source. Si tu n'es pas sur, n'inclus pas la ligne.
- "contact"/"people" : uniquement des noms de personnes CITEES/CREDITEES
  dans un article public, avec leur role. Jamais d'email, de telephone, ou
  de profil LinkedIn -- tu n'as pas acces a LinkedIn et tu ne dois pas
  essayer de deviner ou reconstituer des coordonnees personnelles.
- "status" : utilise un des libelles suivants tel quel pour que le site les
  colore correctement : "Signed", "MOU", "Under Construction", "Operational",
  "Announced", "Pledge", "Aggregate", "Pipeline", "Confirmed", "Cancelled"
  (tu peux nuancer, ex. "Signed / Pre-Construction", tant que le mot-cle
  principal apparait).
- Vise ~4 lignes par region en hospitality, ~10-15 en newly_signed,
  ~10-15 en investments, ~8-10 en arts. Qualite avant quantite : mieux vaut
  moins de lignes toutes verifiees qu'une liste longue et approximative.
- Reste factuel et neutre, pas de ton promotionnel.
```

## Si le JSON renvoye n'est pas valide

Rare, mais si Claude ajoute du texte autour du JSON ou casse le format,
redemandez simplement : "Renvoie uniquement le JSON, sans aucun texte autour,
sans bloc de code markdown."
