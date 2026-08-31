"""Source registry. Add a new adapter by putting it in REGISTRY."""

from .base import Source, SourceError
from .koneps import KonepsSource
from .kenya_ppip import KenyaPpipSource
from .boamp import BoampSource
from .generic import CsvInboxSource, RssSource, VendorApiSource
from .ocds import UkFtsSource, ZaEtendersSource
from .ted import TedSource
from .ungm import UngmSource
from .world_bank import WorldBankSource
from .ecuador import EcuadorOcdsSource
from .international import AusTenderSource, SecopColombiaSource, AnacItalySource, ProzorroSource
from .open_sources import OcpRegistrySource, MtenderSource, TanzaniaNestLiveSource

SOURCE_ALIASES = {
    "paraguay_dncp": "paraguay_dncp",
    "uruguay_arce_api": "uruguay_arce",
    "chile_compra": "chile_mercadopublico",
    "tanzania_ppra_ocds": "tanzania_ppra",
    "zambia_zppa_ocds": "zambia_zppa",
    "kosovo_pprc_ocds": "kosovo_pprc",
    "south_africa_nt_ocds": "za_etenders",
}


REGISTRY = {
    "austender": AusTenderSource,
    "secop_colombia": SecopColombiaSource,
    "anac_italy": AnacItalySource,
    "prozorro": ProzorroSource,
    "ocp_registry": OcpRegistrySource,
    "mtender": MtenderSource,
    "tanzania_nest_live": TanzaniaNestLiveSource,
    "koneps": KonepsSource,
    "kenya_ppip": KenyaPpipSource,
    "boamp": BoampSource,
    "ted": TedSource,
    "ecuador_ocds": EcuadorOcdsSource,

    "ungm": UngmSource,
    "world_bank": WorldBankSource,
    "ocds_fts": UkFtsSource,
    "ocds_za": ZaEtendersSource,
    "rss": RssSource,
    "csv_inbox": CsvInboxSource,
    "vendor_api": VendorApiSource,
}


def build_sources(config: dict, only_source: str | None = None) -> list[Source]:
    run_config = config.get("run", {})
    wanted = SOURCE_ALIASES.get(only_source, only_source)
    built = []
    for name, settings in (config.get("sources") or {}).items():
        if not settings or not settings.get("enabled"):
            continue
        if wanted and name != wanted:
            continue
        kind = settings.get("type")
        cls = REGISTRY.get(kind)
        if cls is None:
            raise SourceError(f"source '{name}' has unknown type '{kind}'")
        built.append(cls(name, settings, run_config))
    return built


__all__ = ["Source", "SourceError", "REGISTRY", "build_sources"]
