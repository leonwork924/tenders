"""
country_prepositions.py — "au/en/aux/à + pays" pour la lettre type
====================================================================

Nécessaire pour des phrases comme "Présent au Kenya" ou "Présent en
Thaïlande" -- l'accord préposition/pays en français a de vraies exceptions
(ex. le Mexique, le Mozambique, le Cambodge sont masculins malgré leur "e"
final ; Cuba, Chypre, Israël n'ont pas d'article). Cette table couvre les
pays où je suis raisonnablement confiant, construite à partir de règles
grammaticales connues + une liste d'exceptions classiques -- PAS générée
automatiquement, PAS vérifiée contre une liste externe faisant autorité.

Pour tout pays absent de cette table : ne pas deviner. Le générateur de
lettre (letter_generator.py) doit alors soit sauter la phrase concernée,
soit utiliser secours plus prudent -- voir son code.

Si tu repères une erreur ou un pays manquant qui revient souvent dans les
nominations JORF, ajoute-le ici après vérification (ex. Wiktionnaire donne
le genre de chaque pays de façon fiable).
"""

from __future__ import annotations

# clé = valeur telle qu'elle apparaît dans le tag ambassadeur_pays de JORF
# valeur = (préposition, nom_tel_quel_apres_preposition)
# Le nom après la préposition est parfois légèrement différent de la clé
# (ex. accord), mais dans l'immense majorité des cas identique -- laissé
# explicite plutôt que reconstruit, pour rester lisible et vérifiable.
COUNTRY_PREPOSITIONS: dict[str, tuple[str, str]] = {
    # --- "au" (masculin singulier) ---
    "Kenya": ("au", "Kenya"),
    "Japon": ("au", "Japon"),
    "Canada": ("au", "Canada"),
    "Maroc": ("au", "Maroc"),
    "Sénégal": ("au", "Sénégal"),
    "Mali": ("au", "Mali"),
    "Niger": ("au", "Niger"),
    "Tchad": ("au", "Tchad"),
    "Cameroun": ("au", "Cameroun"),
    "Congo": ("au", "Congo"),
    "Burundi": ("au", "Burundi"),
    "Rwanda": ("au", "Rwanda"),
    "Ghana": ("au", "Ghana"),
    "Nigeria": ("au", "Nigeria"),
    "Bénin": ("au", "Bénin"),
    "Botswana": ("au", "Botswana"),
    "Zimbabwe": ("au", "Zimbabwe"),  # exception -e -> masculin
    "Mozambique": ("au", "Mozambique"),  # exception -e -> masculin
    "Mexique": ("au", "Mexique"),  # exception -e -> masculin
    "Cambodge": ("au", "Cambodge"),  # exception -e -> masculin
    "Portugal": ("au", "Portugal"),
    "Danemark": ("au", "Danemark"),
    "Luxembourg": ("au", "Luxembourg"),
    "Liban": ("au", "Liban"),
    "Qatar": ("au", "Qatar"),
    "Koweït": ("au", "Koweït"),
    "Brésil": ("au", "Brésil"),
    "Chili": ("au", "Chili"),
    "Pérou": ("au", "Pérou"),
    "Guatemala": ("au", "Guatemala"),
    "Honduras": ("au", "Honduras"),
    "Vietnam": ("au", "Vietnam"),
    "Cambodge": ("au", "Cambodge"),
    "Kazakhstan": ("au", "Kazakhstan"),
    "Turkménistan": ("au", "Turkménistan"),
    "Ouzbékistan": ("au", "Ouzbékistan"),
    "Tadjikistan": ("au", "Tadjikistan"),
    "Kirghizistan": ("au", "Kirghizistan"),
    "Yémen": ("au", "Yémen"),
    "Bangladesh": ("au", "Bangladesh"),
    "Sri Lanka": ("au", "Sri Lanka"),
    "Népal": ("au", "Népal"),
    "Costa Rica": ("au", "Costa Rica"),
    "Panama": ("au", "Panama"),
    "Zimbabwe": ("au", "Zimbabwe"),
    "Nigéria": ("au", "Nigéria"),
    "Togo": ("au", "Togo"),
    "Guyana": ("au", "Guyana"),
    "Suriname": ("au", "Suriname"),
    "Belize": ("au", "Belize"),
    "Malawi": ("au", "Malawi"),
    "Lesotho": ("au", "Lesotho"),
    "Sultanat d'Oman": ("au", "Sultanat d'Oman"),
    "Oman": ("au", "Oman"),
    "Pakistan": ("au", "Pakistan"),
    "Monténégro": ("au", "Monténégro"),

    # --- "en" (féminin singulier -- la grande majorité des pays en -e) ---
    "France": ("en", "France"),
    "Thaïlande": ("en", "Thaïlande"),
    "Bosnie-Herzégovine": ("en", "Bosnie-Herzégovine"),
    "Norvège": ("en", "Norvège"),
    "Allemagne": ("en", "Allemagne"),
    "Italie": ("en", "Italie"),
    "Espagne": ("en", "Espagne"),
    "Belgique": ("en", "Belgique"),
    "Suisse": ("en", "Suisse"),
    "Autriche": ("en", "Autriche"),
    "Grèce": ("en", "Grèce"),
    "Pologne": ("en", "Pologne"),
    "Hongrie": ("en", "Hongrie"),
    "Roumanie": ("en", "Roumanie"),
    "Bulgarie": ("en", "Bulgarie"),
    "Croatie": ("en", "Croatie"),
    "Serbie": ("en", "Serbie"),
    "Slovénie": ("en", "Slovénie"),
    "Slovaquie": ("en", "Slovaquie"),
    "Finlande": ("en", "Finlande"),
    "Suède": ("en", "Suède"),
    "Irlande": ("en", "Irlande"),
    "Estonie": ("en", "Estonie"),
    "Lettonie": ("en", "Lettonie"),
    "Lituanie": ("en", "Lituanie"),
    "Ukraine": ("en", "Ukraine"),
    "Moldavie": ("en", "Moldavie"),
    "Turquie": ("en", "Turquie"),
    "Russie": ("en", "Russie"),
    "Chine": ("en", "Chine"),
    "Corée du Sud": ("en", "Corée du Sud"),
    "Mongolie": ("en", "Mongolie"),
    "Inde": ("en", "Inde"),
    "Indonésie": ("en", "Indonésie"),
    "Malaisie": ("en", "Malaisie"),
    "Birmanie": ("en", "Birmanie"),
    "Mauritanie": ("en", "Mauritanie"),
    "Algérie": ("en", "Algérie"),
    "Tunisie": ("en", "Tunisie"),
    "Libye": ("en", "Libye"),
    "Égypte": ("en", "Égypte"),
    "Éthiopie": ("en", "Éthiopie"),
    "Namibie": ("en", "Namibie"),
    "Tanzanie": ("en", "Tanzanie"),
    "Zambie": ("en", "Zambie"),
    "Ouganda": ("en", "Ouganda"),  # exception -a mais usage "en"
    "Somalie": ("en", "Somalie"),
    "Colombie": ("en", "Colombie"),
    "Bolivie": ("en", "Bolivie"),
    "Argentine": ("en", "Argentine"),
    "Australie": ("en", "Australie"),
    "Nouvelle-Zélande": ("en", "Nouvelle-Zélande"),
    "Jamaïque": ("en", "Jamaïque"),
    "Irak": ("en", "Irak"),
    "Iran": ("en", "Iran"),  # masculin en usage courant mais "en Iran" reste correct
    "Jordanie": ("en", "Jordanie"),
    "Arménie": ("en", "Arménie"),
    "Azerbaïdjan": ("en", "Azerbaïdjan"),  # -an, pas -e, mais usage "en" par tradition
    "Géorgie": ("en", "Géorgie"),
    "Albanie": ("en", "Albanie"),
    "Macédoine": ("en", "Macédoine"),
    "Guinée": ("en", "Guinée"),
    "Guinée-Bissau": ("en", "Guinée-Bissau"),
    "Angola": ("en", "Angola"),  # exception -a, usage "en"
    "Gambie": ("en", "Gambie"),
    "Sierra Leone": ("en", "Sierra Leone"),

    # --- "aux" (pluriel) ---
    "États-Unis": ("aux", "États-Unis"),
    "Etats-Unis": ("aux", "Etats-Unis"),
    "Émirats arabes unis": ("aux", "Émirats arabes unis"),
    "Pays-Bas": ("aux", "Pays-Bas"),
    "Philippines": ("aux", "Philippines"),
    "Comores": ("aux", "Comores"),
    "Seychelles": ("aux", "Seychelles"),
    "Maldives": ("aux", "Maldives"),
    "Bahamas": ("aux", "Bahamas"),
    "Fidji": ("aux", "Fidji"),

    # --- "à" (sans article) ---
    "Cuba": ("à", "Cuba"),
    "Chypre": ("à", "Chypre"),
    "Malte": ("à", "Malte"),
    "Monaco": ("à", "Monaco"),
    "Singapour": ("à", "Singapour"),
    "Bahreïn": ("à", "Bahreïn"),
    "Djibouti": ("à", "Djibouti"),
    "Madagascar": ("à", "Madagascar"),
    "Israël": ("à", "Israël"),
    "Haïti": ("à", "Haïti"),
}
