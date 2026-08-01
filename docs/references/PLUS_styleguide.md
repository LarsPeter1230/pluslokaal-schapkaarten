# PLUS – Style Guide (intern)

Gedestilleerd uit de bestaande login- en homepage-implementatie (Material-UI/JSS componenten). Bedoeld als basis voor toekomstige interne applicaties.

## 1. Kleuren

### Merkgroen (primair)
- Primary Dark Green — `#115013` — tekst, headings (h1/h5), links, primaire outline-knoppen, focus states
- Action Green — `#80bd1d` — primaire call-to-action achtergrond (login-knop, header-balk)
- Button hover (dark) — `rgb(11, 56, 13)`

### Merk-accenten (uit het PLUS-blad-logo)
- Paars — `#554DA7`
- Bosgroen — `#227647`
- Rood — `#E3131D`
- (Spinner-variant, iets lichter) — Groen `#7fbd23`, Rood `#e01e19`, Paars `#6555b3`, Groen `#017f38`

Gebruik: uitsluitend als brand-accent (logo, laad-indicator, illustratieve momenten) — niet als UI-actiekleur.

### Semantisch
- Error / invalid — `#dd350d` (tekst/rand), `#f44336` (Material error)
- Disabled tekst — `rgba(0,0,0,0.26)`
- Disabled achtergrond — `rgba(0,0,0,0.12)`

### Neutralen
- Tekst (donker) — `#333333`
- Tekst (gedempt) / placeholder / labels — `#999999`
- Tekst secundair — `#6c6c6c`
- Scheidingslijnen — `#d8d8d8`
- Achtergrond — `#ffffff`

## 2. Typografie

**Lettertype:** `Gotham, 'Open Sans', sans-serif` (Gotham primair; Open Sans als webfallback wordt zelf gehost). Iconen via het `Material Icons` icoonlettertype.

**Schaal (gebaseerd op bestaande componenten):**
- Titel (h1 / pagina-titel) — 22–24px, bold, line-height 30px
- Subtitel — 16px, regular/semibold, line-height 24px
- Body — 16px, regular, line-height 24px
- Body small / caption — 14px, regular, line-height 22px
- Micro / helper — 12px

Tekstkleur volgt de neutrale schaal hierboven; koppen en links in Primary Dark Green.

## 3. Knoppen

- **Vorm:** asymmetrische afgeronde hoek — `border-radius: 24px 24px 24px 4px` (herkenbaar "spraakwolk"-silhouet, kenmerkend voor PLUS-knoppen)
- **Hoogte:** 48px, volledige breedte in formulieren
- **Primair (contained):** achtergrond Action Green `#80bd1d`, tekst wit, geen schaduw in rust
- **Outlined:** rand 1px Primary Dark Green, tekst Primary Dark Green, achtergrond wit
- **Tekstknop (text):** geen achtergrond/rand, tekst Primary Dark Green
- **Disabled:** achtergrond `rgba(0,0,0,0.12)`, tekst `rgba(0,0,0,0.26)`
- **Transitions:** opacity/box-shadow 250ms cubic-bezier(0.4,0,0.2,1)

## 4. Formuliervelden

- **Hoogte:** 56px
- **Radius:** 8px
- **Rand (rust):** 1px `#999999`; hover 2px `rgba(0,0,0,.87)`; focus 1px `#333333`; error `#f44336`
- **Label:** zwevend (floating label), 16px rust → 12px bij focus/gevuld, kleur `#999999` → Primary Dark Green bij focus
- **Errortekst:** 14px, `#dd350d`, marge 4px boven veld

## 5. Vorm & verhoging (radius/elevation)

- **Kaarten / containers:** 4px radius, subtiele schaduw `0 1px 3px rgba(0,0,0,.2), 0 1px 1px rgba(0,0,0,.14), 0 2px 1px -1px rgba(0,0,0,.12)`
- **Header-balk:** geen radius, schaduw `2px 1px 6px 0 rgba(51,51,51,.2)`, achtergrond Action Green
- **Tooltips:** 4px radius, achtergrond `rgba(97,97,97,.9)`, tekst wit 10px

## 6. Spacing

Basiseenheid ~8px. Veelvoorkomende waarden: 4 / 8 / 16 / 24 / 32px voor marges tussen form-elementen en secties. Content-breedte gemaximeerd op ~570px voor formulier/tekstblokken op desktop.

## 7. Iconografie

Material Icons (24px standaard, 1.25rem klein / 2.1875rem groot). Kleur volgt tekstkleur (`currentColor`) of semantische kleur (primary/error/disabled).

## 8. Logo

Blad-vormig woordmerk "PLUS", wit op groene ondergrond in gebruik op de login-header. Het blad-icoon bestaat uit 4 kwadranten in de merkaccentkleuren (paars, rood, bosgroen, wit/groen-tint) — zie `PLUS_logo.svg`.

## 9. Toepassing in nieuwe apps

- Gebruik Primary Dark Green voor tekst-acties/links, Action Green uitsluitend voor primaire CTA-knoppen.
- Reserveer de blad-accentkleuren (paars/rood/bosgroen) voor merkmomenten, niet voor reguliere UI-status (gebruik semantische rood voor errors).
- Houd de karakteristieke knopvorm (24/24/24/4 radius) aan voor herkenbaarheid.
- Formulieren: 56px velden, 8px radius, zwevend label — consistent houden voor UX-continuïteit.
