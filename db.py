"""Assignment + settings store.

Two backends with identical behaviour:
  * PostgresStore - used when DATABASE_URL is set (env var or Streamlit secret). Required for the cloud
    deployment: Streamlit Community Cloud containers are ephemeral, so a local SQLite file is wiped on
    every restart/redeploy.
  * SqliteStore   - local development and tests (path from MATCHER_DB, default matcher.db).

Invariants enforced by the database itself, not just the UI:
  * a candidate holds at most one assignment  -> candidate_id is the PRIMARY KEY
  * a demand is never over-filled             -> count check + insert inside one serialised transaction
                                                 (BEGIN IMMEDIATE on SQLite, per-demand advisory lock on Postgres)
"""
import json
import os
import sqlite3
import threading

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS assignments (
           candidate_id TEXT PRIMARY KEY,
           demand_id    TEXT NOT NULL,
           assigned_at  TEXT NOT NULL)""",
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
]


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteStore:
    backend = "sqlite"
    durable = False   # durable on a laptop, NOT on Streamlit Cloud

    def __init__(self, path="matcher.db"):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.lock = threading.Lock()
        for stmt in SCHEMA:
            self.conn.execute(stmt)

    def assignments(self):
        with self.lock:
            return dict(self.conn.execute("SELECT candidate_id, demand_id FROM assignments ORDER BY assigned_at, candidate_id"))

    def assignment_rows(self):
        with self.lock:
            return self.conn.execute(
                "SELECT candidate_id, demand_id, assigned_at FROM assignments ORDER BY assigned_at, candidate_id").fetchall()

    def filled(self, demand_id):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM assignments WHERE demand_id=?", (demand_id,)).fetchone()[0]

    def assign(self, candidate_id, demand_id, headcount):
        """Atomic: refuses double-booking and over-filling. Returns (ok, message)."""
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                row = cur.execute("SELECT demand_id FROM assignments WHERE candidate_id=?", (candidate_id,)).fetchone()
                if row:
                    cur.execute("ROLLBACK")
                    return False, f"{candidate_id} is already assigned to {row[0]}"
                n = cur.execute("SELECT COUNT(*) FROM assignments WHERE demand_id=?", (demand_id,)).fetchone()[0]
                if n >= headcount:
                    cur.execute("ROLLBACK")
                    return False, f"{demand_id} is already full ({n}/{headcount})"
                cur.execute("INSERT INTO assignments VALUES (?, ?, ?)", (candidate_id, demand_id, _now()))
                cur.execute("COMMIT")
                return True, f"{candidate_id} assigned to {demand_id}"
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def unassign(self, candidate_id):
        with self.lock:
            return self.conn.execute("DELETE FROM assignments WHERE candidate_id=?", (candidate_id,)).rowcount > 0

    def get(self, key, default=None):
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))

    def delete(self, key):
        with self.lock:
            self.conn.execute("DELETE FROM settings WHERE key=?", (key,))


class PostgresStore:
    backend = "postgres"
    durable = True

    def __init__(self, url):
        import psycopg  # imported lazily so local/SQLite use needs no Postgres driver
        self._psycopg = psycopg
        self.url = url
        self.lock = threading.Lock()
        self.conn = None
        self._run(lambda cur: [cur.execute(s) for s in SCHEMA])

    def _connect(self):
        if self.conn is None or self.conn.closed:
            self.conn = self._psycopg.connect(self.url, autocommit=True, connect_timeout=10)
        return self.conn

    def _run(self, fn):
        """Run fn(cursor) in one transaction; reconnect once if a hosted DB dropped an idle connection."""
        with self.lock:
            for attempt in (1, 2):
                try:
                    conn = self._connect()
                    with conn.transaction(), conn.cursor() as cur:
                        return fn(cur)
                except self._psycopg.OperationalError:
                    if self.conn is not None:
                        self.conn.close()
                    self.conn = None
                    if attempt == 2:
                        raise

    def assignments(self):
        return self._run(lambda cur: dict(cur.execute(
            "SELECT candidate_id, demand_id FROM assignments ORDER BY assigned_at, candidate_id").fetchall()))

    def assignment_rows(self):
        return self._run(lambda cur: cur.execute(
            "SELECT candidate_id, demand_id, assigned_at FROM assignments ORDER BY assigned_at, candidate_id").fetchall())

    def filled(self, demand_id):
        return self._run(lambda cur: cur.execute(
            "SELECT COUNT(*) FROM assignments WHERE demand_id=%s", (demand_id,)).fetchone()[0])

    def assign(self, candidate_id, demand_id, headcount):
        def tx(cur):
            # serialise all assignments to this demand (count + insert must be atomic across users)
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("demand:" + demand_id,))
            row = cur.execute("SELECT demand_id FROM assignments WHERE candidate_id=%s", (candidate_id,)).fetchone()
            if row:
                return False, f"{candidate_id} is already assigned to {row[0]}"
            n = cur.execute("SELECT COUNT(*) FROM assignments WHERE demand_id=%s", (demand_id,)).fetchone()[0]
            if n >= headcount:
                return False, f"{demand_id} is already full ({n}/{headcount})"
            # ON CONFLICT covers a concurrent assignment of the same candidate to a *different* demand
            ins = cur.execute("INSERT INTO assignments VALUES (%s, %s, %s) ON CONFLICT (candidate_id) DO NOTHING",
                              (candidate_id, demand_id, _now()))
            if ins.rowcount == 0:
                return False, f"{candidate_id} was just assigned elsewhere"
            return True, f"{candidate_id} assigned to {demand_id}"
        return self._run(tx)

    def unassign(self, candidate_id):
        return self._run(lambda cur: cur.execute(
            "DELETE FROM assignments WHERE candidate_id=%s", (candidate_id,)).rowcount > 0)

    def get(self, key, default=None):
        row = self._run(lambda cur: cur.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone())
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self._run(lambda cur: cur.execute(
            "INSERT INTO settings VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, json.dumps(value))))

    def delete(self, key):
        self._run(lambda cur: cur.execute("DELETE FROM settings WHERE key=%s", (key,)))

    def clear_all(self):
        """Test helper."""
        self._run(lambda cur: [cur.execute("DELETE FROM assignments"), cur.execute("DELETE FROM settings")])


def Store(target=None):
    """Factory: a postgres:// URL -> PostgresStore, anything else -> SqliteStore(path)."""
    target = target or os.environ.get("DATABASE_URL") or os.environ.get("MATCHER_DB", "matcher.db")
    if str(target).startswith(("postgres://", "postgresql://")):
        return PostgresStore(target)
    return SqliteStore(target)
