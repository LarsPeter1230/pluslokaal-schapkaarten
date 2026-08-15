"""Client voor het oude W2P-systeem (pluslokaal.nl/W2P) om kant-en-klare
weekpakket-schapkaarten op te halen.

Werkt net als ``plus_search.py``: één warme Playwright/Chromium-worker in een
achtergrond-thread met een queue. De worker logt éénmalig in en houdt de sessie
warm, zodat volgende acties snel zijn. Alle publieke functies zijn thread-safe
(ze zetten een opdracht op de queue en wachten op het resultaat).

Credentials komen uit de DB-``Setting`` (``w2p_user``, ``w2p_pass``, ``w2p_base``)
en staan NERGENS in code of logs.

Publieke API:
  - list_categories() -> [{'category_id','label'}]
  - list_periods(category_id=7) -> [{'period_id','label'}]
  - list_groups(period_id, category_id=7) -> [{'group_id','label'}]
  - crawl(period_id, category_id, group_id) -> [{'promotion_document_id','formaat','naam','group_label'}]
  - thumbnail(promotion_document_id) -> PNG-bytes (of {'error':...})
  - order_and_download(doc_ids, period_id=None, category_id=7, targets=None, job_id=None) -> {formaat: pdf_bytes} (of {'error':...})
  - get_progress(job_id) -> {'percent','message'} (of None) - voortgang van een lopende order_and_download
  - clear_progress(job_id) -> ruimt de voortgangs-entry op

Bij fouten geven de functies een dict ``{'error': '...'}`` terug i.p.v. te crashen.
"""
import threading
import queue
import re
import urllib.parse
import urllib.request
import concurrent.futures as cf

_q = queue.Queue()
_started = False
_lock = threading.Lock()

# Voortgang van lopende order_and_download-jobs (job_id -> {'percent','message'}),
# zodat de Flask-kant (andere thread) kan pollen hoever een lange download-taak is.
_progress = {}
_progress_lock = threading.Lock()

def get_progress(job_id):
    with _progress_lock:
        p = _progress.get(job_id)
        return dict(p) if p else None

def _set_progress(job_id, percent, message, **stats):
    if not job_id:
        return
    with _progress_lock:
        _progress[job_id] = {'percent': percent, 'message': message, 'stats': stats}

def clear_progress(job_id):
    with _progress_lock:
        _progress.pop(job_id, None)

def set_progress(job_id, percent, message, **stats):
    """Publieke versie van _set_progress, voor gebruik door app.py buiten de Playwright-worker om
    (bv. tijdens het samenstellen van een download uit de lokale PDF-cache). ``stats`` zijn losse
    extra kengetallen (bv. docs_found=123, pdfs_cached=45) die de UI erbij kan tonen."""
    _set_progress(job_id, percent, message, **stats)

_NAV_TIMEOUT = 45000
_DEFAULT_BASE = "https://pluslokaal.nl"


def _get_accounts():
    """Lees alle geconfigureerde W2P-accounts uit de DB-Setting. Account 2 (w2p_user2/w2p_pass2) is
    optioneel; is die gezet, dan kunnen workers over twee accounts verdeeld worden zodat parallelle
    downloads gegarandeerd gescheiden sessies/winkelmandjes hebben. Geeft ([(user,pw),...], base)."""
    import app as m
    with m.app.app_context():
        base = m.get_setting("w2p_base") or _DEFAULT_BASE
        accounts = []
        for i in range(1, getattr(m, "W2P_MAX_ACCOUNTS", 6) + 1):
            uk = "w2p_user" if i == 1 else f"w2p_user{i}"
            pk = "w2p_pass" if i == 1 else f"w2p_pass{i}"
            u, p = m.get_setting(uk), m.get_setting(pk)
            if u and p:
                accounts.append((u, m._w2p_pass_plain(p)))   # wachtwoord ontsleutelen (of legacy plaintext)
    return accounts, (base or _DEFAULT_BASE).rstrip("/")

def account_count():
    """Aantal geconfigureerde W2P-accounts (bepaalt hoeveel workers echt onafhankelijk kunnen werken)."""
    accounts, _ = _get_accounts()
    return max(1, len(accounts))


# --- extractie-scripts (draaien in de pagina) --------------------------------

# Alle sidebar-links met een PeriodID maar zonder PromotionGroupID = periodes.
_PERIOD_LINKS = r"""() => document.querySelectorAll('a[href*="PeriodID"]') &&
  [...document.querySelectorAll('a[href*="PeriodID"]')]
    .filter(a => !/PromotionGroupID/i.test(a.getAttribute('href') || ''))
    .map(a => ({href: a.getAttribute('href') || '', label: (a.innerText || '').trim()}))"""

# Top-niveau sidebar-links met alleen een CategoryID (geen PeriodID/PromotionGroupID) = categorieën.
_CATEGORY_LINKS = r"""() =>
  [...document.querySelectorAll('a[href*="CategoryID"]')]
    .filter(a => !/PeriodID/i.test(a.getAttribute('href') || '') && !/PromotionGroupID/i.test(a.getAttribute('href') || ''))
    .map(a => ({href: a.getAttribute('href') || '', label: (a.innerText || '').trim()}))"""

# Alle sidebar-links met een PromotionGroupID = afdelingen (groepen).
_GROUP_LINKS = r"""() =>
  [...document.querySelectorAll('a[href*="PromotionGroupID"]')]
    .map(a => ({href: a.getAttribute('href') || '', label: (a.innerText || '').trim()}))"""

# Kaart-tegels op een groep-pagina.
_TILES = r"""() =>
  [...document.querySelectorAll('div.panel.panel-default')].map(e => {
    const t = e.querySelector('h3.panel-title');
    const cb = e.querySelector('input.template-checkbox');
    return {
      title: t ? (t.innerText || '').trim() : '',
      docid: cb ? (cb.getAttribute('name') || '') : ''
    };
  }).filter(o => o.docid)"""

# Download-knoppen op de Thankyou/Downloadpagina: id="groupdl-<formaat>".
_GROUP_DL = r"""() =>
  [...document.querySelectorAll("a[id^='groupdl-']")].map(a => ({
    formaat: (a.id || '').replace(/^groupdl-/, ''),
    href: a.getAttribute('href') || ''
  }))"""

# Vink in één keer (in de pagina) alle gevraagde checkboxes aan i.p.v. 1-voor-1 via Playwright -
# bij grote afdelingen (honderden kaarten) is dat het verschil tussen ~3 minuten en enkele seconden.
_BULK_CHECK = r"""(names) => {
  let n = 0;
  for (const name of names) {
    const el = document.querySelector('input.template-checkbox[name="' + name + '"]');
    if (el && !el.checked) { el.checked = true; el.dispatchEvent(new Event('change', {bubbles:true})); n++; }
  }
  return n;
}"""


def _parse_id(href, key):
    m = re.search(key + r"=(\d+)", href or "")
    return m.group(1) if m else None


def _parse_title(title):
    """'A3 liggend - 4439-7-F21-Gato Negro-' -> ('A3 liggend', '4439-7-F21-Gato Negro')."""
    formaat, naam = "", title or ""
    if " - " in (title or ""):
        formaat, rest = title.split(" - ", 1)
        formaat = formaat.strip()
        naam = rest.strip().strip("-").strip()
    return formaat, naam


def _worker(worker_index=0):
    from playwright.sync_api import sync_playwright

    accounts, base = _get_accounts()
    if not accounts:
        return
    user, pw = accounts[worker_index % len(accounts)]

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(locale="nl-NL", viewport={"width": 1366, "height": 1000})
        page = ctx.new_page()
        # Bevestig alle dialogs automatisch (o.a. "winkelmandje leegmaken"-confirm).
        page.on("dialog", lambda d: d.accept())
        state = {"logged_in": False}

        # -- sessie -----------------------------------------------------------
        def do_login():
            page.goto(base + "/login.aspx", wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
            page.fill("#username", user)
            page.fill("#password", pw)
            page.press("#password", "Enter")  # knop-klik submit is niet betrouwbaar
            page.wait_for_load_state("domcontentloaded", timeout=_NAV_TIMEOUT)
            page.wait_for_timeout(2000)
            state["logged_in"] = "login.aspx" not in (page.url or "").lower()
            return state["logged_in"]

        def ensure_login():
            if not state["logged_in"]:
                do_login()

        def nav(url):
            """Navigeer; log opnieuw in als we op de login-pagina belanden."""
            page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
            if "login.aspx" in (page.url or "").lower():
                do_login()
                page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
            page.wait_for_timeout(1200)

        # -- URL-helpers ------------------------------------------------------
        def tree_url(category_id, period_id=None, group_id=None):
            u = base + "/W2P/RetailDocuments.aspx?"
            parts = []
            if period_id:
                parts.append("PeriodID=%s" % period_id)
            parts.append("CategoryID=%s" % category_id)
            if group_id:
                parts.append("PromotionGroupID=%s" % group_id)
            return u + "&".join(parts)

        # -- operaties --------------------------------------------------------
        def op_list_categories():
            ensure_login()
            nav(base + "/W2P/RetailDocuments.aspx")
            rows = page.evaluate(_CATEGORY_LINKS) or []
            out, seen = [], set()
            for r in rows:
                cid = _parse_id(r["href"], "CategoryID")
                if cid and cid not in seen:
                    seen.add(cid)
                    out.append({"category_id": cid, "label": r["label"]})
            return out

        def op_list_periods(category_id=7):
            ensure_login()
            nav(tree_url(category_id))
            rows = page.evaluate(_PERIOD_LINKS) or []
            out, seen = [], set()
            for r in rows:
                pid = _parse_id(r["href"], "PeriodID")
                if pid and pid not in seen:
                    seen.add(pid)
                    out.append({"period_id": pid, "label": r["label"]})
            return out

        def op_list_groups(period_id, category_id=7):
            ensure_login()
            nav(tree_url(category_id, period_id=period_id))
            rows = page.evaluate(_GROUP_LINKS) or []
            out, seen = [], set()
            for r in rows:
                gid = _parse_id(r["href"], "PromotionGroupID")
                if gid and gid not in seen:
                    seen.add(gid)
                    out.append({"group_id": gid, "label": r["label"]})
            return out

        def _group_label_for(group_id):
            rows = page.evaluate(_GROUP_LINKS) or []
            for r in rows:
                if _parse_id(r["href"], "PromotionGroupID") == str(group_id):
                    return r["label"]
            return ""

        def op_crawl(period_id, category_id, group_id):
            ensure_login()
            nav(tree_url(category_id, period_id=period_id, group_id=group_id))
            group_label = _group_label_for(group_id)
            tiles = page.evaluate(_TILES) or []
            out = []
            for t in tiles:
                formaat, naam = _parse_title(t["title"])
                out.append({
                    "promotion_document_id": t["docid"],
                    "formaat": formaat,
                    "naam": naam,
                    "group_label": group_label,
                })
            return out

        def op_thumbnail(promotion_document_id):
            ensure_login()
            u = base + "/W2P/GetPromotionDocumentThumb.ashx?PromotionDocumentID=%s" % promotion_document_id
            r = page.request.get(u, timeout=_NAV_TIMEOUT)
            if r.status != 200:
                return {"error": "thumbnail HTTP %s" % r.status}
            data = r.body()
            if not data or not data.startswith(b"\x89PNG"):
                # Waarschijnlijk uitgelogd -> opnieuw en 1x proberen.
                state["logged_in"] = False
                ensure_login()
                r = page.request.get(u, timeout=_NAV_TIMEOUT)
                data = r.body() if r.status == 200 else b""
            if not data or not data.startswith(b"\x89PNG"):
                return {"error": "geen PNG ontvangen voor document %s" % promotion_document_id}
            return data

        def op_order_and_download(doc_ids, period_id=None, category_id=7, targets=None, job_id=None):
            _set_progress(job_id, 2, "Inloggen op printsysteem…")
            ensure_login()
            wanted = [str(d) for d in doc_ids]
            if not wanted:
                return {"error": "geen documenten opgegeven"}
            remaining = set(wanted)

            # 1) winkelmandje leegmaken zodat de order alleen het gevraagde bevat.
            _set_progress(job_id, 5, "Winkelmandje voorbereiden…")
            nav(base + "/W2P/Basket.aspx")
            clr = page.query_selector("#ClearCart")
            if clr:
                try:
                    clr.click(timeout=8000)
                    page.wait_for_load_state("domcontentloaded", timeout=_NAV_TIMEOUT)
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

            # 2) documenten aanvinken per groep-pagina (checkbox bestaat alleen daar).
            #    Als we al weten in welke (categorie,periode,groep) elk document zit (uit onze DB-cache),
            #    bezoeken we alleen die pagina's rechtstreeks - geen blinde zoektocht door alle periodes/groepen.
            if targets:
                pages_needed = {}  # (category_id, period_id, group_id) -> set(doc_ids)
                for d in wanted:
                    t = targets.get(d)
                    if not t:
                        continue
                    key = (t.get("category_id", category_id), t["period_id"], t["group_id"])
                    pages_needed.setdefault(key, set()).add(d)
                total_pages = len(pages_needed) or 1
                done_pages = 0
                for (cid, pid, gid), docs in pages_needed.items():
                    if not remaining:
                        break
                    docs = docs & remaining
                    if not docs:
                        continue
                    nav(tree_url(cid, period_id=pid, group_id=gid))
                    present = [d for d in docs if page.query_selector("input.template-checkbox[name='%s']" % d)]
                    if present:
                        page.evaluate(_BULK_CHECK, present)   # in één keer aanvinken (snel bij grote afdelingen)
                        page.click("#btSubmit", timeout=90000)
                        page.wait_for_load_state("domcontentloaded", timeout=90000)
                        page.wait_for_timeout(1500)
                        remaining.difference_update(present)
                    done_pages += 1
                    _set_progress(job_id, 8 + int(50 * done_pages / total_pages),
                                  "Kaarten aanvinken (%d/%d afdelingen)…" % (done_pages, total_pages))

            # Terugval: documenten zonder bekende locatie (of geen targets meegegeven) blind zoeken.
            if remaining:
                periods = [str(period_id)] if period_id else [p["period_id"] for p in op_list_periods(category_id)]
                for pid in periods:
                    if not remaining:
                        break
                    for g in op_list_groups(pid, category_id):
                        if not remaining:
                            break
                        _set_progress(job_id, 55, "Kaarten zoeken in %s…" % (g.get("label") or pid))
                        nav(tree_url(category_id, period_id=pid, group_id=g["group_id"]))
                        present = [d for d in list(remaining)
                                   if page.query_selector("input.template-checkbox[name='%s']" % d)]
                        if not present:
                            continue
                        page.evaluate(_BULK_CHECK, present)
                        page.click("#btSubmit", timeout=90000)
                        page.wait_for_load_state("domcontentloaded", timeout=90000)
                        page.wait_for_timeout(1500)
                        remaining.difference_update(present)

            # Niet-gevonden documenten (verdwenen van pluslokaal.nl). Zijn ÁLLE gevraagde documenten weg,
            # dan valt er niets te bestellen → foutmelding. Zijn er nog wél gevonden, dan gaan we door met
            # die (de gevonden kaarten blijven werken) en geven we de verdwenen IDs terug in '_not_found'.
            not_found = sorted(remaining)
            if len(remaining) >= len(wanted):
                return {"error": "documenten niet gevonden: %s" % ", ".join(not_found)}

            # 3) bestellen: Basket -> "Ga naar downloadscherm" -> Thankyou.aspx?W2P_OrderID=...
            _set_progress(job_id, 62, "Bestelling plaatsen…")
            nav(base + "/W2P/Basket.aspx")
            btn = page.query_selector("#btDownload")
            if not btn:
                return {"error": "winkelmandje leeg of downloadknop ontbreekt"}
            btn.click(timeout=90000)
            page.wait_for_load_state("domcontentloaded", timeout=90000)
            page.wait_for_timeout(2500)
            if "W2P_OrderID" not in (page.url or ""):
                return {"error": "order niet aangemaakt (geen W2P_OrderID)"}

            # 4) per formaatgroep de gecombineerde PDF ophalen - PARALLEL (dit zijn onafhankelijke
            #    downloads van vaak grote bestanden (10+ MB); na elkaar was dit de grootste
            #    tijdvreter). We gebruiken de sessie-cookies buiten Playwright om (urllib, thread-
            #    pool) i.p.v. page.request, die noodgedwongen sequentieel is.
            _set_progress(job_id, 68, "PDF's ophalen…")
            groups = page.evaluate(_GROUP_DL) or []
            if not groups:
                return {"error": "geen download-knoppen op de downloadpagina"}

            def _resolve_href(href):
                href = href or ""
                if href.startswith("./"):
                    url = base + "/W2P/" + href[2:]
                elif href.startswith("/"):
                    url = base + href
                elif not href.startswith("http"):
                    url = base + "/W2P/" + href
                else:
                    url = href
                # De site levert onge-encodede spaties in de querystring (bv. "Group=A3 liggend")
                # - page.request (Playwright) accepteert dat, urllib.request niet.
                return urllib.parse.quote(url, safe=":/?&=")

            cookie_header = "; ".join("%s=%s" % (c["name"], c["value"]) for c in ctx.cookies())
            done_count = [0]

            def _fetch_one(g):
                url = _resolve_href(g["href"])
                req = urllib.request.Request(url, headers={"Cookie": cookie_header,
                                                             "User-Agent": "Mozilla/5.0"})
                # Het oude systeem geeft onder gelijktijdige belasting soms een tijdelijke HTTP 500 -
                # een paar keer opnieuw proberen (met korte backoff) haalt die alsnog binnen.
                data, status, err = b"", 0, None
                for attempt in range(3):
                    try:
                        with urllib.request.urlopen(req, timeout=240) as resp:
                            data = resp.read()
                            status = resp.status
                    except Exception as e:
                        err = str(e)[:200]
                        status = 0
                    if status == 200 and data.startswith(b"%PDF"):
                        err = None
                        break
                    import time as _t
                    _t.sleep(1.5 * (attempt + 1))
                if status == 200 and data.startswith(b"%PDF"):
                    result = (g["formaat"], data)
                else:
                    result = (g["formaat"], {"error": err or ("geen PDF (HTTP %s)" % status)})
                done_count[0] += 1
                _set_progress(job_id, 70 + int(29 * done_count[0] / (len(groups) or 1)),
                              "PDF ophalen: %s (%d/%d)…" % (g["formaat"], done_count[0], len(groups)))
                return result

            out = {}
            try:
                with cf.ThreadPoolExecutor(max_workers=4) as ex:
                    for formaat, result in ex.map(_fetch_one, groups):
                        out[formaat] = result
            except Exception:
                # Terugval: sequentieel via Playwright zelf (zoals voorheen), robuuster maar trager.
                out = {}
                for i, g in enumerate(groups):
                    r = page.request.get(_resolve_href(g["href"]), timeout=_NAV_TIMEOUT)
                    data = r.body() if r.status == 200 else b""
                    out[g["formaat"]] = data if data.startswith(b"%PDF") else {"error": "geen PDF (HTTP %s)" % r.status}
                    _set_progress(job_id, 70 + int(29 * (i + 1) / (len(groups) or 1)),
                                  "PDF ophalen: %s (%d/%d)…" % (g["formaat"], i + 1, len(groups)))
            if not_found:
                out["_not_found"] = not_found      # verdwenen kaarten → caller markeert ze onbeschikbaar
            _set_progress(job_id, 100, "Klaar")
            return out

        _ops = {
            "list_categories": op_list_categories,
            "list_periods": op_list_periods,
            "list_groups": op_list_groups,
            "crawl": op_crawl,
            "thumbnail": op_thumbnail,
            "order_and_download": op_order_and_download,
        }

        # -- queue-lus met 1x herproberen na sessie-/actiefout ----------------
        while True:
            item = _q.get()
            if item is None:
                break
            name, kwargs, holder = item
            fn = _ops.get(name)
            try:
                holder["result"] = fn(**kwargs)
            except Exception:
                # verlopen sessie / hapering: opnieuw inloggen en 1x herproberen.
                try:
                    state["logged_in"] = False
                    ensure_login()
                    holder["result"] = fn(**kwargs)
                except Exception as e2:
                    holder["result"] = {"error": str(e2)[:300]}
            holder["event"].set()


# Aantal parallelle browser-workers. Elke worker heeft z'n eigen Chromium-context (eigen W2P-login,
# eigen sessie en dus eigen winkelmandje), zodat meerdere afdelingen tegelijk besteld+gedownload
# kunnen worden zonder dat hun mandjes elkaar vervuilen. Instelbaar via env (standaard 1, zodat
# gewoon bladeren/zoeken licht blijft; de PDF-sync schaalt dit tijdelijk op - zie set_pool_size()).
import os as _os
_NUM_WORKERS = max(1, int(_os.environ.get("W2P_WORKERS", "1")))
_workers = []

def ensure_started():
    """Zorg dat er minstens één worker draait."""
    set_pool_size(_NUM_WORKERS)

def set_pool_size(n):
    """Schaal het aantal parallelle browser-workers op naar (minstens) ``n``. Workers worden alleen
    bijgestart, nooit gestopt (ze blijven warm voor volgende acties). Thread-safe.

    HARD gemaximeerd op het aantal W2P-accounts: één account deelt zijn winkelmandje over al zijn
    sessies, dus twee workers op hetzelfde account zouden bij gelijktijdig bestellen elkaars mandje
    vervuilen. Met max. één worker per account kan dat niet gebeuren."""
    global _started
    n = max(1, min(int(n), account_count()))
    with _lock:
        while len(_workers) < n:
            idx = len(_workers)
            t = threading.Thread(target=_worker, args=(idx,), daemon=True)
            t.start()
            _workers.append(t)
        _started = True

def pool_size():
    return len(_workers)

def reset_pool():
    """Stop alle draaiende workers (bv. nadat de W2P-accounts/wachtwoorden in Beheer zijn gewijzigd).
    De volgende bewerking start verse workers die de nieuwe inloggegevens inlezen."""
    global _workers
    with _lock:
        old = _workers
        _workers = []
    for _ in old:
        _q.put(None)   # elke worker leest één sentinel en stopt (Chromium sluit netjes)


def _call(op, kwargs, timeout):
    ensure_started()
    holder = {"event": threading.Event(), "result": None}
    _q.put((op, kwargs, holder))
    if holder["event"].wait(timeout):
        return holder["result"]
    return {"error": "W2P-bewerking duurde te lang (probeer opnieuw)."}


# --- publieke API ------------------------------------------------------------

def list_categories(timeout=60):
    """Geef de top-niveau categorieën uit de sidebar: [{'category_id','label'}]."""
    return _call("list_categories", {}, timeout)


def list_periods(category_id=7, timeout=60):
    """Geef de weekpakket-periodes uit de sidebar: [{'period_id','label'}]."""
    return _call("list_periods", {"category_id": category_id}, timeout)


def list_groups(period_id, category_id=7, timeout=60):
    """Geef de afdelingen (groepen) van een periode: [{'group_id','label'}]."""
    return _call("list_groups", {"period_id": period_id, "category_id": category_id}, timeout)


def crawl(period_id, category_id, group_id, timeout=90):
    """Geef alle kaart-tegels van een groep-pagina:
    [{'promotion_document_id','formaat','naam','group_label'}]."""
    return _call("crawl", {"period_id": period_id, "category_id": category_id, "group_id": group_id}, timeout)


def thumbnail(promotion_document_id, timeout=45):
    """Geef de thumbnail-PNG-bytes van een document (of {'error':...})."""
    return _call("thumbnail", {"promotion_document_id": promotion_document_id}, timeout)


def order_and_download(doc_ids, period_id=None, category_id=7, targets=None, job_id=None, timeout=300):
    """Vink de documenten aan, doorloop de bestel-flow en haal per formaat de
    gecombineerde print-PDF op. Geeft {formaat: pdf_bytes} terug (of {'error':...}).

    LET OP: dit maakt een echte order aan op het oude W2P-systeem.

    ``targets`` (optioneel, aanbevolen): dict ``{doc_id_str: {'period_id','group_id','category_id'}}``
    uit onze eigen DB-cache - dan wordt elke (categorie,periode,groep)-pagina maar één keer
    rechtstreeks bezocht i.p.v. blind alle periodes/groepen te doorzoeken (veel sneller,
    en werkt ook als de gekozen kaarten uit meerdere periodes/weken tegelijk komen).

    ``period_id`` is een terugval-optie voor documenten zonder bekende locatie in ``targets``
    (of als ``targets`` niet is meegegeven): geef het mee om alleen die periode te doorzoeken;
    zonder ``period_id`` worden alle periodes doorzocht tot alle documenten gevonden zijn.

    ``job_id`` (optioneel): als gezet, wordt de voortgang tijdens het verwerken bijgehouden en
    op te vragen via ``get_progress(job_id)`` (percentage + statusbericht).
    """
    return _call("order_and_download",
                 {"doc_ids": doc_ids, "period_id": period_id, "category_id": category_id,
                  "targets": targets, "job_id": job_id},
                 timeout)
