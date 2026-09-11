# Zoeken op plus.nl - technische uitleg (herbruikbaar)

Dit document beschrijft hoe de productzoek op **plus.nl** werkt in PLUSLokaal, zodat je het in andere
apps kunt hergebruiken. Kernpunten: plus.nl heeft **geen publieke API**, staat achter **Cloudflare**, en
is een **OutSystems**-webapp. We halen data op met een **warme headless browser** die de Cloudflare-check
eenmalig passeert.

Referentie-implementatie: `plus_search.py`.

---

## 1. Het probleem

- **Geen officiele API.** Er is geen gedocumenteerd endpoint dat je met een simpele HTTP-call kunt bevragen.
- **Cloudflare bot-bescherming.** Een kale `requests.get()` krijgt een "Even geduld"-uitdagingspagina terug
  in plaats van de echte inhoud.
- **JavaScript-app (OutSystems).** De zichtbare HTML is grotendeels leeg; content wordt client-side geladen.
  Zoekresultaten renderen als DOM-tegels; productdetails komen via aparte OutSystems-"screen service"-calls.

Conclusie: je hebt een **echte browser** nodig die JS uitvoert en Cloudflare passeert. Wij gebruiken
**Playwright (Chromium)** in headless modus.

---

## 2. Architectuur op hoofdlijnen

```
[web workers / processen]
        |  HTTP (127.0.0.1:5100)
        v
[EEN gedeelde zoek-service]  --->  [EEN warme Chromium-browser]
                                     |- pool van N tabbladen (parallel zoeken)
                                     |- Cloudflare-clearance geldt voor de hele context
```

- **Eén warme browser**, gestart bij het opstarten van de app (bij ons in de gunicorn-master via de
  `when_ready`-hook). Die passeert Cloudflare **eenmalig**; de clearance-cookies gelden daarna voor **alle**
  tabbladen in dezelfde browsercontext.
- **Pool van tabbladen** (bij ons 12) op een asyncio-loop, zodat er veel zoekopdrachten **tegelijk** lopen
  in plaats van serieel.
- **Lokale HTTP-service** op `127.0.0.1:5100`. Alle app-processen (workers) bevragen die service, zodat ze
  **één** browser delen in plaats van elk een eigen browser te starten. Valt de service weg, dan is er een
  in-process fallback (zelfde pool-mechanisme binnen het proces).
- **Korte cache + request-coalescing** om pieken en dubbele zoekopdrachten goedkoop af te vangen.

Waarom dit patroon: 1 browser opwarmen kost tijd en RAM; die deel je. Cloudflare passeer je 1x. En met een
tab-pool schaal je naar veel gelijktijdige gebruikers zonder minutenlange wachtrij.

---

## 3. De browser opstarten en Cloudflare passeren

Belangrijke launch-argumenten en context-instellingen:

```python
browser = await p.chromium.launch(args=[
    "--disable-blink-features=AutomationControlled",  # minder "ik ben een bot"-signalen
    "--no-sandbox",
    "--blink-settings=imagesEnabled=false",           # afbeeldingen UIT op browserniveau
])
ctx = await browser.new_context(
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    locale="nl-NL",
    viewport={"width": 1366, "height": 1000},
)
await ctx.add_init_script(
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"  # webdriver-vlag verbergen
)
```

- **`imagesEnabled=false`**: cruciaal voor snelheid. De zoekpagina's worden veel lichter en je kunt veel
  meer parallel draaien. De **foto-URL's** blijven gewoon als attribuut in de DOM staan, dus je verliest ze
  niet - je downloadt de plaatjes alleen niet in de zoekbrowser. (Deed je image-blocking via een per-request
  `ctx.route`-callback, dan is dat juist trager: elke request loopt dan door Python.)
- **`navigator.webdriver` verbergen** en de automation-vlag uitzetten verkleinen de kans op een
  Cloudflare-challenge.

Cloudflare passeren = gewoon de homepage laden en wachten tot de titel niet meer de wachtpagina is:

```python
async def _pass_cf(page):
    for _ in range(3):
        try:
            await page.goto("https://www.plus.nl/", wait_until="domcontentloaded", timeout=40000)
        except Exception:
            pass
        await page.wait_for_timeout(5000)
        t = ((await page.title()) or "").strip()
        if t and "moment" not in t.lower():   # "Even geduld a.u.b." / "One moment" = challenge
            return True
    return False
```

Doe dit **één keer** met de eerste pagina; bouw daarna de pool op met extra tabbladen die de
clearance-cookies van de context erven.

---

## 4. Zoeken en data extraheren

De zoek-URL is simpelweg de publieke zoekresultatenpagina:

```
https://www.plus.nl/zoekresultaten?SearchTerm=<urlencoded query>
```

Per zoekopdracht:

```python
async def _do_search_on(page, q):
    url = "https://www.plus.nl/zoekresultaten?SearchTerm=" + urllib.parse.quote(q)
    await page.goto(url, wait_until="domcontentloaded", timeout=40000)
    try:
        await page.wait_for_selector('a[href*="/product/"]', timeout=12000)  # wacht op producttegels
        await page.wait_for_timeout(1200)                                    # even laten settelen
    except Exception:
        await page.wait_for_timeout(1500)
    return await page.evaluate(_EXTRACT)                                     # JS-extractie in de pagina
```

De extractie draait **in de pagina** (snelste manier om de gerenderde DOM te lezen). Kern van `_EXTRACT`:

- Selecteer producttegels: `document.querySelectorAll('a[href*="/product/"]')`.
- Per tegel:
  - **href**: `a.getAttribute('href')` (bv. `/product/friesche-vlag-halvamel-pak-455-ml-382028`). Het
    getal achteraan is het **plus.nl product-id**, niet de EAN.
  - **naam**: `a.getAttribute('title')`.
  - **eenheid/inhoud**: uit `aria-label` (die begint met de naam; de rest is bv. "Per pak 455 ml").
  - **prijzen**: alle voorkomens van `\d+\.\d{2}` in de tegeltekst (na normaliseren van `1,49`/`1 49` naar
    `1.49`).
  - **foto**: de grootste `<img>` in de tegel die geen badge/keurmerk is (filter op
    `laagblijvers|badge|keurmerk|nutri|logo`). URL zit als attribuut in de DOM, ook met images uit.
  - **actie/deal**: regex op de tegeltekst voor `X voor EUR Y`, `X gram voor EUR Y`, `X% korting`.
- Dedupe op href, cap op ~16 resultaten.

Resultaat per product (ruwe vorm):

```json
{ "href": "/product/...", "name": "Friesche Vlag Halvamel",
  "unit": "Per pak 455 ml", "prices": ["1.49"], "img": "https://images.ctfassets.net/...",
  "deal": "2 voor 3.99" }
```

Daarna normaliseer je dat naar je eigen velden (naam, merk, verpakking, prijs, actie, van-prijs, enz.).

Zelfherstel: geeft de zoek een lege lijst terug, dan is er mogelijk opnieuw een Cloudflare-challenge. Passeer
CF dan nog een keer (onder een lock, zodat maar 1 tab het tegelijk doet) en probeer opnieuw:

```python
res = await _do_search_on(page, q)
if not res:
    async with _cf_lock:
        await _pass_cf(page)
    res = await _do_search_on(page, q)
```

---

## 5. Concurrency, cache en coalescing

- **Tab-pool** (asyncio.Queue met N pagina's): een zoek pakt een vrij tabblad, doet z'n werk, en geeft het
  terug. Dit begrenst automatisch hoeveel er tegelijk draaien.
- **Cache (60 s)** per zoekterm: veelgezochte producten komen direct terug. Bewust **kort**, zodat nieuwe
  artikelen, gewijzigde prijzen en vervangen foto's binnen een minuut vers zijn.
- **Request-coalescing**: zoeken meerdere aanvragen **tegelijk** naar dezelfde term, dan doet er **één** de
  echte zoekopdracht (de "leider") en delen de rest datzelfde resultaat. Scheelt browserwerk bij een piek op
  een populair product.

---

## 6. De gedeelde HTTP-service

Een kleine `ThreadingHTTPServer` op `127.0.0.1:5100` ontsluit de browser aan de app-processen:

- `GET /health` -> `{"ok": true, "ready": <bool>}`
- `GET /search?q=<term>` -> lijst producten (of `{"error": ...}`)
- `GET /ean?href=<producthref>` -> `{"eans": [...], "ean": <eerste of null>}` (zie hoofdstuk 7)

De workers roepen dit lokaal aan met `urllib.request.urlopen(...)`. `daemon_threads = True` zodat elke
request in z'n eigen thread parallel loopt. Start de service in een achtergrond-thread bij het opstarten van
de app (bij ons: gunicorn `when_ready`).

Publieke functie met fallback:

```python
def search(q, timeout=65):
    cached = _cache_get(q)
    if cached is not None:
        return cached
    if _service_reachable():          # draait de gedeelde service? gebruik die
        try:
            res = _service_search(q, timeout=timeout)
            _cache_put(q, res); return res
        except Exception:
            pass                      # service hapert -> in-process fallback
    return _local_search(q, timeout)  # eigen browser-pool in dit proces
```

---

## 7. De EAN/barcode ophalen (bonus)

De zoekpagina geeft **geen** EAN. De **productpagina** ook niet in zichtbare HTML. Maar plus.nl (OutSystems)
laadt de productdetails via een background-call, en **daar** zit de EAN in:

```
POST .../screenservices/ECP_Product_CW/ProductDetails/PDPContent/DataActionGetProductDetailsAndAgeInfo
-> JSON met o.a.  data.ProductOut.Medicine.EAN = "8712800102403"
```

Aanpak: laad de productpagina in de warme browser en **onderschep die response**:

```python
async def _ean_on(page, url):
    async with page.expect_response(
            lambda r: 'GetProductDetailsAndAgeInfo' in r.url, timeout=30000) as ri:
        await page.goto(url, wait_until='domcontentloaded', timeout=40000)
    txt = await (await ri.value).text()
    found = []
    for m in re.findall(r'"[^"]*(?:EAN|GTIN|Gtin|gtin)[^"]*"\s*:\s*"?(\d{8,14})"?', txt):
        d = ''.join(c for c in m if c.isdigit())
        if _valid_ean(d) and d not in found:      # EAN-8/13 checksum-validatie
            found.append(d)
    return found                                   # kan meerdere zijn -> laat de gebruiker kiezen
```

Let op / eerlijke kanttekening:
- plus.nl geeft doorgaans **één** EAN per product; soms meerdere velden -> geef een lijst terug en laat de
  gebruiker kiezen.
- De plus.nl-EAN is de **officiele GTIN** die plus.nl voert; die is **niet gegarandeerd** identiek aan de
  barcode die fysiek op elke verpakking staat (andere/oudere verpakking-GTIN kan voorkomen). Voor kritische
  toepassingen: laat 'm bewerkbaar en/of scan ter controle.
- Deze detail-call is **zwaar** (volledige productpagina laden). Doe het **on-demand** (bij een klik) of
  gethrottled (paar tegelijk) en **cache** het per href (bij ons 1 dag).

---

## 8. EAN-checksum (EAN-8 / EAN-13)

```python
def _valid_ean(code):
    d = ''.join(c for c in str(code or '') if c.isdigit())
    if len(d) not in (8, 13):
        return False
    digits = [int(c) for c in d]
    check = digits[-1]
    body = digits[:-1][::-1]
    s = sum(n * (3 if i % 2 == 0 else 1) for i, n in enumerate(body))
    return (10 - s % 10) % 10 == check
```

---

## 9. Afhankelijkheden

- **Playwright** + Chromium: `pip install playwright` en `python -m playwright install chromium`
  (plus `install-deps chromium` voor systeembibliotheken op Linux).
- Verder alleen standaardbibliotheek (`asyncio`, `threading`, `http.server`, `urllib`, `re`, `json`).

---

## 10. Aandachtspunten / valkuilen

- **Fragiliteit.** Het leunt op de HTML-structuur en Cloudflare van plus.nl. Verandert PLUS de zoekpagina of
  verscherpt de bot-detectie, dan kan de zoek breken en is onderhoud nodig. De code herstelt zichzelf voor
  Cloudflare (opnieuw passeren bij een leeg resultaat), maar niet voor structuurwijzigingen.
- **Fork-safety.** Start de browser/loop in de master en deel via de HTTP-service; start geen browser vlak
  vóór een fork. Elk worker-proces praat via 127.0.0.1 met de master.
- **Rate/gedrag.** Houd het menselijk: warme browser hergebruiken, korte cache, coalescing. Niet honderden
  productpagina's tegelijk voor EAN's ophameren.
- **Juridisch/nette omgang.** Dit is scrapen van publiek zichtbare pagina's. Respecteer de voorwaarden van
  plus.nl en gebruik het beheerst.

---

## 11. Minimale, zelfstandige demo

```python
# pip install playwright ; python -m playwright install chromium
import asyncio, urllib.parse
from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

EXTRACT = r"""() => {
  const out=[], seen=new Set();
  for (const a of document.querySelectorAll('a[href*="/product/"]')) {
    const href=a.getAttribute('href'); if(!href||seen.has(href))continue; seen.add(href);
    const name=(a.getAttribute('title')||'').trim(); if(!name)continue;
    const txt=(a.innerText||'').replace(/(\d+)[\s.,]+(\d{2})(?!\d)/g,'$1.$2');
    const prices=[...new Set((txt.match(/\d+\.\d{2}/g)||[]))];
    out.push({href, name, prices}); if(out.length>=16)break;
  }
  return out;
}"""

async def main(query):
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--disable-blink-features=AutomationControlled",
                                          "--no-sandbox","--blink-settings=imagesEnabled=false"])
        ctx = await b.new_context(user_agent=UA, locale="nl-NL",
                                  viewport={"width":1366,"height":1000})
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await ctx.new_page()
        # Cloudflare eenmalig passeren
        for _ in range(3):
            await page.goto("https://www.plus.nl/", wait_until="domcontentloaded", timeout=40000)
            await page.wait_for_timeout(5000)
            if "moment" not in ((await page.title()) or "").lower(): break
        # Zoeken
        await page.goto("https://www.plus.nl/zoekresultaten?SearchTerm="+urllib.parse.quote(query),
                        wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_selector('a[href*="/product/"]', timeout=12000)
        await page.wait_for_timeout(1200)
        for row in await page.evaluate(EXTRACT):
            print(row["name"], row["prices"], row["href"])
        await b.close()

asyncio.run(main("halvamel"))
```

Voor de volledige, productie-klare versie (pool, service, cache, coalescing, EAN): zie `plus_search.py`.
