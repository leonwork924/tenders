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
        self.conn.commit()

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
                       deadline = ?, value = ?, duplicate_of = ?
                   WHERE uid = ?""",
                (now, tender.score, tender.matched, _iso(tender.deadline),
                 tender.value, tender.duplicate_of, tender.uid()),
            )
            return False

        self.conn.execute(
            """INSERT INTO tenders (uid, source, source_id, title, buyer, country,
                   description, cpv, url, published, deadline, value, currency,
                   language, raw_ref, score, matched, fingerprint, duplicate_of,
                   first_seen, last_seen, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new')""",
            (tender.uid(), tender.source, tender.source_id, tender.title, tender.buyer,
             tender.country, tender.description[:8000], tender.cpv, tender.url,
             _iso(tender.published), _iso(tender.deadline), tender.value, tender.currency,
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
