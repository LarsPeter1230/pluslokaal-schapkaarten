# Portaal - pluslokaal.nl in ons jasje (Plan A: dunne overlay-proxy)

**Status:** ✅ GEÏMPLEMENTEERD (20-07-2026, v2.10.0) volgens de iframe-variant van Plan A - zie §Realisatie onderaan.
**Doel:** de oude **pluslokaal.nl** (jaarkalender, tarieven, mutatieformulieren, campagnes,
"vraag een opdracht aan", enz.) beschikbaar maken **binnen onze app**, in **ons design**, met
**automatisch inloggen op de achtergrond** per gebruiker. Elke gebruiker vult eenmalig zijn
pluslokaal.nl-inloggegevens in onder *Mijn profiel*; wij bewaren de sessie en tonen de content
onder `/portaal` in de pluslokaal.com-huisstijl.

Kernidee van **Plan A**: we bouwen de content **niet** na. We halen de **echte, live pagina** van
pluslokaal.nl op namens de gebruiker en serveren die 1-op-1 door - we vervangen alleen de **chrome**
(hun header/nav) door de onze en injecteren onze CSS. Zo is de **inhoud altijd actueel** en hoeven
wij niets bij te werken als PLUS de content wijzigt.

---

## 1. Waarom A het "content verandert steeds"-probleem oplost

Scheid **structuur** (HTML-skelet + CSS van pluslokaal.nl) van **content** (de kalender-items,
tarieven, campagnes zelf).

- **Content** verandert vaak → bij A komt die **automatisch mee**, want we proxyen de live pagina.
  Nul onderhoud, want we parsen de inhoud niet.
- **Skelet** verandert zelden → A raakt alleen de **header-container** aan (die verbergen we) en
  injecteert onze CSS. Als PLUS het skelet verbouwt, faalt het **zacht**: in het ergste geval zie je
  even hun oude header, niet een kapotte pagina. Formulieren blijven werken want het zijn hún echte
  `<form>`s/POSTs die we gewoon doorzetten.

Dit is bewust het tegenovergestelde van de plus.nl-productzoek (`plus_search.py`), waar we DOM-velden
**scrapen** - dat is gevoelig en vergt onderhoud. Voor het portaal willen we juist **niet** scrapen.

---

## 2. Architectuur op hoofdlijnen

```
Browser ──/portaal/...──► pluslokaal.com (Flask, app.py)
                              │
                              │  (per-user sessie hergebruiken)
                              ▼
                        Portaal-proxy laag  ──►  pluslokaal.nl
                              │                     ▲
                              │  login namens user  │
                              └─ warme sessie/cookies per user
```

Vier componenten:

1. **Credential-opslag** - per user versleuteld opgeslagen pluslokaal.nl login (§3).
2. **Sessie-/login-worker** - logt op de achtergrond in en houdt de sessie warm (§4).
3. **Proxy + reskin** - haalt de pagina op, herschrijft URLs, verbergt hun header, injecteert
   onze header + CSS (§5).
4. **UI** - nieuw nav-item "Portaal" met submenu + profielsectie voor de credentials (§6).

Alles komt in `app.py` (single-file backend) + een klein nieuw modulebestand `portaal.py` voor de
worker (zelfde opzet als `plus_search.py`), plus templates.

---

## 3. Credential-opslag (versleuteld, nooit teruggetoond)

**Datamodel** - nieuwe kolommen op `User` (idempotente ALTER in `_migrate_db()`):

| kolom | inhoud |
|---|---|
| `portaal_user` | pluslokaal.nl gebruikersnaam/e-mail (leesbaar, mag getoond) |
| `portaal_pass_enc` | **versleuteld** wachtwoord (bytes/base64), nooit teruggetoond |
| `portaal_status` | `none` / `ok` / `fout` - laatste loginresultaat |
| `portaal_checked` | timestamp laatste succesvolle login |

**Encryptie:** wachtwoord **niet** hashen (we moeten het kunnen hergebruiken om in te loggen), dus
**symmetrisch versleutelen** met `Fernet` (AES). Sleutel afgeleid van een **apart** keybestand
`.portaal_secret` (32 bytes, net als `.secret_key`; niet in git, `chmod 600`). Reden voor een apart
bestand: rotatie/scope losstaand van sessie-signing.

> **Let op - dependency:** `cryptography` is **nog niet geïnstalleerd** in deze omgeving
> (`playwright` wel). Bouwstap: `pip install cryptography`. Alternatief zonder extra package is
> AES via de stdlib niet beschikbaar; `cryptography`/Fernet is de nette keuze.

**Flow:**
- Gebruiker vult in `/profiel` (of `/portaal/koppelen`) gebruikersnaam + wachtwoord in.
- We proberen **direct** een testlogin (§4). Lukt het → opslaan (`portaal_status='ok'`), veld leeg­
  laten in de UI met tekst *"Gekoppeld ✓ (wijzig)"*. Lukt het niet → melding, niets opslaan.
- Wachtwoordveld toont nooit de bestaande waarde; alleen een "wijzig"-knop.

---

## 4. Login-/sessie-worker (`portaal.py`)

Zelfde patroon als `plus_search.py`: één achtergrond-thread met een `queue`, thread-safe, warm.

**Twee mogelijke transporten - kies op basis van hoe pluslokaal.nl inlogt (nog verifiëren):**

- **4a. `requests.Session`** (voorkeur, licht): als login een simpele form-POST is (username/password
  + evt. CSRF-token). We halen de loginpagina op, lezen het CSRF/hidden-veld, POST'en de credentials,
  bewaren de cookies in een **per-user `requests.Session`** server-side. Snel, weinig geheugen.
- **4b. Playwright** (zwaarder, robuust): als er JS/Cloudflare/redirect-magie bij zit (zoals plus.nl).
  Eén warme browser, per user een **context** met eigen cookies. ~300 MB zoals bij `plus_search`.
  Alleen nemen als 4a niet lukt.

**Wat we bouwen (transport-agnostisch):**

```python
# portaal.py (schets)
def login(user_id, username, password) -> {"ok": bool, "error": str|None}
    # form-POST (4a) of Playwright (4b); zet cookies in _sessions[user_id]

def fetch(user_id, path, method="GET", data=None) -> {"status", "headers", "body"}
    # hergebruik warme sessie; op 401/redirect-naar-login → één keer opnieuw login() en retry
```

**Sessie-levensduur:** cookies vervallen bij pluslokaal.nl na X tijd. Bij een `fetch` die naar de
loginpagina redirect (detecteren op URL/known marker) → **transparant opnieuw inloggen** met de
opgeslagen credentials en de request herhalen. Voor de gebruiker onzichtbaar = het "SSO-gevoel".

**Concurrency:** per user een eigen sessie/lock; meerdere users tegelijk = meerdere sessies. Bij 4a
is dat goedkoop; bij 4b limiteren (bijv. LRU van N contexts).

---

## 5. Proxy + reskin (de kern)

Route: `GET/POST /portaal/<path:sub>` (+ `/portaal` → default landingspagina, bv. kalender).

Stappen per request:

1. **Auth-gate:** ingelogd in pluslokaal.com én `portaal_status=='ok'`, anders → koppel-scherm.
2. **Ophalen:** `portaal.fetch(uid, sub, method, form-data)` → originele response.
3. **Content-type routing:**
   - **HTML** → herschrijven + reskin (stap 4-6).
   - **Assets** (CSS/JS/img/font/PDF/downloads) → **byte-voor-byte doorzetten** met originele
     `Content-Type` (zodat downloads/mutatie-PDF's gewoon werken). Zo nodig via een aparte
     `/portaal/asset?u=<geëncodeerde originele URL>` proxy zodat de browser same-origin blijft.
4. **URL-herschrijven (BeautifulSoup):** alle `href`/`src`/`action`/`form` die naar pluslokaal.nl
   wijzen → prefixen met `/portaal/...` zodat navigatie **binnen onze app** blijft. Externe links
   (andere domeinen) met rust laten of in nieuw tabblad.
5. **Chrome verbergen:** hun header/nav/footer-container selecteren en `display:none` (of
   `.replaceWith`). We hoeven de *exacte* selector niet perfect te raken - mislukt hij, dan zie je hun
   header, geen crash (zacht falen).
6. **Onze skin injecteren:** in `<head>` onze `plus.css` + een klein `portaal.css`; bovenaan `<body>`
   onze eigen `base.html`-header/nav (met "Portaal" actief) en een submenu. Zo staat de PLUS-content
   in **ons** canvas, in **onze** kleuren.

**Beveiliging/afscherming:**
- **SSRF-guard:** alleen paden op host `pluslokaal.nl` proxyen (whitelist), net als de ctfassets/
  plus.nl-guard bij de overlay-foto's.
- **Geen credential-lek:** de opgeslagen wachtwoorden verlaten de server nooit richting browser.
- **CSRF:** onze eigen `before_request` CSRF-check (die er al is) moet `/portaal/*` POSTs die naar
  pluslokaal.nl gaan **overslaan** (hun forms hebben hún eigen token) - uitzonderen in de check.

---

## 6. UI

**Nav (`base.html`)** - nieuw item naast Schapkaarten/Scankaarten/Labels:

```html
<a href="{{ url_for('portaal') }}" class="{{ 'active' if ep.startswith('portaal') }}">
  <i class="fa fa-globe"></i> Portaal
</a>
```

Eventueel als **dropdown** met snelkoppelingen: *Kalender · Tarieven · Campagnes · Mutatie aanvragen*
(elk een deeplink `/portaal/<pad>`). Zichtbaar afhankelijk van rol/capability (bv. `can(user,'portaal')`).

**Koppel-scherm** (`/portaal` zonder koppeling, of sectie in `/profiel`):
- Uitleg + velden *pluslokaal.nl gebruikersnaam* + *wachtwoord* + "Koppelen".
- Bij succes: groene "Gekoppeld ✓" met knop "ontkoppelen/wijzigen".
- Tekst: wachtwoord wordt **versleuteld** bewaard en alleen gebruikt om jou automatisch in te loggen.

---

## 7. Robuustheid & vangnetten

- **Cache laatste goede HTML** (paar uur TTL) per pad; als een ophaal faalt → toon laatste goede
  stand met badge *"kon niet verversen - weergave van HH:MM"* i.p.v. lege pagina.
- **Nachtelijke health-check** (cron/loop): logt in + haalt een paar sleutelpagina's op; mailt/meldt
  bij een breuk vóórdat een gebruiker het merkt. (Hergebruik `send_mail_async`.)
- **"Open origineel op pluslokaal.nl"**-link als ultieme vangnet.
- **Zacht falen** bij header-verbergen: nooit een 500 om een gemiste selector.

---

## 8. Aandachtspunten / open vragen (voor de bouw)

1. **Loginmechanisme van pluslokaal.nl** verifiëren: simpele form-POST (→ 4a `requests`) of
   JS/Cloudflare (→ 4b Playwright)? Bepaalt het transport.
2. **Toestemming/ToS:** namens de gebruiker inloggen op pluslokaal.nl met diens eigen credentials -
   afstemmen dat dit is toegestaan/gewenst.
3. **`pip install cryptography`** toevoegen (Fernet voor credential-encryptie).
4. **Welke pagina's** in het submenu (kalender, tarieven, campagnes, mutaties, opdracht aanvragen)?
   Sitemap van de oude pluslokaal.nl inventariseren.
5. **Downloads/PDF's** (mutatieformulieren) end-to-end testen via de asset-proxy.
6. **2FA op pluslokaal.nl?** Zo ja, dan is volautomatisch inloggen lastiger (dan 4b + evt. eenmalige
   handmatige stap).

---

## 9. Voorgestelde bouwvolgorde (fasen)

- **Fase 0 - verkenning:** login-mechanisme + sitemap van pluslokaal.nl vaststellen (bepaalt 4a/4b).
- **Fase 1 - credentials:** `cryptography` erbij, `User`-kolommen + migratie, Fernet-helper,
  `/profiel`-koppelsectie, testlogin.
- **Fase 2 - worker:** `portaal.py` met `login()` + `fetch()` (transport uit fase 0), auto-relogin.
- **Fase 3 - proxy/reskin:** `/portaal/<path>` route, URL-herschrijven, asset-proxy, header verbergen,
  onze skin injecteren; SSRF- + CSRF-uitzonderingen.
- **Fase 4 - UI:** nav-item + submenu, koppel-scherm, deeplinks.
- **Fase 5 - robuustheid:** cache + fallback-badge, health-check, "open origineel"-link.
- **Fase 6 - verificatie:** kalender/tarieven/mutatie doorlopen; download testen; zacht-falen testen.
```

---

## Realisatie (zoals gebouwd, v2.10.0)

Gekozen: **Plan A in iframe-variant** - onze echte app-header (base.html) met "Portaal" bovenaan; de
volledige pluslokaal.nl draait geïsoleerd in een `<iframe>` onder `/portaal/view/`. Nul CSS-conflict,
content altijd live.

**pluslokaal.nl bleek een ASP.NET-WebForms/Bootstrap-site, GEEN Cloudflare/JS-gate** → lichte
`requests.Session`-route (transport 4a), niet Playwright. Login = GET `login.aspx` → `__VIEWSTATE` +
`__VIEWSTATEGENERATOR` lezen → POST `username`/`password`/`remember`. Sessie verlopen ⇒ redirect naar
`login.aspx`; dat detecteren we (`_looks_logged_out`) en loggen transparant opnieuw in.

**Bestanden/wijzigingen:**
- **`portaal.py`** (nieuw): warme per-gebruiker `requests.Session` (dict `_sessions[uid]`), `login()`,
  `fetch()` (auto-relogin bij GET), `_parse_hidden()`.
- **`app.py`**: `User` +`portaal_user`/`portaal_pass_enc`/`portaal_status`/`portaal_checked` (+migratie).
  Fernet-helpers (`.portaal_secret`, `_portaal_encrypt/_decrypt`). Reskin/proxy: `_portaal_rewrite_url`
  (alleen host `pluslokaal.nl`/`www.pluslokaal.nl`, extern blijft staan), `_portaal_rewrite_css`
  (url()/@import), `_portaal_reskin_html` (BeautifulSoup: href/src/action/srcset/inline-style +
  `<base target="_self">`). Routes: `/portaal` (shell), `/portaal/koppel` (POST, testlogin→opslaan),
  `/portaal/ontkoppel` (POST), `/portaal/view/<path:sub>` (GET/POST proxy, html→reskin, css→rewrite,
  rest→bytes doorzetten incl. Content-Disposition). `portaal_view` in `_CSRF_EXEMPT`.
- **`templates/portaal.html`** (nieuw): koppelscherm (niet gekoppeld) of volledig-breed iframe +
  portaalbalk (Start/Vernieuwen/Origineel/Ontkoppelen).
- **`templates/base.html`**: nav-item "Portaal" (desktop + mobiel). **Geen submenu's** (bewust -
  navigatie gebeurt via pluslokaal.nl's eigen menu binnen het iframe).

**Dependencies toegevoegd:** `cryptography` (Fernet), `requests`, `beautifulsoup4`.

**Geverifieerd (Playwright, lokaal):** koppelen met echte account `voorbeeldond` → iframe toont live home
("Home - Portal", campagnetegels + afbeeldingen laden via proxy); sub-pagina `/aanbiedingen/` proxyt
correct ("Aanbiedingen - Portal"); URL-rewrite (css/js root-relatief → `/portaal/view/…`), externe CDN
ongemoeid; geen JS-fouten. Test-medewerker daarna verwijderd.

**Nog te doen / ideeën (niet gebouwd):** cache-fallback met "weergave van HH:MM"-badge; nachtelijke
health-check; per-rol zichtbaarheid (`can(user,'portaal')`) als je Portaal niet voor iedereen wilt.

---

## Iteratie 2 (20-07-2026) - header-items in ons design + layout-fixes

Feedback gebruiker verwerkt:
- **Reskin-parser gedropt:** pluslokaal.nl heeft kapotte HTML (o.a. een comment `<!--<div class="collapse
  navbar-collapse">…<!--/.nav-collapse -->` die het hele top-header-blok omvat) + JS-gebouwde nav. Een
  BeautifulSoup-round-trip herserialiseerde dat en de browser liet hele secties vallen. **Oplossing:**
  `_portaal_reskin_html` doet nu **alleen regex-attribuut-herschrijving** (href/src/action/poster/data-src/
  srcset + url()/@import in <style>/style=""), structuur blijft byte-identiek → browser parset als het echt.
- **Sidebar hersteld:** pluslokaal.nl zet `.sidebar` en `.nav-panels` op `display:none` en toont ze via
  runtime-JS dat het proxyen niet overleeft. We injecteren CSS: op desktop `.sidebar{display:inline-block!
  important;float:left;width:31%}` + `.container:has(.sidebar) .pagecontent{float:right;width:67%}` (twee
  kolommen zoals origineel). `.nav-panels` (uitgeklapt mega-menu) verbergen we. Injected JS haalt het
  **"Schapkaarten"**-item uit de sidebar (dat hebben we zelf al in de app).
- **Header-items + zoek in ONS design:** de portaalbalk (Start/Vernieuwen/"Portaal·pluslokaal.nl") is weg.
  In plaats daarvan een **witte categorie-navbalk** (`portaal.html`, `.portaal-nav`, PLUS-groene links) met
  de 6 vaste top-categorieën `_PORTAAL_CATS` (Landelijke/Lokale activiteiten, Winkel, E-commerce, Social
  Media, Helpdesk) + home-icoon + **zoekveld** (input name=`s` → `/search/?s=`). Links/zoek sturen het
  iframe aan via JS-helpers `pgo()`/`psearch()` (`iframe.src=…`; `target="pframe"` bleek onbetrouwbaar).
- Geverifieerd (Playwright): home 2-koloms, Winkel-klik → `/winkel/`, zoeken "kaas" → `/search/?s=kaas`
  met Zoekresultaten, Schapkaarten weg. Alles in ons design.

**Bekende beperking:** de top-**userbar** (winkelmandje/geschiedenis-iconen, "U bent ingelogd als") van
pluslokaal.nl valt weg (zit in het kapotte comment-blok + JS). Niet krit-functioneel; onze eigen categorie-
nav + zoek dekken de navigatie. Sub-item-dropdowns per categorie zijn (nog) niet gebouwd - een categorie
laadt de landingspagina (met eigen sub-nav) in het iframe.

---

## Iteratie 3 (20-07-2026) - mega-menu's, userbar-iconen, laad-spinner

- **Hover-dropdowns (mega-menu) per categorie**, net als het origineel. De hele menu-boom wordt server-
  side gereconstrueerd uit de **URL-padstructuur** van de `item-link`-anchors (`/cat/sectie/item/` →
  boomdiepte): `_portaal_build_menu()` + cache `_portaal_menu()` (`_portaal_menu_cache`, TTL 30 min, valt
  bij fout terug op oude cache/kale lijst). Gerenderd als `.pn-cat > .pn-mega` (kolommen via `column-count`),
  toont op `:hover`. In ons design (witte panel, groene sectiekoppen).
- **Userbar-iconen** `_PORTAAL_ICONS` rechts in de nav: Winkelmandje (`/W2P/Basket.aspx`),
  Mijn bestelgeschiedenis (`/W2P/OrderHistory.aspx`), Mijn actieoverzicht (`/Campaigns/Home.aspx`).
- **Laad-spinner**: PLUS-blaadjes-overlay (`.portaal-loading` + `.pl-leaf`/`plLeafPulse`, dezelfde
  blaadjes-SVG als de W2P cache-sync) **over het iframe** (geen popup). `showLoad()` bij elke navigatie
  (pgo/psearch), `hideLoad()` op iframe-`load` + 20s-fallback.
- **Volledige breedte**: `.page:has(.portaal-wrap){max-width:none;padding:0}` (+ footer verbergen) zodat
  de nav op één rij past en de brede pluslokaal.nl-content ruim staat; koppelscherm houdt normale padding.
- Geverifieerd (Playwright): 5 mega-menu's (Landelijke act. 6 secties/20 items, Winkel 10 secties, …) +
  Helpdesk als losse link; 3 icon-links laden (winkelmandje → `/W2P/Basket.aspx`); sub-item-klik → diepe
  link; spinner verschijnt bij navigatie en verdwijnt na laden. Nav 1 rij op volle breedte.

---

## Iteratie 4 (20-07-2026) - polish: nav 1-rij, mandje-badge, spinner-vooraan, form-styling

- **Nav wrapt nooit** (welk scherm dan ook): `.pn-cats` (categorieën) `overflow-x:auto` en kan krimpen,
  terwijl `.pn-right` (iconen + zoekveld) `flex:0 0 auto` - zoeken komt dus nooit op een 2e regel.
  Getest 820-1500px: altijd 1 rij. Nav compacter (kleinere padding/gaps).
- **Mega-menu `position:fixed`** met `--pn-top` (= onderkant nav, via JS gezet op load/resize) i.p.v.
  absolute - zo knipt de scroll-container (`overflow`) de dropdown niet af.
- **Winkelmandje-badge**: `_portaal_basket_count()` leest `fa-shopping-basket → <span class=counter>N`
  uit de (kort gecachete, per-user) home-HTML `_portaal_home_doc()` (gedeeld met de menu-build). Rode
  `.pn-badge` op het mandje-icoon als N>0.
- **Laad-spinner altijd vooraan**: `.portaal-loading` z-index 60 (boven mega 40) + bij navigatie
  `closeMega()` (klasse `pn-busy` sluit dropdowns, opgeheven bij `mouseleave`). Spinner is default
  zichtbaar bij openen tot iframe-`load`.
- **Formulieren/knoppen 1-op-1 met pluslokaal.com**: reskin injecteert CSS (PLUS-kleuren + spraakwolk-
  radius `22px…4px`) voor `.pagecontent`/`.sidebar` `.btn`/inputs/selects/textarea. Geverifieerd: submit
  = groen + radius 22. (Tekstvelden in accordion-stappen vallen onder dezelfde regel.)
- **Volledige-breedte** portaal via `.page:has(.portaal-wrap)` (uit iteratie 2, nu i.c.m. 1-rij nav).

---

## Iteratie 5 (20-07-2026) - brede huisstijl, mobiel hamburger-menu, profielkoppeling

- **Onze stijl overal** (reskin-injectie, nu **iframe-breed** i.p.v. alleen `.pagecontent` - veilig want de
  CSS raakt alleen pluslokaal.nl-DOM en hun header is verborgen; werkt zo óók op W2P-pagina's zonder
  `.pagecontent`): content-tegels (`.blockitem`, BBQ/Mepal e.d.) met radius+shadow+hover; panelen
  (`.panel`/`.panel-heading-custom`); **tabellen** (`.table`/`.fixed-table-header th` → groene header,
  zebra, hover) voor winkelmandje/bestelgeschiedenis; koppen (`h1`/`.optiongroup>h2`) donkergroen;
  knoppen + invoervelden (al eerder). 
- **Spinner ook bij links in het iframe** (bv. sidebar Jaarkalender/Tarieven): de reskin injecteert een
  `beforeunload`+capture-`click`-handler die `window.parent.showLoad()` aanroept → spinner bij ELKE
  navigatie, ook die niet via onze nav loopt.
- **Mobiel hamburger-menu**: `.pn-burger` (≤820px) opent een off-canvas `.pn-mobile` (groene kop + ×,
  zoekveld, Home, categorieën met uitklapbare sub-items, icoon-links). Desktop-nav (`.pn-cats`) verborgen
  op mobiel; alleen winkelmandje-icoon blijft in de balk. `pgo()` sluit het menu.
- **Profiel-koppeling** (`/profile`): nieuw paneel "Portaal & pluslokaal.nl" - toont het **gekoppelde
  account**, en laat de gebruiker **gebruikersnaam + wachtwoord wijzigen** (wachtwoord leeg = behouden) of
  **ontkoppelen**. Route-acties `action=portaal` / `portaal_unlink` in `profile()` (testlogin vóór opslaan,
  home-cache invalidatie). Placeholder-voorbeeld → `bv. 110ond`.

---

## Iteratie 6 (20-07-2026) - winkelmandje/bestellen werkend + live badge + stijlgids

- **AJAX-acties werkten niet** ("Bestellen"/mandje leegmaken → "something went wrong" / 404): pluslokaal.nl
  doet die via JS met **root-relatieve URLs** die op onze origin belandden. Fixes:
  1. Reskin injecteert VROEG een patch op `XMLHttpRequest.open` + `window.fetch` die pluslokaal.nl-/root-
     relatieve URLs naar `/portaal/view/...` herschrijft (AJAX blijft same-origin geproxyd).
  2. **404-vangnet** (`errorhandler(404)`): een pluslokaal.nl-pad met een `/portaal/view/`-referer wordt
     alsnog 302/307 (307 behoudt POST) naar `/portaal/view/<pad>` gestuurd - vangt volledige navigaties/
     form-submits (bv. "Naar mijn winkelmand", DeleteDocument).
  3. AJAX-fragmenten (geen `<html>`/`<head>`) worden alleen ge-URL-herschreven, niet ge-injecteerd
     (`_portaal_rewrite_attrs_only` afgesplitst van `_portaal_reskin_html`) zodat JSON/fragmenten heel blijven.
- **Live winkelmandje-badge**: route `/portaal/basket-count` (verse teller) + parent `refreshBasket()` na
  elke iframe-`load` én na mandje-XHR's (`loadend` op basket/adddocument/deletedocument) → badge (`pBasketBadge`
  /`pBasketBadgeM`) update direct bij toevoegen/verwijderen. Geverifieerd: leeg→weg, Bestellen→1, verwijderen→weg.
- **Stijlgids** (downloadbaar): `docs/PLUS-Stijlgids.html` - zelf-standig, met kleuren+hex+var-namen,
  `:root`-tokens (kopieerbaar), typografie, knoppen, velden, panelen/badges/tabellen, app-header en het
  **inlogscherm** (mockup + CSS). Voor het bouwen van nieuwe apps 1-op-1 in PLUS-stijl.
