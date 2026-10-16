"""The live-operations store (DECISIONS D-38): one SQLite file shared by the dispatcher, drivers and the public page.

Documents are JSON bodies kept per (collection, id) with a version number; every write also appends a row to an
audit table that is never updated or deleted. Python's stdlib sqlite3 only, so there is nothing to install.

Not used on Vercel: there only /tmp is writable, per instance and not persistent, so two requests could see two
different incidents and a resident's alert link could vanish. open_store() refuses instead (503 at the API) unless
FLOODNET_OPS_ALLOW_EPHEMERAL=1 is set for a throw-away preview.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


class OpsUnavailable(RuntimeError):
    """The live-operations store cannot be used here (-> HTTP 503 with this message)."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  collection TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (collection, id));
CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, collection TEXT NOT NULL, id TEXT NOT NULL,
  version INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seqs (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS audit_doc ON audit (collection, id, seq);
"""


class OpsStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10.0)
        self._db.execute("PRAGMA journal_mode=WAL" if self.path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self._db.execute("PRAGMA secure_delete=ON")     # erased personal data is overwritten, not left in free pages
        self._db.executescript(_SCHEMA)

    # ------------------------------------------------------------------ transactions
    def transaction(self):
        """`with store.transaction():` groups several writes; nested use is allowed (one outer BEGIN)."""
        store = self

        class _Tx:
            def __enter__(self):
                store._lock.acquire()
                store._depth = getattr(store, "_depth", 0) + 1
                if store._depth == 1:
                    try:
                        store._db.execute("BEGIN IMMEDIATE")
                    except BaseException:           # e.g. "database is locked": undo, or every later call hangs (QA M2)
                        store._depth -= 1
                        store._lock.release()
                        raise
                return store

            def __exit__(self, exc_type, *_):
                try:
                    if store._depth == 1:
                        store._db.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store._depth -= 1
                    store._lock.release()
                return False
        return _Tx()

    # ------------------------------------------------------------------ documents
    @staticmethod
    def _load(row) -> dict:
        body = json.loads(row[0])
        body.update(_version=row[1], _created_at=row[2], _updated_at=row[3])
        return body

    def get(self, collection: str, doc_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT body, version, created_at, updated_at FROM docs WHERE collection=? AND id=?",
                                   (collection, doc_id)).fetchone()
        return self._load(row) if row else None

    def list(self, collection: str, where: Optional[Callable[[dict], bool]] = None) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT body, version, created_at, updated_at, id FROM docs WHERE collection=? "
                                    "ORDER BY created_at, rowid", (collection,)).fetchall()
        docs = [self._load(r) for r in rows]
        return [d for d in docs if where is None or where(d)]

    def put(self, collection: str, doc_id: str, body: dict, *, actor: str, action: str,
            expected_version: Optional[int] = None) -> dict:
        """Insert or replace one document and append the audit row, atomically. `expected_version` refuses a write
        based on an older read (ValueError) so two writers cannot silently overwrite each other."""
        clean = {k: v for k, v in body.items() if not k.startswith("_")}
        text = json.dumps(clean, sort_keys=True, default=str)
        now = iso(utc_now())
        with self.transaction():
            row = self._db.execute("SELECT version, created_at FROM docs WHERE collection=? AND id=?",
                                   (collection, doc_id)).fetchone()
            current = row[0] if row else 0
            if expected_version is not None and expected_version != current:
                raise ValueError(f"{collection}/{doc_id}: expected version {expected_version}, store has {current}")
            version = current + 1
            created = row[1] if row else now
            self._db.execute("INSERT OR REPLACE INTO docs (collection, id, version, body, created_at, updated_at) "
                             "VALUES (?,?,?,?,?,?)", (collection, doc_id, version, text, created, now))
            self._db.execute("INSERT INTO audit (at, collection, id, version, actor, action, body) VALUES (?,?,?,?,?,?,?)",
                             (now, collection, doc_id, version, actor, action, text))
        return {**clean, "_version": version, "_created_at": created, "_updated_at": now}

    def insert_unique(self, collection: str, doc_id: str, body: dict, *, actor: str, action: str) -> tuple[dict, bool]:
        """Idempotent insert: if (collection, id) exists, return it unchanged and False."""
        with self.transaction():
            existing = self.get(collection, doc_id)
            if existing is not None:
                return existing, False
            return self.put(collection, doc_id, body, actor=actor, action=action), True

    def audit(self, collection: Optional[str] = None, doc_id: Optional[str] = None, limit: int = 500) -> list[dict]:
        q, args = "SELECT seq, at, collection, id, version, actor, action, body FROM audit", []
        cond = []
        if collection:
            cond.append("collection=?"); args.append(collection)
        if doc_id:
            cond.append("id=?"); args.append(doc_id)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY seq LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [{"seq": r[0], "at": r[1], "collection": r[2], "id": r[3], "version": r[4], "actor": r[5],
                 "action": r[6], "body": json.loads(r[7])} for r in rows]

    def next_seq(self, name: str) -> int:
        with self.transaction():
            row = self._db.execute("SELECT value FROM seqs WHERE name=?", (name,)).fetchone()
            value = (row[0] if row else 0) + 1
            self._db.execute("INSERT OR REPLACE INTO seqs (name, value) VALUES (?, ?)", (name, value))
        return value

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _on_vercel() -> bool:
    return os.environ.get("VERCEL") == "1"


def open_store(path: str | Path) -> OpsStore:
    if _on_vercel() and os.environ.get("FLOODNET_OPS_ALLOW_EPHEMERAL") != "1":
        raise OpsUnavailable("live operations need persistent shared storage; this deployment only has a per-instance "
                             "temporary disk, so trips, alerts and public pages are disabled here (DECISIONS D-38)")
    return OpsStore(path)


_store: Optional[OpsStore] = None
_store_path: Optional[str] = None
_store_lock = threading.Lock()


def default_path() -> Path:
    from .. import config
    return Path(os.environ.get("FLOODNET_OPS_DB") or (config.STATE_DIR / "ops.sqlite3"))


def get_store() -> OpsStore:
    """The process-wide store at default_path(); re-opened if FLOODNET_OPS_DB changes (tests)."""
    global _store, _store_path
    path = str(default_path())
    with _store_lock:
        if _store is None or _store_path != path:
            _store, _store_path = open_store(path), path
        return _store


def reset_store_for_tests() -> None:
    global _store, _store_path
    with _store_lock:
        if _store is not None:
            _store.close()
        _store, _store_path = None, None


def jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))
