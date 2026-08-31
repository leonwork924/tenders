"""Base class for every source adapter."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    pass


class Source:
    #: value used in Tender.source and in config keys
    name = "base"

    def __init__(self, name: str, settings: dict, run_config: dict):
        self.name = name
        self.settings = settings or {}
        self.run = run_config or {}
        self.delay = float(self.run.get("request_delay", 1.0))
        self.timeout = int(self.settings.get("request_timeout", self.run.get("request_timeout", 45)))
        self.ssl_verify = self.run.get("ssl_verify", True)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.run.get("user_agent", "tender-radar/1.0"),
            "Accept": "application/json, text/xml;q=0.9, */*;q=0.8",
        })
        self._last_call = 0.0

    # -- helpers -----------------------------------------------------------
    def _wait(self):
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_call = time.monotonic()

    def get(self, url: str, **kwargs):
        self._wait()
        log.debug("GET %s %s", url, kwargs.get("params", ""))
        r = self.session.get(url, timeout=self.timeout, verify=self.ssl_verify, **kwargs)
        r.raise_for_status()
        return r

    def post(self, url: str, **kwargs):
        self._wait()
        log.debug("POST %s", url)
        r = self.session.post(url, timeout=self.timeout, verify=self.ssl_verify, **kwargs)
        if r.status_code >= 400:
            raise SourceError(f"{self.name}: HTTP {r.status_code} - {r.text[:300]}")
        return r

    def since(self) -> date:
        return date.today() - timedelta(days=int(self.run.get("lookback_days", 3)))

    # -- interface ---------------------------------------------------------
    def fetch(self) -> list:
        """Return a list of Tender objects published since self.since()."""
        raise NotImplementedError
