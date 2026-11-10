"""build_branches.py -- Construit alerts/branches_by_country.json à partir du CSV des branches.

NOM + marque seulement, JAMAIS d'e-mail ni de téléphone : le repo est public.
Les adresses restent dans ton CSV, sur ton poste.

Usage :  python alerts/build_branches.py "C:\\chemin\\Mobilitas_BranchesALL.csv"
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pycountry

# Jamais alertées (décision de Leon, 10/11/2026).
EXCLUDE_NAME_WORDS = ("santa fe", "sanelo")

ALIASES = {  # noms du CSV que pycountry ne connaît pas
    "Cape Verde": "CV", "China (People's Republic)": "CN", "Ivory Coast": "CI", "Macau": "MO",
    "Slovakia (Slovak Republic)": "SK", "South Korea, Republic of": "KR", "Swaziland": "SZ",
}


def alpha2(name: str) -> str:
    if name in ALIASES:
        return ALIASES[name]
    try:
        return pycountry.countries.lookup(name).alpha_2
    except LookupError:
        try:
            return pycountry.countries.search_fuzzy(name)[0].alpha_2
        except LookupError:
            return ""


def read_csv(path: str) -> list[dict]:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return list(csv.DictReader(raw.decode(enc).splitlines()))
        except UnicodeDecodeError:
            continue
    raise SystemExit("Encodage du CSV non reconnu")


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else ""
    if not src:
        raise SystemExit(__doc__)
    out: dict[str, list[dict]] = {}
    skipped = []
    for r in read_csv(src):
        name, country = (r.get("Branch Name") or "").strip(), (r.get("Country") or "").strip()
        if not name or not country:
            continue
        if any(w in name.lower() for w in EXCLUDE_NAME_WORDS):
            continue
        a2 = alpha2(country)
        if not a2:
            skipped.append(country)
            continue
        out.setdefault(a2, []).append({"branch": name, "code": (r.get("Branch Code") or "").strip(),
                                       "network": (r.get("Network") or "").strip(), "country": country})
    payload = {"note": "Noms de branches uniquement (pas d'e-mails : repo public). Santa Fe / Sanelo exclues.",
               "countries": out}
    dst = Path(__file__).with_name("branches_by_country.json")
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{sum(len(v) for v in out.values())} branches, {len(out)} pays -> {dst}")
    if skipped:
        print("Pays non reconnus :", sorted(set(skipped)))


if __name__ == "__main__":
    main()
