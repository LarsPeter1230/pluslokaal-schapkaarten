"""Productzoek op plus.nl via een warme headless browser (Playwright).

Architectuur (multi-worker- én concurrency-proof):
- Er draait ÉÉN gedeelde zoek-service met één warme browser, gestart in de gunicorn-MASTER
  (`start_service_in_thread`, aangeroepen vanuit de gunicorn `when_ready`-hook). De browser passeert
  de Cloudflare-check éénmalig; de clearance geldt voor de hele context (alle tabbladen).
- Binnen die ene browser draait een POOL van pagina's (tabbladen) op een asyncio-loop, zodat er
  VEEL zoekopdrachten TEGELIJK kunnen lopen (niet serieel achter elkaar). Zo kunnen ~100 mensen
  tegelijk zoeken zonder minutenlange wachtrij.
- De gunicorn-workers (aparte processen) bevragen de service via een lokale HTTP-call op 127.0.0.1.
  Zo delen alle workers één browser i.p.v. elk een eigen. Valt de service weg (of losse dev-server),
  dan valt `search()` terug op een in-proces browser (zelfde pool-mechanisme).
- Resultaat-cache (per query, 5 min) → veelgezochte producten komen direct terug (geen browserwerk).
"""
import threading
import urllib.parse
import json
import time
import socket

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

SERVICE_PORT = 5100
_CACHE_TTL = 60            # seconden dat een zoekresultaat wordt hergebruikt. Kort → nieuwe artikelen,
                           # vervangen foto's en gewijzigde prijzen zijn binnen een minuut vers; lang
                           # genoeg om een piek gelijktijdige zoekopdrachten voor hetzelfde product op
                           # te vangen. (plus.nl blijft de bron; wij tonen nooit langer dan dit iets ouds.)
_POOL_SIZE = 12            # aantal gelijktijdige zoek-tabbladen in de ene browser

_EXTRACT = r"""() => {
  const seen = new Set(); const out = [];
  const tiles = document.querySelectorAll('a[href*="/product/"]');
  for (const a of tiles) {
    const href = a.getAttribute('href'); if (!href || seen.has(href)) continue; seen.add(href);
    const name = (a.getAttribute('title') || '').trim();
    if (!name) continue;
    const aria = (a.getAttribute('aria-label') || '').trim();
    let unit = aria.startsWith(name) ? aria.slice(name.length).trim() : '';
    let txt = (a.innerText || '').replace(/(\d+)[\s.,]+(\d{2})(?!\d)/g, '$1.$2');
    const prices = [...new Set((txt.match(/\d+\.\d{2}/g) || []))];
    let img = '';
    {
      const imgs = [...a.querySelectorAll('img')];
      const isBadge = (s) => /laagblijvers|badge|keurmerk|nutri|logo/i.test(s || '');
      let best = '', bestArea = -1;
      for (const im of imgs) {
        const s = im.currentSrc || im.src || im.getAttribute('data-src') || '';
        if (!s || isBadge(s)) continue;
        const area = (im.clientWidth || 0) * (im.clientHeight || 0)
                  || (im.naturalWidth || 0) * (im.naturalHeight || 0);
        if (area >= bestArea) { bestArea = area; best = s; }
      }
      if (!best) { for (const im of imgs) { const s = im.src || ''; if (s && !isBadge(s)) { best = s; break; } } }
      if (!best && imgs.length) best = imgs[0].src || '';
      img = best;
    }
    let deal = '';
    {
      const mv = txt.match(/(\d+)\s*voor\s*€?\s*(\d+\.\d{2})/i);
      const mg = txt.match(/(\d+)\s*GRAM\s*(?:VOOR\s*)?€?\s*(\d+\.\d{2})/);
      const mp = txt.match(/(\d+)\s*%\s*korting/i);
      if (mv) deal = mv[1] + ' voor ' + mv[2];
      else if (mg) deal = mg[1] + ' gram voor ' + mg[2];
      else if (mp) deal = mp[1] + '% korting';
    }
    out.push({ href, name, unit, prices, img, deal });
    if (out.length >= 16) break;
  }
  return out;
}"""

# ─── Interne async-engine (één browser, pool van tabbladen) ───────────────────
_loop = None
_started = False
_start_lock = threading.Lock()
_ready = threading.Event()
_pool = None               # asyncio.Queue met warme pagina's
_cf_lock = None            # asyncio.Lock voor het (her)passeren van Cloudflare
_cache = {}                # query -> (timestamp, result-list)
_cache_lock = threading.Lock()
_inflight = {}             # query -> {'event':Event,'result':...}: coalesce gelijktijdige zelfde zoek
_inflight_lock = threading.Lock()


async def _pass_cf(page):
    for _ in range(3):
        try:
            await page.goto("https://www.plus.nl/", wait_until="domcontentloaded", timeout=40000)
        except Exception:
            pass
        await page.wait_for_timeout(5000)
        t = ((await page.title()) or "").strip()
        if t and "moment" not in t.lower():
            return True
    return False


async def _setup():
    global _pool, _cf_lock
    import asyncio
    from playwright.async_api import async_playwright
    p = await async_playwright().start()
    # Afbeeldingen op BROWSER-niveau uitzetten (imagesEnabled=false) → veel lichtere zoekpagina's,
    # dus sneller en veel meer gelijktijdige zoekopdrachten mogelijk. Geen per-request Python-callback
    # (dat was juist trager). De foto-URL's zitten als attribuut in de DOM en blijven dus beschikbaar.
    browser = await p.chromium.launch(args=[
        "--disable-blink-features=AutomationControlled", "--no-sandbox",
        "--blink-settings=imagesEnabled=false",
    ])
    ctx = await browser.new_context(user_agent=_UA, locale="nl-NL", viewport={"width": 1366, "height": 1000})
    await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    _cf_lock = asyncio.Lock()
    # Cloudflare één keer passeren met de eerste pagina; de clearance geldt voor de hele context.
    page0 = await ctx.new_page()
    await _pass_cf(page0)
    # Pool van tabbladen opbouwen (delen de clearance-cookies van de context).
    _pool = asyncio.Queue()
    await _pool.put(page0)
    for _ in range(_POOL_SIZE - 1):
        _pool.put_nowait(await ctx.new_page())
    _ready.set()


async def _do_search_on(page, q):
    url = "https://www.plus.nl/zoekresultaten?SearchTerm=" + urllib.parse.quote(q)
    await page.goto(url, wait_until="domcontentloaded", timeout=40000)
    try:
        await page.wait_for_selector('a[href*="/product/"]', timeout=12000)
        await page.wait_for_timeout(1200)
    except Exception:
        await page.wait_for_timeout(1500)
    return await page.evaluate(_EXTRACT)


async def _search_async(q):
    page = await _pool.get()                       # wacht op een vrij tabblad (begrenst concurrency)
    try:
        res = await _do_search_on(page, q)
        if not res:
            # mogelijk Cloudflare-uitdaging opnieuw → één keer herpasseren (max één tegelijk) en opnieuw
            async with _cf_lock:
                await _pass_cf(page)
            res = await _do_search_on(page, q)
        return res if isinstance(res, list) else []
    except Exception as e:
        return {"error": str(e)[:200]}
    finally:
        _pool.put_nowait(page)


def _run_loop():
    import asyncio
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    try:
        _loop.run_until_complete(_setup())
    except Exception:
        _ready.set()                               # deblokkeer wachters; searches geven dan een fout
    _loop.run_forever()


def ensure_started():
    """Start (indien nodig) de async-engine + browser-pool in een achtergrond-thread."""
    global _started
    with _start_lock:
        if not _started:
            threading.Thread(target=_run_loop, daemon=True).start()
            _started = True


def _cache_get(q):
    with _cache_lock:
        hit = _cache.get(q)
    if hit and time.time() - hit[0] < _CACHE_TTL and isinstance(hit[1], list):
        return hit[1]
    return None


def _cache_put(q, res):
    if isinstance(res, list):
        with _cache_lock:
            _cache[q] = (time.time(), res)
            if len(_cache) > 300:
                for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:150]:
                    _cache.pop(k, None)


def _search_leader(q, timeout):
    import asyncio
    if not _ready.wait(timeout):
        return {"error": "Zoekdienst start nog op, probeer zo opnieuw."}
    if _loop is None:
        return {"error": "Zoekdienst niet beschikbaar."}
    try:
        fut = asyncio.run_coroutine_threadsafe(_search_async(q), _loop)
        res = fut.result(timeout)
    except Exception as e:
        return {"error": str(e)[:200]}
    _cache_put(q, res)
    return res


def _local_search(q, timeout=60):
    """Zoek via de eigen browser-pool (met cache + coalescing). Blokkeert tot resultaat/timeout.
    Zoeken meerdere aanvragen TEGELIJK naar hetzelfde product, dan doet er ÉÉN de echte zoekopdracht
    en delen de rest datzelfde resultaat (scheelt browserwerk bij een piek voor populaire producten)."""
    cached = _cache_get(q)
    if cached is not None:
        return cached
    ensure_started()
    with _inflight_lock:
        holder = _inflight.get(q)
        leader = holder is None
        if leader:
            holder = {"event": threading.Event(), "result": None}
            _inflight[q] = holder
    if not leader:                                  # volger: wacht op de leider en deel z'n resultaat
        if holder["event"].wait(timeout):
            return holder["result"]
        return {"error": "Zoeken duurde te lang (probeer opnieuw)."}
    try:                                            # leider: voer de echte zoek uit
        res = _search_leader(q, timeout)
    except Exception as e:
        res = {"error": str(e)[:200]}
    holder["result"] = res
    with _inflight_lock:
        _inflight.pop(q, None)
    holder["event"].set()                           # volgers vrijgeven met het gedeelde resultaat
    return res


# ─── Gedeelde HTTP-service (draait in de gunicorn-master) ─────────────────────
def start_service(port=SERVICE_PORT):
    """Start de warme browser-pool + een lokale HTTP-service waar de workers op zoeken. Blokkeert."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse, parse_qs
    ensure_started()                               # browser-pool NU opwarmen (Cloudflare passeren)

    class _Handler(BaseHTTPRequestHandler):
        def _json(self, obj, code=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/health":
                self._json({"ok": True, "ready": _ready.is_set()}); return
            if u.path == "/search":
                q = (parse_qs(u.query).get("q") or [""])[0].strip()
                if len(q) < 2:
                    self._json([]); return
                self._json(_local_search(q)); return
            self.send_error(404)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    srv.daemon_threads = True                      # elke zoek-request in z'n eigen thread → parallel
    srv.serve_forever()


def start_service_in_thread(port=SERVICE_PORT):
    """Start de gedeelde service in een achtergrond-thread (aanroepen in de gunicorn when_ready-hook)."""
    threading.Thread(target=lambda: start_service(port), daemon=True).start()


def _service_reachable(port=SERVICE_PORT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except Exception:
        return False


def _service_search(q, port=SERVICE_PORT, timeout=65):
    import urllib.request
    url = f"http://127.0.0.1:{port}/search?q=" + urllib.parse.quote(q)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def search(q, timeout=65):
    """Geef een lijst producten terug (of {'error':...}). Gebruikt de gedeelde service als die er is,
    anders de eigen browser-pool. Resultaten worden kort gecachet."""
    q = (q or "").strip()
    cached = _cache_get(q)
    if cached is not None:
        return cached
    if _service_reachable():
        try:
            res = _service_search(q, timeout=timeout)
            _cache_put(q, res)
            return res
        except Exception:
            pass                                   # service hapert → val terug op in-proces
    return _local_search(q, timeout=timeout)
