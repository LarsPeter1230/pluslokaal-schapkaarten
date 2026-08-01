Welkom bij de vernieuwingen van **PLUSLokaal**. Hieronder lees je in het kort wat er per versie is
toegevoegd en verbeterd — de nieuwste bovenaan. Heb je een idee of mis je iets? Laat het weten via het
**?-knopje** rechtsonder.

> **Over de versienummers:** we gebruiken **v‹hoofd›.‹functie›.‹fix›**. Het middelste nummer gaat omhoog
> bij **nieuwe functies**, het laatste nummer bij **verbeteringen en opgeloste puntjes**.

## versie 2.14.1 — Zeldzame 500-fout verholpen · 31 juli 2026

- **Willekeurige "Internal Server Error" opgelost** *(31 juli, 18:39)* — een zeldzame technische race (bij het
  verwerken van de domeinnaam) kon af en toe een 500-fout op een pagina geven. Structureel verholpen; pagina's
  laden nu betrouwbaar.

## versie 2.14.0 — Winkelpakket-accounts beheren + foutmeldingen · 30 juli 2026

- **Beheer je eigen pluslokaal.nl-accounts** *(30 juli, 01:00)* — onder **Beheer → Winkelpakket-accounts**
  stel je nu zelf de accounts in waarmee winkelpakketten op de achtergrond worden opgehaald (handig als een
  wachtwoord verandert). Je kunt er tot **6** instellen; wachtwoorden worden **versleuteld** bewaard.
- **Meer accounts = sneller downloaden** — elk account krijgt een eigen download-worker met een eigen
  winkelmandje, dus met meer accounts worden meer afdelingen **tegelijk** opgehaald. Wijzig je de accounts,
  dan worden de download-workers automatisch met de nieuwe gegevens vernieuwd.
- **Mail bij een mislukte sync/download** — per superadmin in te stellen (aan/uit) of 'ie een e-mail krijgt
  wanneer het ophalen of synchroniseren van winkelpakketten misgaat.

## versie 2.13.6 — Verdwenen weekpakket-kaarten netjes afgehandeld · 27 juli 2026

- **"Niet meer beschikbaar" i.p.v. blijven laden** *(27 juli, 00:19)* — staat een winkelpakket-kaart niet meer
  op pluslokaal.nl, dan wordt 'ie voortaan gemarkeerd als **niet meer beschikbaar**: je ziet een duidelijk
  label in Winkelpakketten, de app probeert 'm niet meer eindeloos te downloaden, en een download **blokkeert
  niet meer volledig** als er een paar kaarten ontbreken — de rest wordt gewoon opgehaald. Kaarten die al op
  onze server staan blijven altijd gewoon werken.

## versie 2.13.5 — Sneller & betrouwbaarder laden · 26 juli 2026

- **Foto's, voorbeelden en opmaak laden nu in één keer** *(26 juli, 23:45)* — de app laat de browser (en
  Cloudflare) statische bestanden nu netjes **onthouden/cachen**, in plaats van ze bij elk bezoek opnieuw op
  te halen. Daardoor is de kans weg dat één hapering de pagina ongestyled laat of thumbnails laat missen —
  het laadt sneller en in één keer goed. (Kaartvoorbeelden worden ook niet meer bij elke pagina-load opnieuw
  door de server gecontroleerd.)
- **Winkelpakketten opgeschoond** — week 28 is volledig verwijderd (metadata, PDF's en thumbnails; ~1,1 GB
  vrijgemaakt). Een week die eenmaal is gedownload blijft gewoon werken, ook als 'ie van pluslokaal.nl verdwijnt.

## versie 2.13.4 — Actie én tip mixen op één SK Maxi-vel · 25 juli 2026

- **Per ¼-kaart kiezen: actie of tip** *(25 juli, 22:41)* — op een **SK Maxi** (4 kaarten op 1 A4) kun je nu
  per kaart apart kiezen of het een **actiekaart** of een **tip-kaart** is. Zo maak je bijvoorbeeld 3 actie- +
  1 tip-kaart (of elke andere mix) op één vel. Klik op **Kaart 1–4**, kies het type, en de rest blijft staan.

## versie 2.13.3 — Merk slim overnemen · 22 juli 2026

- **Merk komt nu automatisch in het Merk-veld** *(22 juli, 14:55)* — bij het overnemen van een plus.nl-product
  wordt het **merk** herkend en apart in **Merk** gezet, de rest in **Koptekst**. Slim genoeg voor lastige
  gevallen: *"Biologisch PLUS Halfvolle melk"* → merk **PLUS**, en *"PLUS Korenlanders Boeren tijger"* →
  merk **PLUS Korenlanders**. Klopt een merk een keer niet? Pas 't gewoon aan in de preview.

## versie 2.13.2 — Tip-kaart bewerken + foto overnemen · 22 juli 2026

- **Tip-kaart: overal in de preview typen** *(22 juli, 14:30)* — je kunt nu ook op de tip-kaart rechtstreeks
  in het live voorbeeld typen: **verpakking, prijs, land van herkomst en aanvullende tekst** (voorheen kon
  dat alleen bij merk/koptekst/subtekst).
- **Foto met één vinkje overnemen** *(22 juli, 14:36)* — bij een plus.nl-zoekresultaat staat nu ook een
  **"Foto"-vinkje**. Vink 'm aan en klik **Overnemen** → de productfoto komt automatisch op de kaart. Je kunt
  'm daarna nog **verplaatsen en schalen** (of verwijderen met het rode ×). Slepen kan nog steeds ook.

## versie 2.13.1 — Ingelogd blijven écht opgelost · 22 juli 2026

- **Je blijft nu ingelogd na het sluiten/heropenen van de browser** *(22 juli, 14:21)* — er bleek een
  hardnekkig misverstand: je sessie bleef al die tijd gewoon geldig, maar de app stuurde je bij het openen
  van de site **altijd** naar het inlogscherm — óók als je nog ingelogd was. Daardoor léék je uitgelogd. Nu
  ga je bij het openen van pluslokaal.com direct door naar je **dashboard** zodra je sessie nog geldig is.
  Je blijft **een maand** ingelogd; cache wissen of opnieuw inloggen is niet meer nodig.

## versie 2.13.0 — Sneller & klaar voor alle winkels · 22 juli 2026

Een grote prestatie- en stabiliteitsverbetering, zodat de app straks vlot blijft werken met honderden
winkels die tegelijk kaarten maken, printen en downloaden. De tijden hieronder zijn Nederlandse tijd.

- **Veel sneller onder drukte** *(22 juli, 01:15)* — de app draait nu op een professionele meer-proces-server
  die alle rekenkernen van de machine benut. In een test met **800 gelijktijdige gebruikers** daalde de tijd
  om een pagina te openen van ~13 seconden naar ~1 seconde, en ging het tempo waarin kaarten worden opgemaakt
  ruim **3× omhoog**. De machine is daarvoor ook opgewaardeerd naar 12 rekenkernen en 16 GB geheugen.
- **Betrouwbaar printen & downloaden bij drukte** *(22 juli, 01:10)* — de voortgang van printen en van het
  downloaden van winkelpakketten werkt nu correct, ongeacht welke server-worker je verzoek afhandelt.
- **Portaal laadt vlotter** *(21 juli, 22:15)* — pagina's van pluslokaal.nl binnen het **Portaal** laden
  sneller: onderdelen worden parallel opgehaald en door de browser onthouden, zodat navigeren rapper gaat.
- **Live voorbeeld = precies de afdruk** *(21 juli, 21:30)* — het live voorbeeld in de kaart-editor komt nu
  op **elk formaat** exact overeen met wat er geprint wordt, voor zowel **tip- als actiekaarten**.
- **Laadspinner bij opslaan** *(22 juli, 01:28)* — tijdens het opslaan of bijwerken van een kaart zie je nu
  de bekende **PLUS-blaadjes-spinner** in beeld (zoals op het Portaal), zodat duidelijk is dat 'ie bezig is.
- **Zoeken op plus.nl veel sneller** *(22 juli, 01:50)* — de eerste zoekopdracht duurde soms ~20 seconden;
  nu staat er één warme zoek-browser klaar en komen resultaten meestal in **enkele seconden**. Ook zie je
  tijdens het zoeken de **PLUS-laadspinner** (de "kan ~20s duren"-tekst is weg). Zoeken **honderden mensen
  tegelijk**, dan kan dat nu ook: veelgezochte producten komen direct terug en gelijktijdige zoekopdrachten
  naar hetzelfde product worden gebundeld. Resultaten blijven **vers** (max ~1 minuut oud), dus nieuwe
  artikelen, vervangen foto's en gewijzigde prijzen zie je snel.

## versie 2.12.0 — Winkelpakketten slimmer · 21 juli 2026

- **Aantal per kaart**: bij het selecteren stel je nu per kaart in hoeveel afdrukken je wilt — handig als
  je meerdere van dezelfde kaart nodig hebt. Zet je het aantal hoger, dan wordt de kaart automatisch
  geselecteerd.
- **Winkelmandje-overzicht**: via **Bekijk mandje** zie je in één scherm alles wat je hebt gekozen (ook uit
  andere weken/afdelingen) met de aantallen; je kunt daar de aantallen aanpassen of kaarten verwijderen.
- Het **winkelmandje wordt nu direct geleegd** zodra je op downloaden of printen klikt.
- **Zelf-aanvullend**: als een nieuwe week nog niet naar de server is gedownload, gebeurt dat nu **vanzelf** —
  niemand hoeft meer handmatig te downloaden. Loopt er een aanvulling, dan zie je na het inloggen een korte
  melding dat sommige acties even trager kunnen zijn.
- **Opslag duidelijker**: per week zie je nu of die alleen **in cache** staat of echt **gedownload** is. Een
  week **verwijderen** wist nu ook de kaarten zelf, zodat de week ook uit Winkelpakketten verdwijnt.

## versie 2.11.0 — Designer (Bèta) · 21 juli 2026

- Nieuw menu-item **Designer (Bèta)**: ontwerp vrij op een **label** of op **papier** (kies zelf
  formaat en staand/liggend).
- Een **Canva-achtige editor** in PLUS-stijl: linker paneel met **Tekst** (koppen/subkoppen),
  **Elementen** (vormen &amp; iconen), **Uploads**, **Achtergrond** en **Zoek op plus.nl** — met dat
  laatste neem je productfoto, naam en prijs zó over door aan te vinken.
- **Sleep, schaal en draai** alles, met **slimme uitlijn-hulplijnen** die netjes uitlijnen op het midden
  en op andere elementen. Pas **lettertype, grootte, kleur en transparantie** aan (standaard Montserrat).
- Voeg **tabellen** in (rijen/kolommen, koprij, dubbelklik om cellen te typen) en kies uit **honderden
  zoekbare Material Icons** naast de PLUS-iconen.
- **Sleep elementen** uit het linkermenu direct op het blad (naast klikken om toe te voegen).
- Productfoto's van **plus.nl** komen nu in **volle kwaliteit** binnen, met een **laad-animatie** op de
  plek terwijl ze inladen.
- **Meerdere pagina's**, **ongedaan maken/opnieuw** (Ctrl+Z/Y), **zoom** en handige **sneltoetsen**.
- **Printen** rechtstreeks op de labelprinter, of download je (meerpagina-)ontwerp als **PDF**.
- Je ontwerpen worden **automatisch bewaard** en staan overzichtelijk in het Designer-dashboard.

## versie 2.10.0 — Portaal: pluslokaal.nl in ons jasje · 20 juli 2026

- Nieuw menu-item **Portaal**: de vertrouwde **pluslokaal.nl** (jaarkalender, tarieven, campagnes,
  mutatieformulieren, "vraag een opdracht aan" en meer) nu **binnen PLUSLokaal**, in ons design.
- **Automatisch inloggen**: koppel eenmalig je pluslokaal.nl-gegevens (onder **Portaal** of **Mijn
  profiel**); daarna log je op de achtergrond automatisch in. Je wachtwoord wordt **versleuteld**
  bewaard en nooit getoond. Je kunt de gegevens later aanpassen of weer ontkoppelen.
- **Vertrouwde navigatie**: de bekende menu's (Landelijke/Lokale activiteiten, Winkel, E-commerce,
  Social Media, Helpdesk) klappen op hover netjes uit, met een **zoekbalk** en snelkoppelingen naar
  je **winkelmandje**, **bestelgeschiedenis** en **actieoverzicht**.
- **Bestellen werkt gewoon**: items in je winkelmandje leggen, bestellen en verwijderen — het aantal
  bij het mandje-icoon werkt direct bij.
- **Mobiel**: nette **hamburger-menu** met uitklapbare categorieën.
- Alles in **onze PLUS-huisstijl** (knoppen, formulieren, tegels, tabellen) en de content is **altijd
  live** — wijzigingen op pluslokaal.nl zie je meteen, zonder dat er iets hoeft te worden bijgewerkt.
- Een **laad-animatie** (PLUS-blaadjes) laat zien wanneer een pagina nog laadt.

## versie 2.9.1 — Verbeteringen aan kaarten · 17 juli 2026

- **Foto's** die je op een kaart plaatst, staan nu **exact** zo op de afdruk als in het voorbeeld — dezelfde
  plek, grootte en vorm.
- De melding **"leeg · wordt niet geprint"** verdwijnt meteen zodra je iets op die kaart typt.
- Kleine **opmaakcorrecties** aan de nieuwe tip-kaart (kleur, patroon en de prijs netjes in het vak).

## versie 2.9.0 — Lege kaarten besparen inkt · 17 juli 2026

- Op een **SK Maxi-vel** worden **lege kaarten niet meer geprint** — dat scheelt inkt en papier.
- Dit geldt nu voor **actie- én tipkaarten** (scankaarten deden dit al).
- In de editor zie je bij een lege kaart een subtiele hint; je kunt gewoon op alle 4 de kaarten blijven typen.

## versie 2.8.0 — Vernieuwde tip-kaart · 17 juli 2026

- De **tip-kaart** heeft het **nieuwe PLUS-ontwerp** gekregen, op **alle formaten** (SK Mini, SK Maxi,
  A5, A4 en A3 — staand en liggend).
- De **oude layout** is vervallen: je maakt kaarten voortaan altijd in de nieuwe PLUS-huisstijl (de
  keuzeknop tussen oud/nieuw is verdwenen).

## versie 2.7.1 — Rondleiding verbeterd · 16 juli 2026

- De **rondleiding** wijst nu de juiste knoppen aan en heeft een extra stap over het **overnemen van
  productgegevens** van plus.nl.
- Staat de rondleiding aan, dan **start hij voortaan bij elke login** opnieuw — ook als je hem de vorige
  keer had weggeklikt (tot een beheerder hem uitzet).

## versie 2.7.0 — Rondleiding voor nieuwe gebruikers · 16 juli 2026

- Nieuw: een **stapsgewijze rondleiding** die je na het inloggen wegwijs maakt in PLUSLokaal.
- Je krijgt eerst een **welkomstvenster**; met één klik op **Rondleiding starten** loop je stap voor stap
  door het maken van een schapkaart, het printen én de winkelpakketten.
- De uitleg verschijnt **naast de knop waar je moet klikken**, terwijl de rest van het scherm even
  dimt — zo zie je precies waar alles zit.
- Beheerders kunnen de rondleiding **per gebruiker aan- of uitzetten** (bij de gebruikersinstellingen).

## versie 2.6.1 — 4 scankaarten per vel · 16 juli 2026

- Op één SK Maxi-vel kun je nu **4 verschillende scankaarten** maken — precies zoals bij de actiekaarten.
- Je vult elke kaart apart in via **tabbladen** (Kaart 1 t/m 4), met een live voorbeeld van het hele vel.
- Handige knoppen om een kaart **naar alle vier te kopiëren** of **leeg te maken**.

## versie 2.6.0 — Scankaarten in PLUS-stijl · 15 juli 2026

- De **scankaart** ziet er nu precies uit zoals de vertrouwde PLUS-scankaart: een groene "Scan hier"-kaart
  met de producten netjes erop — **4 kaarten op één SK Maxi-vel (A4)**, net als de actiekaarten.
- De **indeling past zich vanzelf aan** het aantal producten aan:
  - 1 product: groot "Scan hier" met de barcode ernaast;
  - 2 producten: "Scan hier" met twee vakjes;
  - 3 of meer: een net raster met "Scan hier" in de hoek.
- Per product tonen we **naam, formaat en barcode** in een helder wit vakje.
- De **editor** is vereenvoudigd: je voegt gewoon producten toe en ziet direct een live voorbeeld van de kaart.

## versie 2.5.0 — Veiliger, met versiegeschiedenis · 15 juli 2026

- **Versienummer in de voettekst** van elke pagina. Klik erop en je komt op deze pagina met alle
  vernieuwingen.
- **Extra beveiliging van accounts en gegevens** achter de schermen: veiligere omgang met
  wachtwoorden en met de bestanden die je maakt, zodat alleen jij en je collega's erbij kunnen.
- Nettere **foutpagina's** wanneer een link niet (meer) bestaat.

## versie 2.4.0 — Reageren op je meldingen · juli 2026

- Je meldingen zijn nu een **gesprek**: het beheer kan **terugreageren** en jij kunt antwoorden.
- Onder het **?-knopje** vind je de tab **Mijn meldingen** met de status van al je meldingen en de
  reacties daarop.
- Nieuwe reacties verschijnen **live** en je ziet een **rood bolletje** op het ?-knopje zodra er een
  antwoord voor je klaarstaat.

## versie 2.3.0 — Feedback & kennisbank · juli 2026

- Nieuw **?-knopje rechtsonder** op elke pagina: meld hier eenvoudig een **probleem, suggestie of idee**.
- Bij het openen wordt **automatisch een schermafbeelding** gemaakt van wat je ziet — handig bij een
  probleem. Je kunt die weghalen of een eigen afbeelding meesturen.
- Een uitgebreide **kennisbank** met uitleg en handleidingen over álle onderdelen van PLUSLokaal,
  makkelijk doorzoekbaar.

## versie 2.2.0 — Vrijblijvend uitproberen · 2026

- Een **demo-account** om PLUSLokaal rustig te leren kennen. Alles werkt als normaal, maar er wordt
  niets echt geprint en de gegevens staan apart.

## versie 2.1.0 — Extra accountbeveiliging · 2026

- **Tweestapsverificatie (2FA)**: naast je wachtwoord kun je je account beveiligen met een code uit een
  app op je telefoon. Voor beheerders staat dit standaard aan.
- **Wachtwoord vergeten?** Je herstelt het nu zelf via een e-mail met een veilige link.

## versie 2.0.0 — Een frisse, snellere omgeving · 2026

- Volledig **vernieuwde uitstraling** in de PLUS-huisstijl, met overzichtelijke menu's.
- **Werkt op elk scherm**: computer, tablet en telefoon.
- Je blijft **ingelogd** tot je zelf uitlogt of je wachtwoord wijzigt.
- Voor beheerders van meerdere winkels: een handige **winkelkiezer** bovenin om snel tussen winkels te
  wisselen.

## versie 1.7.0 — Winkelpakketten · 2026

- De wekelijkse, **kant-en-klare actiepakketten** staan nu in PLUSLokaal. Kies de week en afdeling en
  print of download in één keer alle schapkaarten.

## versie 1.6.0 — Printen in de winkel · 2026

- **Rechtstreeks printen op de winkelprinter** — geen bestanden meer downloaden en versturen.
- Elk formaat rolt **automatisch uit de juiste papierlade**.
- **Meerdere kaarten tegelijk** printen, met **live voortgang** en een knop om te annuleren.

## versie 1.5.0 — Producten opzoeken · 2026

- In de kaart-editor kun je nu **producten opzoeken** en gegevens sneller invullen.

## versie 1.4.0 — Prijslabels · 2026

- Nieuwe module **Labels**: maak **schaplabels/prijskaartjes** met naam, prijs en barcode.
- Houd je eigen **productenlijst** bij en vind eerdere labelopdrachten terug in de **geschiedenis**.

## versie 1.3.0 — Scankaarten · 2026

- Maak **scankaarten** met een scanbare **barcode**, klaar om te printen.

## versie 1.2.0 — E-mail & uitnodigingen · 2025

- **Welkomstmails** en uitnodigingen in de PLUS-huisstijl, zodat nieuwe collega's zelf een wachtwoord
  kunnen instellen.

## versie 1.1.0 — Accounts, winkels & rechten · 2025

- **Gebruikersbeheer** en **filialen (winkels)**.
- **Rollen & rechten**: bepaal per rol (medewerker, ondernemer, beheerder) wat iemand mag.
- Ondernemers beheren hun **eigen team**.

## versie 1.0.0 — De start: schapkaarten · 2025

- De eerste versie van PLUSLokaal: **professionele schapkaarten** maken in álle PLUS-formaten
  (SK Mini, Middel, Maxi, A5, A4 en A3).
- Kies **actiekaart of tipkaart** en de **nieuwe of oude huisstijl**, met alle actievormen
  (prijs, 2e halve prijs, X% korting, X + Y gratis, en meer).
- Een **live voorbeeld** terwijl je typt en een **print-klare PDF** als resultaat.
