"""
llm.py — Classification des triggers par Claude, avec repli sur les mots-clés
================================================================================

Remplace le classement par mots-clés (triggers.classify) par un vrai appel à
l'API Claude quand une clé est disponible (variable d'env ANTHROPIC_API_KEY) :
UN SEUL appel groupé pour toute la liste d'actus de la semaine, pas un par
entrée -- moins cher, plus rapide, et le modèle voit le contexte de toutes
les actus à la fois plutôt que d'en juger une isolément.

Si la clé est absente, l'appel échoue (réseau, quota) ou la réponse est
malformée, on retombe automatiquement sur triggers.classify (mots-clés)
plutôt que de faire planter tout le run -- jamais d'erreur bloquante ici.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .triggers import KEYWORDS
from .triggers import classify as _keyword_classify

TRIGGER_TYPES = list(KEYWORDS.keys())

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"  # rapide/économique, largement suffisant pour de la classification
REQUEST_TIMEOUT_SECONDS = 60


def _build_prompt(news_items: list[dict]) -> str:
    numbered = "\n".join(
        f"{i}. {n.get('headline', '')}" for i, n in enumerate(news_items)
    )
    types_list = "\n".join(f"- {t}" for t in TRIGGER_TYPES)
    return (
        "Tu classes des actus business pour repérer des signaux qu'une "
        "entreprise pourrait avoir besoin de services de relocation/déménagement "
        "international (nouveaux bureaux, expansion, fusion-acquisition, "
        "restructuration, etc.). Juge le sens de l'actu, pas seulement la "
        "présence littérale d'un mot-clé.\n\n"
        f"Types de signal autorisés (utilise UNIQUEMENT ces libellés exacts, "
        f"jamais d'autre valeur) :\n{types_list}\n\n"
        "Pour chaque actu numérotée ci-dessous, indique quels types de signal "
        "s'appliquent (0, 1 ou plusieurs à la fois). Une actu sans signal "
        "pertinent reçoit une liste vide.\n\n"
        f"Actus :\n{numbered}\n\n"
        "Réponds UNIQUEMENT avec un objet JSON de la forme "
        '{"0": ["TYPE1", "TYPE2"], "1": [], ...} — une entrée par actu, '
        "index en chaîne de caractères comme clé, aucun texte avant ou après "
        "le JSON, aucun bloc de code markdown."
    )


def _call_claude(prompt: str) -> dict | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    body = json.dumps({
        "model": MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip()
        # Filet de sécurité si le modèle enrobe quand même la réponse dans
        # un bloc markdown malgré la consigne.
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                first_line, rest = text.split("\n", 1)
                text = rest if first_line.strip().lower() in ("json", "") else text
        return json.loads(text)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        KeyError,
        TimeoutError,
        ValueError,
    ) as e:
        print(f"  [llm] Appel Claude échoué ({e}) -- repli sur le classement par mots-clés.")
        return None


def extract_triggers_batch(news_items: list[dict]) -> list[list[dict]]:
    """Comme extract_triggers, mais retourne une liste de listes -- une
    sous-liste de triggers par entrée d'entrée, dans le même ordre. Permet
    d'appeler Claude UNE SEULE FOIS sur les actus de plusieurs entreprises à
    la fois (le pipeline construit un batch combiné), puis de retrouver quels
    triggers appartiennent à quelle entreprise via l'index."""
    if not news_items:
        return []

    classifications: dict | None = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        result = _call_claude(_build_prompt(news_items))
        if isinstance(result, dict):
            classifications = result
            print(f"  [llm] Classification Claude utilisée pour {len(news_items)} actu(s).")
        else:
            print("  [llm] Repli sur le classement par mots-clés (voir avertissement ci-dessus).")
    else:
        print("  [llm] ANTHROPIC_API_KEY absente -- classement par mots-clés.")

    out: list[list[dict]] = []
    for i, n in enumerate(news_items):
        if classifications is not None:
            types = classifications.get(str(i), [])
            # Ignore toute valeur hors de la liste autorisée (hallucination
            # ou libellé légèrement différent) plutôt que de la propager à
            # scoring.py, qui ne connaît que les 15 types exacts.
            types = [t for t in types if t in TRIGGER_TYPES]
        else:
            types = _keyword_classify(n.get("headline", ""))
        out.append([{"type": t, "date": n.get("date"), "source": n.get("source")} for t in types])
    return out


def extract_triggers(news_items: list[dict]) -> list[dict]:
    """Version historique (liste plate, pas groupée par entrée) -- gardée
    pour compatibilité. Le pipeline utilise extract_triggers_batch pour
    pouvoir grouper les appels sur plusieurs entreprises à la fois."""
    return [t for group in extract_triggers_batch(news_items) for t in group]
