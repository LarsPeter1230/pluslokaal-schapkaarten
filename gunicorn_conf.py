# Gunicorn-config voor PLUSLokaal Schapkaarten.
#
# MULTI-WORKER (sinds de gedeelde-state-refactor): meerdere worker-PROCESSEN benutten alle CPU-cores
# voor zowel pagina's serveren als kaarten renderen. De state die vroeger per-proces in-memory stond
# (print-jobs, W2P-download-jobs, login-rate-limiting) staat nu in de GEDEELDE SQLite-store (sharedstate),
# zodat statuspolling/annuleren/voortgang over ELKE worker heen werken.
#
# preload_app=True → de app wordt één keer in de MASTER geïmporteerd; daardoor draait de opstart-branch
# (migraties, seeds, sharedstate.init én de nachtelijke W2P-scheduler) precies ÉÉN keer i.p.v. per worker
# (anders zouden N workers N gelijktijdige nachtelijke downloads starten). De post_fork-hook geeft elke
# worker daarna VERSE database- en SQLite-connecties (een over de fork geërfde connectie is niet veilig).
#
# Terugrollen naar de dev-server: zet in de systemd-unit ExecStart terug op
#   /usr/bin/python3 /root/pluslokaal/app.py   (backup: pluslokaal.service.bak)

import multiprocessing

_cores = multiprocessing.cpu_count()

bind = "0.0.0.0:5000"
# Renderen is CPU-werk → ~1 worker per core, met wat marge voor master/scheduler/OS. Threads erbovenop
# voor I/O-gelijktijdigheid (IPP-printer, DB, portaal-proxy) binnen een worker.
workers = max(2, min(8, _cores - 2))
worker_class = "gthread"
threads = 8
worker_connections = 1000

preload_app = True               # opstart-branch (scheduler!) één keer in de master; zie boven

# Renders/downloads mogen onder piekbelasting even duren → niet vroegtijdig afkappen.
timeout = 180
graceful_timeout = 30
keepalive = 5

# GEEN max_requests: worker-recycling is niet nodig (state staat gedeeld) en zou onnodig herstarten.

proc_name = "pluslokaal"
accesslog = None                 # geen per-request access-log (zoals de dev-server); errors -> stderr
errorlog = "-"
loglevel = "info"


def post_fork(server, worker):
    """Na het forken: elke worker eigen DB- en SQLite-connecties (geërfde connecties zijn niet fork-safe)."""
    try:
        import sharedstate
        sharedstate.reset()
    except Exception:
        pass
    try:
        from app import db
        db.engine.dispose()
    except Exception:
        pass


def when_ready(server):
    """Draait één keer in de MASTER nadat de workers geforkt zijn (dus fork-veilig): start de gedeelde
    plus.nl-zoekservice met één warme browser. De workers bevragen 'm via 127.0.0.1 → geen koude
    ~20s Cloudflare-start meer per worker, en maar één browser i.p.v. één per worker."""
    try:
        import plus_search
        plus_search.start_service_in_thread()
    except Exception as e:
        server.log.error(f"plus.nl-zoekservice niet gestart: {e}")
