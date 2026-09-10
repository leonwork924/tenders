from dataclasses import dataclass
from pathlib import Path
import yaml

@dataclass
class ICPConfig:
    raw: dict
    scorer_version: str
    @classmethod
    def load(cls,path):
        raw=yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if sum(x["points"] for x in raw["headcount_bands"]) < 0:
            raise ValueError("Invalid headcount configuration")
        if max(x["points"] for x in raw["headcount_bands"]) > 7:
            raise ValueError("Headcount points exceed 7")
        if max(x["points"] for x in raw["contact_roles"]) > 12:
            raise ValueError("Contact role points exceed 12")
        return cls(raw,raw["scorer_version"])
