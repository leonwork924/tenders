"""Local SQLite store. Nothing leaves this file except what you export."""

from __future__ import annotations

import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Iterable, Optional

from .models import Tender

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
    uid           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    buyer         TEXT,
    country       TEXT,
    description   TEXT,
    cpv           TEXT,
    url           TEXT,
    published     TEXT,
    deadline      TEXT,
    value         REAL,
    currency      TEXT,
    language      TEXT,
    raw_ref       TEXT,
    score         REAL,
    matched       TEXT,
    fingerprint   TEXT,
    duplicate_of  TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'new'   -- new | seen | shortlisted | ignored
);
CREATE INDEX IF NOT EXISTS idx_tenders_fingerprint ON tenders(fingerprint);
CREATE INDEX IF NOT EXISTS idx_tenders_score ON tenders(score DESC);
CREATE INDEX IF NOT EXISTS idx_tenders_deadline ON tenders(deadline);
CREATE INDEX IF NOT EXISTS idx_tenders_status ON tenders(status);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    fetched    INTEGER DEFAULT 0,
    inserted   INTEGER DEFAULT 0,
    duplicates INTEGER DEFAULT 0,
    errors     TEXT
);

CREATE TABLE IF NOT EXISTS source_state (
    source     TEXT PRIMARY KEY,
    last_run   TEXT,
    last_ok    TEXT,
    last_error TEXT
);
"""


def _iso(d) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, (date, datetime)):
        return d.isoformat()
    return str(d)


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Add columns that didn't exist in older committed databases.
        CREATE TABLE IF NOT EXISTS in SCHEMA only helps on a brand new file --
        this covers everyone already running the committed data/tenders.sqlite3.
        """
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(tenders)")}
        if "contract_end" not in cols:
            self.conn.execute("ALTER TABLE tenders ADD COLUMN contract_end TEXT")

    def close(self):
        self.conn.close()

    # -- writes ------------------------------------------------------------
    def upsert(self, tender: Tender) -> bool:
        """Insert a tender. Returns True if this is the first time we see it."""
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute("SELECT uid FROM tenders WHERE uid = ?", (tender.uid(),))
        exists = cur.fetchone() is not None

        if exists:
            self.conn.execute(
                """UPDATE tenders SET last_seen = ?, score = ?, matched = ?,
                       deadline = ?, value = ?, duplicate_of = ?, contract_end = ?
                   WHERE uid = ?""",
                (now, tender.score, tender.matched, _iso(tender.deadline),
                 tender.value, tender.duplicate_of, _iso(tender.contract_end), tender.uid()),
            )
            return False

        self.conn.execute(
            """INSERT INTO tenders (uid, source, source_id, title, buyer, country,
                   description, cpv, url, published, deadline, contract_end, value, currency,
                   language, raw_ref, score, matched, fingerprint, duplicate_of,
                   first_seen, last_seen, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new')""",
            (tender.uid(), tender.source, tender.source_id, tender.title, tender.buyer,
             tender.country, tender.description[:8000], tender.cpv, tender.url,
             _iso(tender.published), _iso(tender.deadline), _iso(tender.contract_end),
             tender.value, tender.currency,
             tender.language, tender.raw_ref, tender.score, tender.matched,
             tender.fingerprint, tender.duplicate_of, now, now),
        )
        return True

    def commit(self):
        self.conn.commit()

    def mark_seen(self, uids: Iterable[str]):
        self.conn.executemany(
            "UPDATE tenders SET status = 'seen' WHERE uid = ? AND status = 'new'",
            [(u,) for u in uids],
        )
        self.conn.commit()

    def set_status(self, uid: str, status: str):
        self.conn.execute("UPDATE tenders SET status = ? WHERE uid = ?", (status, uid))
        self.conn.commit()

    def start_run(self) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at) VALUES (?)",
            (datetime.now().isoformat(timespec="seconds"),),
        )
        self.conn.commit()
        return cur.lastrowid

    def end_run(self, run_id: int, fetched: int, inserted: int, duplicates: int, errors: str):
        self.conn.execute(
            "UPDATE runs SET ended_at=?, fetched=?, inserted=?, duplicates=?, errors=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), fetched, inserted,
             duplicates, errors, run_id),
        )
        self.conn.commit()

    def record_source(self, source: str, ok: bool, error: str = ""):
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO source_state (source, last_run, last_ok, last_error)
               VALUES (?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET
                   last_run = excluded.last_run,
                   last_ok = COALESCE(excluded.last_ok, source_state.last_ok),
                   last_error = excluded.last_error""",
            (source, now, now if ok else None, error[:500]),
        )
        self.conn.commit()

    # -- reads -------------------------------------------------------------
    def fingerprints(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT uid, fingerprint, title, buyer FROM tenders WHERE duplicate_of IS NULL"
        ).fetchall()

    def shortlist(self, min_score: float, only_new: bool = True,
                  limit: int = 200, min_days: int = 0) -> list[sqlite3.Row]:
        sql = ["SELECT * FROM tenders WHERE score >= ? AND duplicate_of IS NULL"]
        args: list = [min_score]
        if only_new:
            sql.append("AND status = 'new'")
        if min_days:
            sql.append("AND (deadline IS NULL OR deadline >= date('now', ?))")
            args.append(f"+{int(min_days)} day")
        sql.append("ORDER BY score DESC, deadline IS NULL, deadline ASC")
        if limit:
            sql.append("LIMIT ?")
            args.append(limit)
        return self.conn.execute(" ".join(sql), args).fetchall()

    def expired(self, min_score: float, limit: int = 2000) -> list[sqlite3.Row]:
        """Tenders whose bid deadline has passed -- the Historique tab on the
        site. Needs an actual deadline to qualify (a NULL deadline can't be
        judged expired or not, so it's excluded here just like it's always
        treated as 'still open' in shortlist()).
        """
        sql = ("SELECT * FROM tenders WHERE score >= ? AND duplicate_of IS NULL "
               "AND deadline IS NOT NULL AND deadline < date('now') "
               "ORDER BY deadline DESC LIMIT ?")
        return self.conn.execute(sql, (min_score, limit)).fetchall()

    def contract_renewals(self, min_score: float, within_days: int = 180,
                           limit: int = 500) -> list[sqlite3.Row]:
        """Expired tenders whose stated contract end date falls within the
        next `within_days` -- these are worth watching, since the buyer will
        likely re-tender around then. Only populated for tenders where the
        source actually stated a contract period (currently OCDS-based
        sources, and TED on a best-effort basis) -- see contract_end in
        models.py for the honest caveat on coverage.
        """
        sql = ("SELECT * FROM tenders WHERE score >= ? AND duplicate_of IS NULL "
               "AND contract_end IS NOT NULL "
               "AND contract_end BETWEEN date('now') AND date('now', ?) "
               "ORDER BY contract_end ASC LIMIT ?")
        return self.conn.execute(sql, (min_score, f"+{int(within_days)} day", limit)).fetchall()

    def since(self, min_score: float, since_iso: str, min_days: int = 0,
               limit: int = 5000) -> list[sqlite3.Row]:
        """Tenders first seen on/after `since_iso` (a date or datetime string),
        for the weekly digest -- independent of the new/seen status used by
        the local CLI, so it isn't affected by `fetch --mark-seen`.
        """
        sql = ["SELECT * FROM tenders WHERE score >= ? AND duplicate_of IS NULL",
               "AND first_seen >= ?"]
        args: list = [min_score, since_iso]
        if min_days:
            sql.append("AND (deadline IS NULL OR deadline >= date('now', ?))")
            args.append(f"+{int(min_days)} day")
        sql.append("ORDER BY score DESC, deadline IS NULL, deadline ASC")
        if limit:
            sql.append("LIMIT ?")
            args.append(limit)
        return self.conn.execute(" ".join(sql), args).fetchall()

    def source_health(self, min_score: float, min_days: int = 0) -> list[dict]:
        """Per-source status for the site's health panel: last run outcome
        (from source_state) plus how many currently-active tenders that
        source is contributing to the shortlist right now.
        """
        c = self.conn
        sql = ["SELECT source, COUNT(*) n FROM tenders",
               "WHERE score >= ? AND duplicate_of IS NULL"]
        args: list = [min_score]
        if min_days:
            sql.append("AND (deadline IS NULL OR deadline >= date('now', ?))")
            args.append(f"+{int(min_days)} day")
        sql.append("GROUP BY source")
        counts = {r["source"]: r["n"] for r in c.execute(" ".join(sql), args).fetchall()}

        out = []
        for r in c.execute("SELECT * FROM source_state ORDER BY source").fetchall():
            d = dict(r)
            d["active_count"] = counts.get(d["source"], 0)
            d["ok"] = bool(d["last_run"]) and d["last_ok"] == d["last_run"]
            out.append(d)
        return out

    def stats(self) -> dict:
        c = self.conn
        row = c.execute(
            """SELECT COUNT(*) total,
                      SUM(status='new') new,
                      SUM(duplicate_of IS NOT NULL) dupes
               FROM tenders"""
        ).fetchone()
        last = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "total": row["total"] or 0,
            "new": row["new"] or 0,
            "duplicates": row["dupes"] or 0,
            "last_run": dict(last) if last else None,
            "sources": [dict(r) for r in c.execute("SELECT * FROM source_state").fetchall()],
        }
