Welkom bij de vernieuwingen van **PLUSLokaal**. Hieronder lees je in het kort wat er per versie is
toegevoegd en verbeterd - de nieuwste bovenaan. Heb je een idee of mis je iets? Laat het weten via het
**?-knopje** rechtsonder.

> **Over de versienummers:** we gebruiken **v‹hoofd›.‹functie›.‹fix›**. Het middelste nummer gaat omhoog
> bij **nieuwe functies**, het laatste nummer bij **verbeteringen en opgeloste puntjes**.

## versie 2.35.0 - Winkelprinter aanvragen als er nog geen gekoppeld is · 23 augustus 2026

- **Heeft een winkel nog geen gekoppelde printer?** Dan blijft de knop "Printen op winkelprinter" gewoon
  zichtbaar. Klik je erop, dan opent een venster met de melding dat er nog geen winkelprinter is, met twee
  keuzes: de kaarten <strong>samenvoegen en downloaden</strong>, of een <strong>koppeling aanvragen</strong>.
- Het aanvraagformulier vraagt om naam, contact-e-mail en telefoonnummer. De aanvraag gaat naar de
  beheerder, en de aanvrager krijgt automatisch een bevestigingsmail (in PLUS-huisstijl) met uitleg over
  de vervolgstappen, de voordelen (rechtstreeks op de juiste printer, automatisch de juiste lade voor A4,
  SK Maxi en A3) en dat een Raspberry Pi of een oud werkstation gebruikt kan worden.

## versie 2.34.2 - Pi-image weer snel en betrouwbaar (kiosk standaard uit) · 23 augustus 2026

- **De kioskweergave zit niet meer standaard in de image.** Die maakte de eerste boot zwaar en kon 'm
  laten vastlopen, waardoor de Pi soms niet bereikbaar was. De image is nu weer slank: agent + printers
  starten snel en de webinterface is direct op het IP te bereiken.
- De webinterface op afstand (vanuit PLUSLokaal) blijft gewoon werken - daarvoor is geen scherm op de
  Pi nodig.

## versie 2.34.1 - Gebruiker of winkel aanmaken in de nieuwe stijl · 23 augustus 2026

- **Een gebruiker of winkel aanmaken gebeurt nu in een venster op dezelfde pagina**, in de nieuwe stijl -
  je wordt niet meer naar een oud scherm gestuurd.
- In het venster "Nieuwe gebruiker" kun je bij **Filiaal** gewoon typen om snel de juiste winkel te
  vinden (net als de winkelfilter).

## versie 2.34.0 - Gebruikers- en winkelbeheer samengevoegd tot één pagina · 23 augustus 2026

- **Gebruikers en filialen staan nu samen op één overzichtelijke pagina** (Beheer → Gebruikers &amp; winkels),
  met twee tabbladen: **Gebruikers** en **Winkels**.
- Bij **Gebruikers** typ je in het winkelveld om snel op een filiaal te filteren (of kies uit de lijst),
  plus filteren op rol en zoeken op naam/e-mail. De winkel staat als kolom bij elke gebruiker.
- Bij **Winkels** zoek je op naam of nummer; elke winkelkaart toont het aantal medewerkers, ondernemers
  en de status van de Print-agent, en opent met één klik het filiaal.

## versie 2.33.2 - Print-agents werken zichzelf direct bij · 23 augustus 2026

- **Print-agents updaten nu meteen** zodra de server een nieuwere versie aanbiedt, in plaats van pas bij
  de 6-uurs-controle. Zo staat een nieuwe functie (zoals de webinterface op afstand) snel op alle winkels.
- De knop "Webinterface op afstand openen" toont een duidelijke "agent bijwerken"-melding als een winkel
  nog een te oude agent draait (vanaf v1.4.0 werkt de tunnel), in plaats van een lange time-out.

## versie 2.33.1 - Directe-IP-winkels (PLUS Koelhuis) tonen weer al hun instellingen · 23 augustus 2026

- Winkels die **zonder** Print-agent printen (zoals PLUS Koelhuis) tonen op het tabblad Printers weer de
  volledige directe-IP-instellingen (printernaam, IP en poort van de label- en winkelprinter). Bij winkels
  met een Print-agent blijven die velden verborgen, want daar kies je de printers op de PA zelf.

## versie 2.33.0 - Webinterface op afstand, tabs op de filiaalpagina en uitgebreidere PA · 23 augustus 2026

- **Webinterface van de Pi/mini-pc op afstand openen vanuit PLUSLokaal**: in Beheer → Filialen staat nu
  een knop "Webinterface op afstand openen". De agent tunnelt zijn interface veilig via de bestaande
  uitgaande verbinding - geen open poorten in de winkel, geen RMM nodig. (Werkt vanaf agent v1.3.0; oudere
  agents vragen eerst om bijwerken.)
- **De filiaalpagina is opnieuw ingedeeld met echte tabbladen**: Printers, Print-agent en
  Filiaal-instellingen. Veel overzichtelijker.
- **Geen losse IP-instellingen meer**: omdat de winkels via de Print-agent printen, kies je de printers
  op de PA. Op de filiaalpagina stel je alleen nog in hoe een label eruitziet en welke lade elk
  kaartformaat gebruikt.
- **De PA-webinterface heeft uitgebreidere instellingen**: een Systeem-kaart (winkel, verbinding,
  hostnaam, IP-adres, versie, aantal jobs), een teller van gevonden USB-printers met verversknop, en een
  instelbaar standaard aantal kopieën.
- **Webinterface ook zichtbaar via RMM en op een aangesloten scherm**: nieuwe images bevatten een
  kioskweergave die de webinterface toont (zodat RMM remote-desktop en een lokaal scherm 'm laten zien).

## versie 2.32.0 - Print-agent: echt PLUS-logo + direct door na koppelen · 23 augustus 2026

- **De webinterface van de Pi/mini-pc gebruikt nu het echte PLUS-logo en de huisstijl 1:1 zoals
  pluslokaal.com** (links bovenin het PLUS-logo met "Lokaal"), in plaats van een nagemaakt logo.
- **Na het koppelen kom je meteen in de volledige instelpagina** (printers koppelen, lades, testen).
  Je hoeft niet eerst apart in te loggen; dat gebeurt automatisch. Bij een volgend bezoek vraagt 'ie
  wel weer om het wachtwoord.
- **Opgelost:** in sommige gevallen bleef de agent na het koppelen op het welkomstscherm hangen omdat
  het weblogin-wachtwoord nog niet was gesynct. Oudere sleutels zonder wachtwoord krijgen er nu
  automatisch een, zodat inloggen altijd werkt.

## versie 2.31.0 - Print-agent ook op mini-pc's (Wyse/Futro/HP/Lenovo) · 23 augustus 2026

- **Naast de Raspberry Pi werkt de print-agent nu ook op goedkope (refurb) mini-pc's** zoals de Dell
  Wyse, Fujitsu Futro of HP/Lenovo thin clients - handig nu Pi's slecht leverbaar zijn.
- Bouw in Beheer met één knop de **mini-pc-installer (USB)**: stick flashen, mini-pc ervan laten opstarten,
  en de machine installeert zichzelf volledig automatisch (let op: de schijf wordt gewist). Daarna is de
  ervaring identiek aan de Pi: IP intypen, sleutel plakken, printers kiezen - en RMM zit er al op.
- Stap 1 in het Print-agent-blok toont nu beide smaken naast elkaar: **Pi (SD-kaart)** en
  **Mini-pc (USB-stick)**.

## versie 2.30.1 - Print-agent-blok overzichtelijker · 23 augustus 2026

- **Het Print-agent-blok in Beheer → Filialen is opgeruimd**: duidelijke kaarten voor "Status & koppeling",
  "Stap 1: Pi-image downloaden" (met een echte, grote downloadknop) en "Stap 2: Flashen & instellen".
  De alternatieven en het RMM-script staan netjes inklapbaar onderaan.

## versie 2.30.0 - Print-agent: kant-en-klaar .img + RMM veilig meegebakken · 23 augustus 2026

- **Kant-en-klaar .img-bestand**: bouw in Beheer met één knop een compleet Pi-image (agent + RMM er al
  in). Flashen via "Gebruik eigen bestand" in Raspberry Pi Imager, Pi aansluiten, en na de eerste boot
  is 'ie bereikbaar op z'n IP. Sleutel plakken en printers kiezen - klaar.
- **De webinterface draait nu ook op poort 80**: gewoon het IP van de Pi intypen is genoeg (ook handig
  als je via RMM verbinding maakt); poort 8080 blijft als reserve werken.
- **De Pi hernoemt zichzelf na koppeling** naar `PA-<winkelnummer>-PLUSLokaal` (PA = Print-Agent), zodat
  je 'm in je netwerk en RMM direct herkent.
- **RMM-script veilig verwerkt**: het volledige installatiescript (met tokens) wordt versleuteld in het
  SD-bestand/image meegebakken en is alleen door ingelogde admins te downloaden - het staat nergens
  publiek.

## versie 2.29.0 - Print-agent: welkomstscherm, PLUS-stijl en beveiligde login · 23 augustus 2026

- **Eén generiek SD-kaart-bestand voor álle winkels** (geen geheimen erin): flash de kaart, sluit de Pi
  aan, en doe de rest ter plekke via de webinterface.
- **Nieuw welkomstscherm op de Pi**: stap 1 is een knop die **eerst naar een nieuwere versie zoekt**,
  stap 2 is de **agent-sleutel plakken** (kopieer 'm uit Beheer → Filialen). Na het koppelen verschijnt
  de winkelnaam bovenin.
- **De webinterface is nu in PLUS-huisstijl én beveiligd met inloggen**: gebruiker `admin` met een
  **sterk wachtwoord dat PLUSLokaal genereert** (zichtbaar in Beheer → Filialen) en dat **automatisch
  naar de Pi synct**. Inclusief uitloggen en een rem op inlogpogingen.
- In Beheer staan sleutel + Pi-login nu overzichtelijk bij elkaar in een kopieer-en-plak-blok.
- **RMM installeert automatisch mee**: plak in Beheer eenmalig het Linux-installatiecommando van je RMM
  (bv. Tactical RMM) en elke nieuwe Pi voert het bij de eerste boot vanzelf uit.

## versie 2.28.1 - Print-agent: SD-kaart in één keer klaar · 23 augustus 2026

- **De Pi is nu al tijdens het flashen volledig voor te bereiden.** Download per winkel een kant-en-klaar
  SD-kaart-bestand (Beheer → Filialen → Print-agent) met de winkel-sleutel er al in, zet het na het
  flashen (Ubuntu Server via Raspberry Pi Imager) op de SD-kaart, en de eerste boot installeert alles
  vanzelf. Bij de winkel hoef je alleen nog naar het IP van de Pi om de printers te kiezen.

## versie 2.28.0 - Print-agent voor winkels (Raspberry Pi) · 23 augustus 2026

- **Elke winkel kan nu direct printen via een Raspberry Pi.** Sluit de label- en/of winkelprinter via USB
  op een Pi aan; de Pi verbindt zelf beveiligd met pluslokaal.com (geen wijzigingen aan de winkel-firewall)
  en print de opdrachten lokaal - inclusief de papierlade per formaat.
- **Beheer → Filialen** heeft per winkel een nieuw blok "Print-agent": sleutel genereren/intrekken,
  online-status, versie en de installatie-opdracht voor op de Pi.
- Op de Pi draait een **nette webinterface** (poort 8080) voor alle instellingen: sleutel, printers,
  lade-koppeling, testknoppen en een log. De agent **werkt zichzelf automatisch bij** via pluslokaal.com.
- Is de agent van een winkel online, dan gebruiken alle bestaande printknoppen (labels, schapkaarten,
  winkelpakketten) automatisch de agent; anders werkt alles zoals voorheen.

## versie 2.27.0 - Winkelpakketten downloaden zonder zip · 23 augustus 2026

- **Geen zip meer bij het downloaden van winkelpakketten.** Kies je kaarten in meerdere formaten, dan
  krijg je nu per formaat gewoon een **losse PDF** direct in je Downloads (bijv. `winkelpakket_A3_liggend_….pdf`,
  `winkelpakket_SK_Maxi_….pdf`). Personeel hoeft dus niets meer uit te pakken - openen en printen.

## versie 2.26.2 - Winkelpakket-voorbeelden: direct én betrouwbaar · 23 augustus 2026

- **Van gedownloade weken is nu élk voorbeeld direct zichtbaar** - ook de SK Maxi-kaarten (die op
  gedeelde vellen staan) worden nu rechtstreeks uit de lokale bestanden geknipt, met naam-verificatie
  zodat nooit de verkeerde kaart getoond wordt.
- **Nog-niet-gedownloade weken blokkeren de app niet meer:** ontbrekende voorbeelden gaan in een rustige
  achtergrondrij en de tegels vullen zichzelf automatisch zodra ze binnen zijn (geen F5 nodig; de rest
  van de app blijft vlot).

## versie 2.26.1 - Wachtwoord-vergeten beschermd tegen misbruik · 23 augustus 2026

- **"Wachtwoord vergeten" heeft nu een limiet** (max. 3 verzoeken per account en 10 per computer per
  kwartier). Zo kan niemand winkels bestoken met reset-mails, nu de winkel-e-mailadressen in de app staan.

## versie 2.26.0 - Alle PLUS-winkels toegevoegd + nette afzenders · 23 augustus 2026

- **Alle PLUS-winkels staan nu als filiaal in de app**, met per winkel een **medewerker-account** op het
  winkel-e-mailadres. Er zijn **nog geen welkomstmails** verstuurd - dat doen we later. Bestaande winkels
  en accounts zijn ongemoeid gelaten.
- Elke winkel kan **op elk moment zelf een wachtwoord instellen** via "Wachtwoord vergeten" (met het
  winkel-e-mailadres).
- **Mails komen niet meer van "no-reply".** Wachtwoord-resets komen van **passwordreset@mail.pluslokaal.com**
  en welkomst-/accountmails van **info@mail.pluslokaal.com**.

## versie 2.25.0 - Winkelpakketten: zelf-herstellend + thumbnails altijd in één keer · 23 augustus 2026

- **Lost zichzelf voortaan automatisch op.** Duikt er in de toekomst een nieuwe formaat-variant op (zoals
  eerder "Dagdeal"), dan koppelt de app die nu vanzelf aan het juiste basisformaat en corrigeert de lokale
  opslag zichzelf bij de eerstvolgende download - je hoeft niets opnieuw te downloaden en er wordt niet
  onnodig live besteld.
- **Afbeeldingen in Winkelpakketten laden nu altijd in één keer goed** (geen F5 meer nodig). Voor weken die
  al op de server staan, worden de voorbeelden rechtstreeks uit de lokale bestanden gemaakt - snel en
  betrouwbaar. Een enkele tegel die toch hapert, probeert zichzelf automatisch opnieuw.

## versie 2.24.5 - Winkelpakketten: gedownloade weken worden nu echt gebruikt · 23 augustus 2026

- **Opgelost: kaarten van al-gedownloade weken plaatsten tóch een nieuwe bestelling.** De "Dagdeal"-kaarten
  (ma/di/zo) zitten bij het printsysteem in dezelfde formaat-PDF als het gewone formaat, maar werden als een
  apart formaat gezien - waardoor de al-gedownloade weken niet herkend werden en er onnodig live besteld werd.
- Nu worden die kaarten juist herkend: **al je op de server gedownloade weken (bv. 35 en 36) komen direct uit
  de lokale opslag**, zonder nieuwe bestelling. Dit werkt met de al aanwezige bestanden - er hoefde niets
  opnieuw gedownload te worden.

## versie 2.24.4 - Eén vrij invulbaar PLUS-sjabloon · 21 augustus 2026

- **Eén PLUS-sjabloon in de "We doen met je mee"-stijl** met vrij invulbare tekst: vervang zelf
  `[product]` en `[naam]`, sleep je foto erin en download. Zo maak je er elk product mee, zonder een
  hele lijst vaste varianten.

## versie 2.24.3 - Tekst schaalt automatisch mee · 21 augustus 2026

- **Lange teksten passen nu altijd netjes.** De kop en de winkelnaam in de PLUS-sjablonen schalen
  automatisch kleiner (en breken af) zodat ook een lange winkelnaam of productnaam binnen het vak blijft,
  zowel in de editor als op de download.

## versie 2.24.2 - Sjabloonteksten duidelijker bewerkbaar · 21 augustus 2026

- **De PLUS-sjablonen zijn volledig bewerkbaar:** elke tekst (kop, "We doen met je mee.", winkelnaam) pas
  je aan door 'm te selecteren en rechts bij **Inhoud** te typen, of door erop te **dubbelklikken**. Een
  handige tooltip wijst je daar nu op.

## versie 2.24.1 - PNG downloaden in de Designer · 21 augustus 2026

- **Nieuwe "PNG"-knop in de Designer** - handig om een social-media post (bijv. een PLUS-sjabloon) als
  afbeelding op te slaan en direct te plaatsen op je lokale social media.

## versie 2.24.0 - PLUS-sjablonen in de Designer · 21 augustus 2026

- **De PLUS social-media sjablonen zitten nu in onze eigen Designer** (voorheen alleen in Canva). Bij een
  nieuw ontwerp kies je de tab **"PLUS-sjabloon"** en pak je een kant-en-klare post uit de campagne
  "We doen met je mee" (aardbeien, asperges, appels, peren, kersen, aardappels, kaas, eieren, Hollandse Nieuwe).
- Je vervangt alleen de **winkelnaam** en sleept je eigen **foto** in het vlak - daarna direct downloaden.
  Alles gebeurt in onze app; Canva is niet meer nodig.

## versie 2.23.0 - Designer op Fabric.js (bèta, opt-in) · 21 augustus 2026

- **Start van de nieuwe Designer op basis van Fabric.js** (open source, MIT). Te proberen door `?fabric=1`
  achter de ontwerp-URL te zetten; de vertrouwde editor blijft voorlopig gewoon de standaard.
- Eén renderer: het canvas in de browser maakt zelf de print-afbeelding (300 DPI), de server pakt die in
  tot PDF of stuurt 'm naar de labelprinter. Zo is het voorbeeld altijd 1:1 met de afdruk.
- Kernfuncties in deze eerste versie: tekst (kop/subkop/body), vormen, foto's uploaden, PLUS-zoek, iconen,
  barcode, achtergrond, verplaatsen/schalen/roteren, lagen, transparantie, undo/redo, zoom, meerdere pagina's,
  voorbeeld, PDF en labelprinten.

## versie 2.22.2 - Designer-voorbeeld verbeterd · 21 augustus 2026

- **Het "Voorbeeld" in de Designer klopt nu altijd en is goed te zien.** Voorheen kon je de vorige versie te
  zien krijgen (het voorbeeld opende voordat je wijziging was opgeslagen) en verscheen het als een kale
  afbeelding in een nieuw tabblad. Nu slaat de app eerst op en toont het voorbeeld in een net venster,
  met bladeren tussen pagina's - exact zoals het geprint/geexporteerd wordt.

## versie 2.22.1 - Tekstuele opschoning · 15 augustus 2026

- Kleine, tekstuele opschoning door de hele app: lange streepjes zijn overal vervangen door gewone
  koppeltekens. Puur cosmetisch, niets aan de werking veranderd.

## versie 2.22.0 - UC-code + actie overnemen met waarschuwing · 15 augustus 2026

- **De UC-code komt nu altijd als "UC-A35" op het label.** Typ je alleen `A35`, dan zet de app er
  automatisch `UC-` voor. (Opbouw: UC + weekdag A=maandag/B=dinsdag/C=woensdag… + weeknummer.)
- **ⓘ-uitleg bij het UC-veld** - houd de muis erop voor de betekenis en een voorbeeld.
- **Staat een product in de aanbieding? Dan kun je in het zoekresultaat "Actie overnemen" aanvinken** -
  je krijgt dan de actieprijs (met de van-prijs doorgestreept) op het label; laat je 'm uit, dan de normale prijs.
- **Neem je een actie over, dan verschijnt een waarschuwing** (in PLUS-huisstijl): let op dat de stickers na
  afloop van de actie vervangen moeten worden als de producten dan nog niet verkocht zijn.

## versie 2.21.0 - Barcode in de zoekresultaten + "per"-fix · 15 augustus 2026

- **De gevonden barcode staat nu meteen in elk zoekresultaat** bij Labels - je ziet 'm dus al vóór je
  "Overnemen" klikt. (Wordt op de achtergrond opgehaald, een paar tegelijk, zodat het vlot blijft.) Geeft
  plus.nl meerdere barcodes, dan zie je ze allebei en kies je bij het overnemen.
- **Opgelost: "per Per fles" op het label.** De prijs-eenheid toont nu netjes één keer "per" (bijv. "per fles"),
  ook als je een product met verpakking "Per fles" van plus.nl overneemt.
- Actieprijzen worden in de zoekresultaten net als bij Schapkaarten getoond (aanbieding in het rood, van-prijs
  doorgestreept) - zichtbaar zodra een product daadwerkelijk in de aanbieding is.

## versie 2.20.2 - Productfoto in de labels-zoek · 15 augustus 2026

- **De productfoto wordt weer getoond in de zoekresultaten** bij Labels (ter herkenning). De foto komt
  níét op het label - bij "Overnemen" worden alleen naam, prijs en barcode overgenomen.

## versie 2.20.1 - Labels-zoek net als bij Schapkaarten · 15 augustus 2026

- **De plus.nl-zoek bij Labels ziet er nu hetzelfde uit als bij Schapkaarten** - met de PLUS-laadanimatie,
  het aantal resultaten en overzichtelijke productkaartjes met een **"Overnemen"**-knop. (Zonder foto en
  zonder vinkjes: bij een label neem je gewoon het hele product over - naam, prijs en de barcode.)

## versie 2.20.0 - Barcode automatisch van plus.nl · 15 augustus 2026

- **Kies je bij Labels een product uit de plus.nl-zoek, dan wordt nu ook de barcode (EAN) automatisch
  opgehaald** - naast naam en prijs. De barcode komt uit de officiële product-informatie van plus.nl.
- **Geeft plus.nl meerdere barcodes voor een product, dan kun je zelf kiezen** welke op het label komt.
- De barcode blijft gewoon aanpasbaar; controleer 'm bij twijfel met een scan (plus.nl kan een andere
  verpakking-GTIN vermelden dan die fysiek op het product staat).

## versie 2.19.0 - Labels maken vernieuwd · 15 augustus 2026

- **Geen batches meer** - je maakt en bewaart een label nu in één keer op deze pagina. De knop "Toevoegen aan
  lijst" en het overzicht "Labels in deze batch" zijn weg.
- **Zoeken gebeurt nu op plus.nl** (dezelfde bron als bij Schapkaarten): kies een product en de **naam en prijs**
  worden overgenomen. *(plus.nl toont geen streepjescode, dus de barcode vul je zelf in - of hij komt uit je eigen
  productenlijst als we het artikel kennen.)*
- **Leeg beginnen** - naam, prijs en voorbeeld worden niet meer standaard ingevuld. Zolang je nog niets typt,
  toont het voorbeeld een demolabel ("PLUS Voorbeeld"); zodra je iets invult, verschijnt meteen jouw eigen label.
- **Nieuw veld "UC-code" (uithaalcode)** - de versheids-/rotatiecode voor groente & fruit (bijv. `A35`: dag +
  weeknummer). Wordt als kleine, leesbare tekst onder de barcode geprint. Optioneel, maximaal 6 tekens.

## versie 2.18.0 - Barcodes & scankaarten 1:1 met PLUS · 15 augustus 2026

- **Barcodes kun je nu ook op tip-kaarten zetten** (voorheen alleen op actiekaarten) - met exact dezelfde
  "Scan hier"-tegels als op de officiële PLUS A3-kaart.
- **Barcodes botsen niet meer met het prijsblok op staande kaarten** (A4/A3 staand): de tegels staan nu
  netjes boven het prijsblok in plaats van eroverheen.
- **Scankaarten hebben nu exact het PLUS-scankaartgroen** (gelijk aan de barcodetegels en de PLUS SK
  Maxi-referentie) - voorheen een tikje te fel/limoen.

## versie 2.17.0 - Kies & Mix-logo op de kaart · 15 augustus 2026

- **Zet je een kaart op "Kies & Mix"? Dan komt nu het echte PLUS "Kies & Mix"-schild rechtsboven op de
  kaart** - 1:1 hetzelfde logo als op de officiële PLUS-kaarten. Werkt op alle formaten (A3, A4, A3 liggend
  en elk vak van een SK Maxi) en zowel in het live voorbeeld als op de afdruk/PDF.
- De **link naar PLUS Kids** is weer uit de menubalk gehaald.

## versie 2.16.0 - Snelkoppeling naar PLUS Kids · 14 augustus 2026

- **Nieuwe link "Kids" in de menubalk** - opent **PLUS Kids** (kids.pluslokaal.com), de speelsite met
  spelletjes voor kinderen, in een nieuw tabblad. Handig om snel op een winkelscherm te openen.

## versie 2.15.2 - Browser-afdrukken netjes geblokkeerd met uitleg · 5 augustus 2026

- **Afdrukken via het browsermenu (⋯ → Afdrukken), Ctrl+P of Bestand → Afdrukken wordt nu tegengehouden.**
  In plaats van een half-gerenderde kaart verschijnt een **duidelijk hulpvenster** dat uitlegt hoe het wél
  moet - met een knop **“Opslaan & printen”** die je meteen naar de winkelprinter of PDF-download brengt.
- Print je toch door de browser heen, dan komt er op papier alleen een **korte instructie** te staan, geen
  onbruikbare kaart.

## versie 2.15.1 - Browser-afdrukken toont alleen de kaart · 5 augustus 2026

- **Ook via het browsermenu (⋯ → Afdrukken) of Bestand → Afdrukken** wordt nu alleen de **kaart** afgedrukt,
  niet de hele webpagina met menu's en velden. Ongeacht hóe je het afdrukken start, je krijgt netjes de kaart.
- De **pagina-oriëntatie** (staand/liggend) volgt automatisch het gekozen kaartformaat.

## versie 2.15.0 - Opslaan & printen vanuit de editor · 5 augustus 2026

- **Nieuwe knop "Opslaan & printen"** in de kaart-editor - sla je kaart op én stuur 'm meteen naar de
  winkelprinter of download 'm, zonder eerst terug naar het dashboard te hoeven.
- **Ctrl + P print nu de kaart, niet de webpagina** - druk je in de editor op Ctrl+P (of ⌘+P op een Mac),
  dan wordt de kaart opgeslagen en verschijnt de vertrouwde printkeuze (winkelprinter of downloaden). Geen
  rommelige uitdraai van het scherm meer.

## versie 2.14.1 - Zeldzame 500-fout verholpen · 31 juli 2026

- **Willekeurige "Internal Server Error" opgelost** *(31 juli, 18:39)* - een zeldzame technische race (bij het
  verwerken van de domeinnaam) kon af en toe een 500-fout op een pagina geven. Structureel verholpen; pagina's
  laden nu betrouwbaar.

## versie 2.14.0 - Winkelpakket-accounts beheren + foutmeldingen · 30 juli 2026

- **Beheer je eigen pluslokaal.nl-accounts** *(30 juli, 01:00)* - onder **Beheer → Winkelpakket-accounts**
  stel je nu zelf de accounts in waarmee winkelpakketten op de achtergrond worden opgehaald (handig als een
  wachtwoord verandert). Je kunt er tot **6** instellen; wachtwoorden worden **versleuteld** bewaard.
- **Meer accounts = sneller downloaden** - elk account krijgt een eigen download-worker met een eigen
  winkelmandje, dus met meer accounts worden meer afdelingen **tegelijk** opgehaald. Wijzig je de accounts,
  dan worden de download-workers automatisch met de nieuwe gegevens vernieuwd.
- **Mail bij een mislukte sync/download** - per superadmin in te stellen (aan/uit) of 'ie een e-mail krijgt
  wanneer het ophalen of synchroniseren van winkelpakketten misgaat.

## versie 2.13.6 - Verdwenen weekpakket-kaarten netjes afgehandeld · 27 juli 2026

- **"Niet meer beschikbaar" i.p.v. blijven laden** *(27 juli, 00:19)* - staat een winkelpakket-kaart niet meer
  op pluslokaal.nl, dan wordt 'ie voortaan gemarkeerd als **niet meer beschikbaar**: je ziet een duidelijk
  label in Winkelpakketten, de app probeert 'm niet meer eindeloos te downloaden, en een download **blokkeert
  niet meer volledig** als er een paar kaarten ontbreken - de rest wordt gewoon opgehaald. Kaarten die al op
  onze server staan blijven altijd gewoon werken.

## versie 2.13.5 - Sneller & betrouwbaarder laden · 26 juli 2026

- **Foto's, voorbeelden en opmaak laden nu in één keer** *(26 juli, 23:45)* - de app laat de browser (en
  Cloudflare) statische bestanden nu netjes **onthouden/cachen**, in plaats van ze bij elk bezoek opnieuw op
  te halen. Daardoor is de kans weg dat één hapering de pagina ongestyled laat of thumbnails laat missen -
  het laadt sneller en in één keer goed. (Kaartvoorbeelden worden ook niet meer bij elke pagina-load opnieuw
  door de server gecontroleerd.)
- **Winkelpakketten opgeschoond** - week 28 is volledig verwijderd (metadata, PDF's en thumbnails; ~1,1 GB
  vrijgemaakt). Een week die eenmaal is gedownload blijft gewoon werken, ook als 'ie van pluslokaal.nl verdwijnt.

## versie 2.13.4 - Actie én tip mixen op één SK Maxi-vel · 25 juli 2026

- **Per ¼-kaart kiezen: actie of tip** *(25 juli, 22:41)* - op een **SK Maxi** (4 kaarten op 1 A4) kun je nu
  per kaart apart kiezen of het een **actiekaart** of een **tip-kaart** is. Zo maak je bijvoorbeeld 3 actie- +
  1 tip-kaart (of elke andere mix) op één vel. Klik op **Kaart 1-4**, kies het type, en de rest blijft staan.

## versie 2.13.3 - Merk slim overnemen · 22 juli 2026

- **Merk komt nu automatisch in het Merk-veld** *(22 juli, 14:55)* - bij het overnemen van een plus.nl-product
  wordt het **merk** herkend en apart in **Merk** gezet, de rest in **Koptekst**. Slim genoeg voor lastige
  gevallen: *"Biologisch PLUS Halfvolle melk"* → merk **PLUS**, en *"PLUS Korenlanders Boeren tijger"* →
  merk **PLUS Korenlanders**. Klopt een merk een keer niet? Pas 't gewoon aan in de preview.

## versie 2.13.2 - Tip-kaart bewerken + foto overnemen · 22 juli 2026

- **Tip-kaart: overal in de preview typen** *(22 juli, 14:30)* - je kunt nu ook op de tip-kaart rechtstreeks
  in het live voorbeeld typen: **verpakking, prijs, land van herkomst en aanvullende tekst** (voorheen kon
  dat alleen bij merk/koptekst/subtekst).
- **Foto met één vinkje overnemen** *(22 juli, 14:36)* - bij een plus.nl-zoekresultaat staat nu ook een
  **"Foto"-vinkje**. Vink 'm aan en klik **Overnemen** → de productfoto komt automatisch op de kaart. Je kunt
  'm daarna nog **verplaatsen en schalen** (of verwijderen met het rode ×). Slepen kan nog steeds ook.

## versie 2.13.1 - Ingelogd blijven écht opgelost · 22 juli 2026

- **Je blijft nu ingelogd na het sluiten/heropenen van de browser** *(22 juli, 14:21)* - er bleek een
  hardnekkig misverstand: je sessie bleef al die tijd gewoon geldig, maar de app stuurde je bij het openen
  van de site **altijd** naar het inlogscherm - óók als je nog ingelogd was. Daardoor léék je uitgelogd. Nu
  ga je bij het openen van pluslokaal.com direct door naar je **dashboard** zodra je sessie nog geldig is.
  Je blijft **een maand** ingelogd; cache wissen of opnieuw inloggen is niet meer nodig.

## versie 2.13.0 - Sneller & klaar voor alle winkels · 22 juli 2026

Een grote prestatie- en stabiliteitsverbetering, zodat de app straks vlot blijft werken met honderden
winkels die tegelijk kaarten maken, printen en downloaden. De tijden hieronder zijn Nederlandse tijd.

- **Veel sneller onder drukte** *(22 juli, 01:15)* - de app draait nu op een professionele meer-proces-server
  die alle rekenkernen van de machine benut. In een test met **800 gelijktijdige gebruikers** daalde de tijd
  om een pagina te openen van ~13 seconden naar ~1 seconde, en ging het tempo waarin kaarten worden opgemaakt
  ruim **3× omhoog**. De machine is daarvoor ook opgewaardeerd naar 12 rekenkernen en 16 GB geheugen.
- **Betrouwbaar printen & downloaden bij drukte** *(22 juli, 01:10)* - de voortgang van printen en van het
  downloaden van winkelpakketten werkt nu correct, ongeacht welke server-worker je verzoek afhandelt.
- **Portaal laadt vlotter** *(21 juli, 22:15)* - pagina's van pluslokaal.nl binnen het **Portaal** laden
  sneller: onderdelen worden parallel opgehaald en door de browser onthouden, zodat navigeren rapper gaat.
- **Live voorbeeld = precies de afdruk** *(21 juli, 21:30)* - het live voorbeeld in de kaart-editor komt nu
  op **elk formaat** exact overeen met wat er geprint wordt, voor zowel **tip- als actiekaarten**.
- **Laadspinner bij opslaan** *(22 juli, 01:28)* - tijdens het opslaan of bijwerken van een kaart zie je nu
  de bekende **PLUS-blaadjes-spinner** in beeld (zoals op het Portaal), zodat duidelijk is dat 'ie bezig is.
- **Zoeken op plus.nl veel sneller** *(22 juli, 01:50)* - de eerste zoekopdracht duurde soms ~20 seconden;
  nu staat er één warme zoek-browser klaar en komen resultaten meestal in **enkele seconden**. Ook zie je
  tijdens het zoeken de **PLUS-laadspinner** (de "kan ~20s duren"-tekst is weg). Zoeken **honderden mensen
  tegelijk**, dan kan dat nu ook: veelgezochte producten komen direct terug en gelijktijdige zoekopdrachten
  naar hetzelfde product worden gebundeld. Resultaten blijven **vers** (max ~1 minuut oud), dus nieuwe
  artikelen, vervangen foto's en gewijzigde prijzen zie je snel.

## versie 2.12.0 - Winkelpakketten slimmer · 21 juli 2026

- **Aantal per kaart**: bij het selecteren stel je nu per kaart in hoeveel afdrukken je wilt - handig als
  je meerdere van dezelfde kaart nodig hebt. Zet je het aantal hoger, dan wordt de kaart automatisch
  geselecteerd.
- **Winkelmandje-overzicht**: via **Bekijk mandje** zie je in één scherm alles wat je hebt gekozen (ook uit
  andere weken/afdelingen) met de aantallen; je kunt daar de aantallen aanpassen of kaarten verwijderen.
- Het **winkelmandje wordt nu direct geleegd** zodra je op downloaden of printen klikt.
- **Zelf-aanvullend**: als een nieuwe week nog niet naar de server is gedownload, gebeurt dat nu **vanzelf** -
  niemand hoeft meer handmatig te downloaden. Loopt er een aanvulling, dan zie je na het inloggen een korte
  melding dat sommige acties even trager kunnen zijn.
- **Opslag duidelijker**: per week zie je nu of die alleen **in cache** staat of echt **gedownload** is. Een
  week **verwijderen** wist nu ook de kaarten zelf, zodat de week ook uit Winkelpakketten verdwijnt.

## versie 2.11.0 - Designer (Bèta) · 21 juli 2026

- Nieuw menu-item **Designer (Bèta)**: ontwerp vrij op een **label** of op **papier** (kies zelf
  formaat en staand/liggend).
- Een **Canva-achtige editor** in PLUS-stijl: linker paneel met **Tekst** (koppen/subkoppen),
  **Elementen** (vormen &amp; iconen), **Uploads**, **Achtergrond** en **Zoek op plus.nl** - met dat
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

## versie 2.10.0 - Portaal: pluslokaal.nl in ons jasje · 20 juli 2026

- Nieuw menu-item **Portaal**: de vertrouwde **pluslokaal.nl** (jaarkalender, tarieven, campagnes,
  mutatieformulieren, "vraag een opdracht aan" en meer) nu **binnen PLUSLokaal**, in ons design.
- **Automatisch inloggen**: koppel eenmalig je pluslokaal.nl-gegevens (onder **Portaal** of **Mijn
  profiel**); daarna log je op de achtergrond automatisch in. Je wachtwoord wordt **versleuteld**
  bewaard en nooit getoond. Je kunt de gegevens later aanpassen of weer ontkoppelen.
- **Vertrouwde navigatie**: de bekende menu's (Landelijke/Lokale activiteiten, Winkel, E-commerce,
  Social Media, Helpdesk) klappen op hover netjes uit, met een **zoekbalk** en snelkoppelingen naar
  je **winkelmandje**, **bestelgeschiedenis** en **actieoverzicht**.
- **Bestellen werkt gewoon**: items in je winkelmandje leggen, bestellen en verwijderen - het aantal
  bij het mandje-icoon werkt direct bij.
- **Mobiel**: nette **hamburger-menu** met uitklapbare categorieën.
- Alles in **onze PLUS-huisstijl** (knoppen, formulieren, tegels, tabellen) en de content is **altijd
  live** - wijzigingen op pluslokaal.nl zie je meteen, zonder dat er iets hoeft te worden bijgewerkt.
- Een **laad-animatie** (PLUS-blaadjes) laat zien wanneer een pagina nog laadt.

## versie 2.9.1 - Verbeteringen aan kaarten · 17 juli 2026

- **Foto's** die je op een kaart plaatst, staan nu **exact** zo op de afdruk als in het voorbeeld - dezelfde
  plek, grootte en vorm.
- De melding **"leeg · wordt niet geprint"** verdwijnt meteen zodra je iets op die kaart typt.
- Kleine **opmaakcorrecties** aan de nieuwe tip-kaart (kleur, patroon en de prijs netjes in het vak).

## versie 2.9.0 - Lege kaarten besparen inkt · 17 juli 2026

- Op een **SK Maxi-vel** worden **lege kaarten niet meer geprint** - dat scheelt inkt en papier.
- Dit geldt nu voor **actie- én tipkaarten** (scankaarten deden dit al).
- In de editor zie je bij een lege kaart een subtiele hint; je kunt gewoon op alle 4 de kaarten blijven typen.

## versie 2.8.0 - Vernieuwde tip-kaart · 17 juli 2026

- De **tip-kaart** heeft het **nieuwe PLUS-ontwerp** gekregen, op **alle formaten** (SK Mini, SK Maxi,
  A5, A4 en A3 - staand en liggend).
- De **oude layout** is vervallen: je maakt kaarten voortaan altijd in de nieuwe PLUS-huisstijl (de
  keuzeknop tussen oud/nieuw is verdwenen).

## versie 2.7.1 - Rondleiding verbeterd · 16 juli 2026

- De **rondleiding** wijst nu de juiste knoppen aan en heeft een extra stap over het **overnemen van
  productgegevens** van plus.nl.
- Staat de rondleiding aan, dan **start hij voortaan bij elke login** opnieuw - ook als je hem de vorige
  keer had weggeklikt (tot een beheerder hem uitzet).

## versie 2.7.0 - Rondleiding voor nieuwe gebruikers · 16 juli 2026

- Nieuw: een **stapsgewijze rondleiding** die je na het inloggen wegwijs maakt in PLUSLokaal.
- Je krijgt eerst een **welkomstvenster**; met één klik op **Rondleiding starten** loop je stap voor stap
  door het maken van een schapkaart, het printen én de winkelpakketten.
- De uitleg verschijnt **naast de knop waar je moet klikken**, terwijl de rest van het scherm even
  dimt - zo zie je precies waar alles zit.
- Beheerders kunnen de rondleiding **per gebruiker aan- of uitzetten** (bij de gebruikersinstellingen).

## versie 2.6.1 - 4 scankaarten per vel · 16 juli 2026

- Op één SK Maxi-vel kun je nu **4 verschillende scankaarten** maken - precies zoals bij de actiekaarten.
- Je vult elke kaart apart in via **tabbladen** (Kaart 1 t/m 4), met een live voorbeeld van het hele vel.
- Handige knoppen om een kaart **naar alle vier te kopiëren** of **leeg te maken**.

## versie 2.6.0 - Scankaarten in PLUS-stijl · 15 juli 2026

- De **scankaart** ziet er nu precies uit zoals de vertrouwde PLUS-scankaart: een groene "Scan hier"-kaart
  met de producten netjes erop - **4 kaarten op één SK Maxi-vel (A4)**, net als de actiekaarten.
- De **indeling past zich vanzelf aan** het aantal producten aan:
  - 1 product: groot "Scan hier" met de barcode ernaast;
  - 2 producten: "Scan hier" met twee vakjes;
  - 3 of meer: een net raster met "Scan hier" in de hoek.
- Per product tonen we **naam, formaat en barcode** in een helder wit vakje.
- De **editor** is vereenvoudigd: je voegt gewoon producten toe en ziet direct een live voorbeeld van de kaart.

## versie 2.5.0 - Veiliger, met versiegeschiedenis · 15 juli 2026

- **Versienummer in de voettekst** van elke pagina. Klik erop en je komt op deze pagina met alle
  vernieuwingen.
- **Extra beveiliging van accounts en gegevens** achter de schermen: veiligere omgang met
  wachtwoorden en met de bestanden die je maakt, zodat alleen jij en je collega's erbij kunnen.
- Nettere **foutpagina's** wanneer een link niet (meer) bestaat.

## versie 2.4.0 - Reageren op je meldingen · juli 2026

- Je meldingen zijn nu een **gesprek**: het beheer kan **terugreageren** en jij kunt antwoorden.
- Onder het **?-knopje** vind je de tab **Mijn meldingen** met de status van al je meldingen en de
  reacties daarop.
- Nieuwe reacties verschijnen **live** en je ziet een **rood bolletje** op het ?-knopje zodra er een
  antwoord voor je klaarstaat.

## versie 2.3.0 - Feedback & kennisbank · juli 2026

- Nieuw **?-knopje rechtsonder** op elke pagina: meld hier eenvoudig een **probleem, suggestie of idee**.
- Bij het openen wordt **automatisch een schermafbeelding** gemaakt van wat je ziet - handig bij een
  probleem. Je kunt die weghalen of een eigen afbeelding meesturen.
- Een uitgebreide **kennisbank** met uitleg en handleidingen over álle onderdelen van PLUSLokaal,
  makkelijk doorzoekbaar.

## versie 2.2.0 - Vrijblijvend uitproberen · 2026

- Een **demo-account** om PLUSLokaal rustig te leren kennen. Alles werkt als normaal, maar er wordt
  niets echt geprint en de gegevens staan apart.

## versie 2.1.0 - Extra accountbeveiliging · 2026

- **Tweestapsverificatie (2FA)**: naast je wachtwoord kun je je account beveiligen met een code uit een
  app op je telefoon. Voor beheerders staat dit standaard aan.
- **Wachtwoord vergeten?** Je herstelt het nu zelf via een e-mail met een veilige link.

## versie 2.0.0 - Een frisse, snellere omgeving · 2026

- Volledig **vernieuwde uitstraling** in de PLUS-huisstijl, met overzichtelijke menu's.
- **Werkt op elk scherm**: computer, tablet en telefoon.
- Je blijft **ingelogd** tot je zelf uitlogt of je wachtwoord wijzigt.
- Voor beheerders van meerdere winkels: een handige **winkelkiezer** bovenin om snel tussen winkels te
  wisselen.

## versie 1.7.0 - Winkelpakketten · 2026

- De wekelijkse, **kant-en-klare actiepakketten** staan nu in PLUSLokaal. Kies de week en afdeling en
  print of download in één keer alle schapkaarten.

## versie 1.6.0 - Printen in de winkel · 2026

- **Rechtstreeks printen op de winkelprinter** - geen bestanden meer downloaden en versturen.
- Elk formaat rolt **automatisch uit de juiste papierlade**.
- **Meerdere kaarten tegelijk** printen, met **live voortgang** en een knop om te annuleren.

## versie 1.5.0 - Producten opzoeken · 2026

- In de kaart-editor kun je nu **producten opzoeken** en gegevens sneller invullen.

## versie 1.4.0 - Prijslabels · 2026

- Nieuwe module **Labels**: maak **schaplabels/prijskaartjes** met naam, prijs en barcode.
- Houd je eigen **productenlijst** bij en vind eerdere labelopdrachten terug in de **geschiedenis**.

## versie 1.3.0 - Scankaarten · 2026

- Maak **scankaarten** met een scanbare **barcode**, klaar om te printen.

## versie 1.2.0 - E-mail & uitnodigingen · 2025

- **Welkomstmails** en uitnodigingen in de PLUS-huisstijl, zodat nieuwe collega's zelf een wachtwoord
  kunnen instellen.

## versie 1.1.0 - Accounts, winkels & rechten · 2025

- **Gebruikersbeheer** en **filialen (winkels)**.
- **Rollen & rechten**: bepaal per rol (medewerker, ondernemer, beheerder) wat iemand mag.
- Ondernemers beheren hun **eigen team**.

## versie 1.0.0 - De start: schapkaarten · 2025

- De eerste versie van PLUSLokaal: **professionele schapkaarten** maken in álle PLUS-formaten
  (SK Mini, Middel, Maxi, A5, A4 en A3).
- Kies **actiekaart of tipkaart** en de **nieuwe of oude huisstijl**, met alle actievormen
  (prijs, 2e halve prijs, X% korting, X + Y gratis, en meer).
- Een **live voorbeeld** terwijl je typt en een **print-klare PDF** als resultaat.
