"""Gedeelde, proces-overstijgende state voor PLUSLokaal (SQLite/WAL).

Waarom: met meerdere gunicorn-worker-PROCESSEN leeft in-memory state (print-jobs, W2P-download-jobs,
login-rate-limiting) maar in één worker. Een vervolg-request (statuspolling, annuleren) kan bij een
andere worker landen en ziet die state dan niet → kapot. Deze module bewaart die state in dezelfde
SQLite-database (WAL staat al aan), zodat ELKE worker 'm leest/schrijft.

Ontwerp:
- job_state: één rij per job (JSON-blob). Updates gaan via `json_patch` = ATOMAIRE server-side merge,
  zodat een cancel-vlag vanaf worker B niet verloren gaat als worker A tegelijk voortgang wegschrijft.
  (RFC 7386: een veld op null zetten verwijdert het — precies wat de code met None bedoelde: .get() geeft
  daarna None terug, identiek gedrag.)
- rate_limit: één rij per mislukte login-poging (key, timestamp); tellen/opschonen per venster.

Verbindingen zijn thread-local (SQLite-connecties zijn niet thread-safe) met busy_timeout, zodat
gelijktijdige workers/threads netjes wachten i.p.v. te falen.
"""
import sqlite3
import threading
import time
import json

_db_path = None
_local = threading.local()


def init(db_path):
    """Zet het pad en maak de tabellen aan. Idempotent; roep één keer bij het opstarten aan.
    Gebruikt een WEGWERP-connectie (niet in thread-local) zodat er bij gunicorn `preload_app` geen
    SQLite-connectie over de fork wordt meegenomen — elke worker/thread opent later z'n eigen."""
    global _db_path
    _db_path = db_path
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS job_state (
        job_id  TEXT PRIMARY KEY,
        kind    TEXT,
        data    TEXT,
        updated REAL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS rate_limit (
        rkey TEXT,
        ts   REAL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_rl_key ON rate_limit(rkey)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_js_kind ON job_state(kind)")
    conn.commit()
    conn.close()


def reset():
    """Sluit de thread-local connectie (aanroepen in een gunicorn post_fork-hook, zodat een worker
    nooit een over de fork geërfde SQLite-connectie hergebruikt)."""
    c = getattr(_local, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
        _local.conn = None


def _conn():
    """Thread-local verbinding met WAL + busy_timeout."""
    c = getattr(_local, "conn", None)
    if c is None:
        if _db_path is None:
            raise RuntimeError("sharedstate.init(db_path) is nog niet aangeroepen")
        c = sqlite3.connect(_db_path, timeout=10, check_same_thread=False)
        c.execute("PRAGMA busy_timeout=8000")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
    return c


# ─── JOB-STATE (print-jobs, W2P-jobs) ────────────────────────────────────────
def job_create(job_id, kind, data):
    """Maak/overschrijf een job met een begin-dict."""
    c = _conn()
    c.execute("INSERT OR REPLACE INTO job_state (job_id, kind, data, updated) VALUES (?,?,?,?)",
              (job_id, kind, json.dumps(data or {}), time.time()))
    c.commit()


def job_get(job_id, default=None):
    """Geef de job-dict terug (of `default` als 'ie niet bestaat)."""
    c = _conn()
    row = c.execute("SELECT data FROM job_state WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return default


def job_set(job_id, **kw):
    """ATOMAIRE merge van kw in de job (json_patch). None verwijdert een veld (== .get()→None)."""
    if not kw:
        return
    c = _conn()
    c.execute("UPDATE job_state SET data=json_patch(data, ?), updated=? WHERE job_id=?",
              (json.dumps(kw), time.time(), job_id))
    c.commit()


def job_field(job_id, key, default=None):
    """Lees één veld uit een job zonder de hele dict te hoeven laden."""
    c = _conn()
    row = c.execute("SELECT json_extract(data, '$.' || ?) FROM job_state WHERE job_id=?",
                    (key, job_id)).fetchone()
    if not row or row[0] is None:
        return default
    return row[0]


def job_delete(job_id):
    c = _conn()
    c.execute("DELETE FROM job_state WHERE job_id=?", (job_id,))
    c.commit()


def job_all(kind):
    """Alle (job_id, dict) van een soort."""
    c = _conn()
    out = []
    for jid, data in c.execute("SELECT job_id, data FROM job_state WHERE kind=?", (kind,)).fetchall():
        try:
            out.append((jid, json.loads(data)))
        except Exception:
            pass
    return out


def job_cleanup(kind, max_age, statuses):
    """Verwijder afgeronde jobs (status in `statuses`) ouder dan max_age seconden."""
    c = _conn()
    cutoff = time.time() - max_age
    rows = c.execute("SELECT job_id, data FROM job_state WHERE kind=? AND updated < ?",
                     (kind, cutoff)).fetchall()
    stale = []
    for jid, data in rows:
        try:
            st = json.loads(data).get('status')
        except Exception:
            st = None
        if st in statuses:
            stale.append(jid)
    for jid in stale:
        c.execute("DELETE FROM job_state WHERE job_id=?", (jid,))
    if stale:
        c.commit()


# ─── RATE LIMIT (login) ──────────────────────────────────────────────────────
def rl_record(key, now=None):
    """Registreer een mislukte poging."""
    now = now or time.time()
    c = _conn()
    c.execute("INSERT INTO rate_limit (rkey, ts) VALUES (?,?)", (key, now))
    c.commit()


def rl_active(key, window, now=None):
    """Aantal pogingen binnen `window` + oudste timestamp. Snoeit meteen verlopen rijen voor deze key."""
    now = now or time.time()
    c = _conn()
    c.execute("DELETE FROM rate_limit WHERE rkey=? AND ts < ?", (key, now - window))
    c.commit()
    row = c.execute("SELECT COUNT(*), MIN(ts) FROM rate_limit WHERE rkey=?", (key,)).fetchone()
    return (row[0] or 0), (row[1] or now)


def rl_reset(key):
    c = _conn()
    c.execute("DELETE FROM rate_limit WHERE rkey=?", (key,))
    c.commit()


def rl_gc(window, now=None):
    """Globale opschoning van oude rate-limit-rijen (roep af en toe aan)."""
    now = now or time.time()
    c = _conn()
    c.execute("DELETE FROM rate_limit WHERE ts < ?", (now - window,))
    c.commit()
