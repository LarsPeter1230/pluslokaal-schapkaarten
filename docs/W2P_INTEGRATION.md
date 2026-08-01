# Winkelpakketten-integratie (oud W2P-systeem → pluslokaal.com)

**Doel:** de kant-en-klare weekpakketten (schapkaarten) uit het oude W2P-systeem
`pluslokaal.nl/W2P` in pluslokaal.com beschikbaar maken, in **pluslokaal-design**
(NIET de stijl van het oude systeem). Knop **"Printbare winkelpakketten"** op het
Schapkaarten-dashboard (naast zoekbalk + Nieuwe kaart) → pagina met mappenstructuur
(Weekpakket → categorie/groep → kaarten). Meerdere kaarten selecteren → **samengevoegd
in 1 bestand per formaat** om te printen (geen echt winkelmandje nodig).

Gebruikerskeuze: **periodiek synchroniseren** (metadata+thumbnails opslaan zodat
bladeren snel/onafhankelijk is; de print-PDF's on-demand via de bestel-flow).

## Oud systeem — hoe het werkt (verkend, read-only)
- **Login:** `https://pluslokaal.nl/login.aspx` — velden `#username`, `#password`, submit
  door **Enter in het wachtwoordveld** (de "Inloggen"-knop klikken submit niet betrouwbaar).
  Forms-auth cookie. **Cloudflare niet blokkerend** (200), warme sessie werkt.
- **Credentials** staan in DB-`Setting` (`w2p_user`, `w2p_pass`) — net als SMTP. NIET hardcoden.
  (Winkel 0001: gebruiker `voorbeeldond`; wachtwoord in Setting — **rotatie aanbevolen**.)
- **Boomstructuur** via `RetailDocuments.aspx?PeriodID=<p>&CategoryID=<c>&PromotionGroupID=<g>`:
  - Categorie (bv. `CategoryID=7` = Weekpakketten; ook Slijterij/leeg/VINT).
  - Periode = weekpakket: WK27=`25023`, WK28=`25074`, WK29=`25131`, WK30=`25195`.
  - PromotieGroep = afdeling: AGF=`241741`, Bloemen=`241760`, Brood=`241742`,
    Convenience=`241743`, Diepvries=`241744`, Kaas=`241745`, Kruidenierswaren=`241746`,
    Vers gebak=`241748`, Vers vlees=`241749`, Vis=`241750`, Vleeswaren=`241751`, Zuivel=`241752`.
    (Deze ID's wisselen per periode — dynamisch uit de sidebar-links halen.)
- **Kaart-tegel** (in de groep-pagina): `div.panel.panel-default` met
  `h3.panel-title` = `"<formaat> - <code>-<productnaam>-"` (bv. "A3 liggend - 4439-7-F21-Gato Negro-"),
  `img.docimg.thumbnail` = `GetPromotionDocumentThumb.ashx?PromotionDocumentID=<id>`,
  en `input.template-checkbox[type=checkbox][name=<PromotionDocumentID>]`.
  Formaten: A3 liggend, A4 staand, A5 staand, A3 staand, SK Maxi, SK Middel + "Briljant …"-varianten.
- **Thumbnail:** `GET GetPromotionDocumentThumb.ashx?PromotionDocumentID=<id>` → PNG (geen aparte PDF-handler; die 404't).
- **Print-PDF (verzameldocument per formaat):** alleen via de bestel-flow:
  1. Vink de gewenste kaarten aan (checkbox name=<id>) → "In winkelmandje".
  2. Winkelmandje → Basket.aspx → "Ga naar downloadscherm" → order → `Thankyou.aspx?W2P_OrderID=<oid>`.
  3. Download per formaatgroep: `GET ./GetDownloadItemsOpStand.ashx?W2P_OrderID=<oid>&SafetyCheck=True&Group=<formaat>`
     (bv. `Group=A3 liggend`, `Group=SK Maxi`) → **gecombineerde PDF** van alle bestelde kaarten van dat formaat.

## Bouwplan (in te bouwen in app.py + templates, pluslokaal-design)
1. **`w2p_client.py`** — warme Playwright-thread + queue (zoals `plus_search.py`):
   - `login()` (Enter-submit), warme sessie herbruiken.
   - `crawl(period_id, category_id)` → lijst groepen + per groep de documenten
     `{promotion_document_id, formaat, naam, groep}` + thumbnail-bytes.
   - `list_periods(category_id)` en `list_groups(period_id, category_id)` uit de sidebar.
   - `order_and_download(doc_ids)` → maakt order, geeft per formaat de gecombineerde PDF-bytes
     (via GetDownloadItemsOpStand.ashx). Credentials uit `Setting`.
2. **DB-modellen:** `W2PPeriod`(pid,label), `W2PDocument`(promotion_document_id, period_id,
   category_id, group_label, formaat, naam, thumb_path, synced_at). Sync-functie
   `sync_w2p()` (idempotent) + throttled tick (zoals `auto_cleanup_tick`) of handmatige knop.
3. **Routes:** `/winkelpakketten` (browse: periodes → groepen → kaarten met thumbnails, multi-select),
   `/winkelpakketten/download` (POST doc_ids → order_and_download → 1 PDF per formaat, als zip of
   los; of samengevoegd). Alleen ingelogde gebruikers; thumbnails via een eigen route die het
   gecachte bestand serveert.
4. **UI:** knop "Printbare winkelpakketten" op het Schapkaarten-dashboard (naast zoek + Nieuwe kaart)
   + `winkelpakketten.html` (pluslokaal-design: linker mappenboom periode/groep, rechts kaart-grid
   met checkboxes, onderaan "Samenvoegen & downloaden per formaat").

## Valkuilen
- Warme browser ~300MB (net als plus.nl-worker); lazy starten.
- De bestel-flow **maakt echte orders** aan op het oude systeem (nodig voor de PDF). Alleen doen op
  gebruikersactie (download-knop), niet tijdens de metadata-sync.
- ID's (PromotionGroupID) verschillen per periode → altijd dynamisch uit de sidebar halen.
- Login: submit via Enter in `#password` (knop-klik werkt niet).
