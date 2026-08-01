# -*- coding: utf-8 -*-
"""Startinhoud voor de kennisbank (kan daarna door superadmins bewerkt/uitgebreid worden).
Alleen artikelen met een nog niet-bestaand slug worden bij het opstarten aangemaakt."""

ARTICLES = [
    # ── AAN DE SLAG ───────────────────────────────────────────────────────────
    {
        'slug': 'welkom', 'category': 'Aan de slag', 'icon': 'fa-hand-sparkles',
        'sort_index': 1, 'title': 'Welkom bij PLUSLokaal',
        'summary': 'Wat is PLUSLokaal en wat kun je ermee? Een korte rondleiding.',
        'body': """# Welkom bij PLUSLokaal

PLUSLokaal is jouw gereedschap om **snel professionele, PLUS-huisstijl materialen** te maken en te
printen voor in de winkel. Alles zit op één plek en werkt vanuit de browser — je hoeft niets te
installeren.

## Wat kun je maken?

- **Schapkaarten** — de bekende actie- en tipkaarten (nieuwe én oude huisstijl) in alle formaten,
  van SK Mini tot A3.
- **Scankaarten** — kaarten met een scanbare barcode.
- **Labels** — prijskaartjes/labels voor op het schap, met barcode en prijs.
- **Winkelpakketten** — de kant-en-klare wekelijkse actiepakketten, direct te printen.

## De belangrijkste plekken

- Bovenin vind je de **menubalk**: Schapkaarten, Scankaarten, Labels en (voor beheerders) **Beheer**.
- Rechtsboven staat je **account** (profiel, uitloggen) en — voor superadmins — de **winkelkiezer**.
- Rechtsonder staat het ronde **?-knopje**: daarmee geef je feedback of open je deze kennisbank.

> **Tip:** weet je even niet hoe iets werkt? Klik rechtsonder op de **?** en kies *Kennisbank openen*.
> Bijna elk onderwerp staat hier uitgelegd.

Veel plezier! Mis je iets in deze uitleg? Laat het weten via het feedback-knopje — dan vullen we het aan.
""",
    },
    {
        'slug': 'inloggen-account', 'category': 'Aan de slag', 'icon': 'fa-right-to-bracket',
        'sort_index': 2, 'title': 'Inloggen en je account',
        'summary': 'Hoe log je in, wat als je je wachtwoord kwijt bent, en je profiel bijwerken.',
        'body': """# Inloggen en je account

## Inloggen

Je logt in met je **e-mailadres** en wachtwoord op de inlogpagina. Je e-mailadres is hoofdletter-
ongevoelig — `Naam@Voorbeeld.nl` en `naam@voorbeeld.nl` werken allebei.

Standaard blijf je **ingelogd** totdat je zelf uitlogt of je wachtwoord wijzigt. Wil je op een gedeelde
computer werken? Vink dan bij het inloggen *"aangemeld blijven"* uit — dan word je uitgelogd zodra je
de browser sluit.

## Wachtwoord vergeten

1. Klik op de inlogpagina op **Wachtwoord vergeten**.
2. Vul je e-mailadres in. Je ontvangt een e-mail met een link.
3. Via die link stel je een **nieuw wachtwoord** in. De link is 7 dagen geldig.

Krijg je geen e-mail? Kijk in je **spam-map**. Blijft het uit, vraag dan je ondernemer of beheerder om
je account te resetten.

## Je profiel bijwerken

Klik rechtsboven op je naam → **Mijn profiel**. Daar pas je aan:

- je **naam** en **profielfoto** (avatar);
- je **wachtwoord**;
- je **tweefactor-beveiliging** (zie het artikel *Tweefactor (2FA)*).

> **Let op:** wijzig je je wachtwoord, dan word je op alle andere apparaten automatisch uitgelogd.
Dat is een beveiligingsmaatregel.
""",
    },
    {
        'slug': 'winkel-kiezen', 'category': 'Aan de slag', 'icon': 'fa-store',
        'sort_index': 3, 'title': 'In welke winkel werk je?',
        'summary': 'Je filiaal, en (voor superadmins) de winkelkiezer rechtsboven.',
        'body': """# In welke winkel werk je?

Alles wat je maakt en print hoort bij een **winkel (filiaal)**. Zo komen kaarten en labels bij de
juiste winkel terecht en gaat printen naar de juiste printer.

## Medewerkers en ondernemers

Je bent gekoppeld aan je eigen winkel. Je hoeft niets te kiezen — je werkt automatisch in jouw filiaal.

## Superadmins: de winkelkiezer

Ben je superadmin, dan zie je rechtsboven een **winkelkiezer** (met een winkel-icoon). Daarmee bepaal je
in welke winkel je op dat moment werkt:

- Klik op de kiezer en **zoek** op winkelnaam of winkelnummer.
- Kies **"Alle winkels (overzicht)"** om over alle winkels heen te kijken.
- Zolang je nog geen winkel koos, staat er een randje om de knop als hint.

> **Belangrijk:** print- en opslagacties gebruiken de winkel die hier gekozen is. Kies dus eerst de
juiste winkel voordat je iets print.
""",
    },
    {
        'slug': 'feedback-geven', 'category': 'Aan de slag', 'icon': 'fa-comment-dots',
        'sort_index': 4, 'title': 'Feedback geven (het ?-knopje)',
        'summary': 'Een probleem, suggestie of idee doorgeven — met of zonder screenshot.',
        'body': """# Feedback geven

Rechtsonder in het scherm staat altijd een rond **?-knopje**. Beweeg je muis erover (of tik erop) en
het klapt open. Dit is dé plek om iets door te geven:

- **Probleem** — er gaat iets mis of werkt niet zoals verwacht.
- **Suggestie** — iets kan handiger of duidelijker.
- **Idee** — iets nieuws dat je graag zou willen.

## Zo werkt het

1. Klik rechtsonder op de **?**.
2. Kies het **type** (probleem, suggestie of idee).
3. Schrijf in je eigen woorden wat je wilt melden. Hoe concreter, hoe beter we je kunnen helpen.
4. **Screenshot:** zodra je het venster opent, maken we automatisch een schermafbeelding van wat jij op
   dat moment ziet. Handig bij een probleem — dan zien we meteen waar het over gaat.
   - Wil je die niet meesturen? Klik op **Verwijderen**.
   - Wil je een eigen afbeelding sturen? Klik op **Uploaden** en kies een bestand.
5. Klik op **Versturen**. Klaar!

Je melding komt binnen bij de beheerders. Zij kunnen zien wat je meldde, van welke pagina, en op welk
moment — zodat ze er goed mee aan de slag kunnen.

> **Tip:** onderaan het feedback-venster zit ook een knop **Kennisbank openen** — misschien staat je
antwoord er al.
""",
    },

    # ── MODULES ────────────────────────────────────────────────────────────────
    {
        'slug': 'schapkaarten-maken', 'category': 'Modules', 'icon': 'fa-tags',
        'sort_index': 10, 'title': 'Schapkaarten maken',
        'summary': 'Van formaat kiezen tot printen: de complete werkwijze voor schapkaarten.',
        'body': """# Schapkaarten maken

Schapkaarten zijn de bekende **actie- en tipkaarten** voor op het schap. Je maakt ze via het menu
**Schapkaarten**.

## Stap voor stap

1. Ga naar **Schapkaarten** in de menubalk. Je ziet je eerder gemaakte kaarten.
2. Klik op **Nieuwe kaart**.
3. Kies een **formaat**. Beschikbare maten:
   - **SK Mini** (75 × 88 mm)
   - **SK Middel** (270 × 70 mm)
   - **SK Maxi** — 4 kaarten op één A4 (297 × 210 mm)
   - **A5, A4, A3** (staand) en **A3 liggend**
4. Kies **Kaarttype**: *Actiekaart* of *Tip*.
5. Kies de **Layout**: *Nieuwe* of *Oude* huisstijl.
6. Vul de velden in (merk, product, prijs, actie…). Je ziet **live** een voorbeeld van de kaart.
7. Klik op **Opslaan**. De kaart staat nu in je overzicht en is klaar om te **printen** of te
   **downloaden** als PDF.

## Soorten acties

Bij een actiekaart kies je het **actietype**, bijvoorbeeld:

- vaste **prijs**;
- **halve prijs**;
- **X% korting** of **X euro korting**;
- **X + Y gratis**, **X voor Y**, of **X halen Y betalen**.

De kaart past de opmaak automatisch aan het gekozen actietype aan.

> **SK Maxi = 4 kaarten op één A4.** Handig voor de scheurblokjes bij het schap. Vul de vier vakjes
afzonderlijk in.

Zie ook: *De kaart-editor uitgelegd* en *Printen naar de winkelprinter*.
""",
    },
    {
        'slug': 'kaart-editor', 'category': 'Modules', 'icon': 'fa-pen-ruler',
        'sort_index': 11, 'title': 'De kaart-editor uitgelegd',
        'summary': 'Alle knoppen in de editor: type, layout, velden en het live voorbeeld.',
        'body': """# De kaart-editor uitgelegd

De editor toont links de **invoervelden** en rechts een **live voorbeeld** dat precies laat zien hoe de
kaart eruit komt te zien.

## De twee schakelaars

- **Kaarttype** — wissel tussen *Actiekaart* en *Tip*. Een tipkaart heeft geen actiemechanisme; je vult
  gewoon een prijs in.
- **Layout** — wissel tussen de *Nieuwe* en de *Oude* PLUS-huisstijl. Beide zien er anders uit; kies wat
  bij de actie past.

Samen geven die vier combinaties (nieuw/oud × actie/tip) vier verschillende ontwerpen.

## De velden

Vul in wat op de kaart moet komen: **merk**, **omschrijving/product**, **verpakking**, **prijs** en
(bij een actie) de **actiegegevens**. Terwijl je typt, ververst het voorbeeld direct.

## Opslaan en verder

- **Opslaan** bewaart de kaart in je overzicht. Je kunt hem later weer openen en aanpassen.
- Vanuit het overzicht kun je een kaart **printen** of **downloaden** als PDF.

> **Tip:** het voorbeeld is een exacte weergave van de print. Klopt iets niet in het voorbeeld, dan
klopt het ook niet op papier — pas het dan hier aan vóór je print.
""",
    },
    {
        'slug': 'scankaarten', 'category': 'Modules', 'icon': 'fa-barcode',
        'sort_index': 12, 'title': 'Scankaarten',
        'summary': 'Kaarten met een scanbare barcode maken en printen.',
        'body': """# Scankaarten

Scankaarten zijn kaarten met een **scanbare barcode**, handig voor bijvoorbeeld kassa- of
voorraadhandelingen.

## Zo maak je er een

1. Ga naar **Scankaarten** in de menubalk.
2. Klik op **Nieuwe scankaart**.
3. Vul de gegevens in (o.a. de **barcode/EAN** en de omschrijving).
4. Bekijk het voorbeeld en klik op **Opslaan**.
5. **Print** of **download** de kaart vanuit het overzicht.

> **Tip:** controleer de barcode altijd even met een scanner voordat je een grote oplage print.
""",
    },
    {
        'slug': 'labels-prijskaartjes', 'category': 'Modules', 'icon': 'fa-tag',
        'sort_index': 13, 'title': 'Labels (prijskaartjes)',
        'summary': 'Labels met naam, prijs en barcode maken, en op de labelprinter printen.',
        'body': """# Labels (prijskaartjes)

Met de **Labels**-module maak je kleine prijskaartjes/labels voor op het schap, compleet met **naam,
prijs en barcode**. Deze module is beschikbaar als je er rechten voor hebt.

## Een labelopdracht maken

1. Ga naar **Labels** in de menubalk.
2. Klik op **Nieuwe labels**.
3. Voeg **producten** toe. Je kunt:
   - producten opzoeken uit je **productenlijst**, of
   - handmatig naam, prijs, oude prijs en aantal invullen.
4. Kies eventueel het **labelformaat** en extra regels/opties (datum, logo).
5. Bekijk het voorbeeld en **print** de labels op de labelprinter.

## Producten beheren

Onder **Labels → Producten** houd je je productenlijst bij (naam, barcode, prijs, categorie). Zo hoef je
veelgebruikte producten niet telkens opnieuw in te typen.

## Historie

Onder **Labels → Geschiedenis** vind je eerder geprinte opdrachten terug. Handig om iets opnieuw te
printen of te controleren.
""",
    },
    {
        'slug': 'winkelpakketten', 'category': 'Modules', 'icon': 'fa-box-open',
        'sort_index': 14, 'title': 'Winkelpakketten',
        'summary': 'De kant-en-klare wekelijkse actiepakketten bekijken, downloaden en printen.',
        'body': """# Winkelpakketten

Winkelpakketten zijn de **kant-en-klare, wekelijkse actiepakketten**: complete sets schapkaarten die
centraal zijn klaargezet. Je hoeft ze alleen te kiezen en te printen.

## Zo gebruik je ze

1. Ga naar **Winkelpakketten**.
2. Kies de gewenste **periode** en **categorie/afdeling**.
3. Bekijk de tegels (voorbeelden) van de beschikbare documenten.
4. **Print** direct naar de winkelprinter, of **download** de PDF.

> **Tip:** de pakketten worden automatisch bijgewerkt. Zie je een nieuwe week nog niet? Probeer het
later opnieuw, of vraag je beheerder om te synchroniseren.
""",
    },

    # ── PRINTEN ────────────────────────────────────────────────────────────────
    {
        'slug': 'printen', 'category': 'Printen', 'icon': 'fa-print',
        'sort_index': 20, 'title': 'Printen naar de winkelprinter',
        'summary': 'Hoe printen werkt, de juiste lade kiezen, en wat te doen als het misgaat.',
        'body': """# Printen naar de winkelprinter

Vanuit vrijwel elke module kun je direct **printen naar de winkelprinter** (de multifunctional/copier)
of een PDF **downloaden** om zelf te printen.

## Printen

1. Open of selecteer wat je wilt printen.
2. Klik op **Printen op [winkelprinter]**.
3. Bij sommige formaten kies je nog de juiste **papierlade** (bijvoorbeeld A4 of A3).
4. Bevestig. De opdracht gaat naar de printer in de winkel.

## Downloaden in plaats van printen

Geen printer bij de hand, of wil je zelf printen? Kies **Downloaden**. Je krijgt een **PDF** op
ware grootte die je op elke printer kunt afdrukken.

## Er gaat iets mis

- **"Geen printer"** naast een winkel? Dan is er nog geen winkelprinter ingesteld. Vraag je beheerder
  om die in te stellen (Beheer → Filialen).
- Superadmin en je krijgt de vraag om een winkel te kiezen? Kies eerst rechtsboven de juiste **winkel**.
- Komt er niets uit? Controleer of de printer aan staat en papier heeft, en probeer het opnieuw. Blijft
  het misgaan, meld het dan via het **?-knopje** rechtsonder.
""",
    },

    # ── BEHEER ─────────────────────────────────────────────────────────────────
    {
        'slug': 'team-gebruikers', 'category': 'Beheer', 'icon': 'fa-users',
        'sort_index': 30, 'title': 'Team & gebruikers beheren',
        'summary': 'Voor ondernemers en beheerders: mensen uitnodigen, aanpassen en verwijderen.',
        'body': """# Team & gebruikers beheren

Ondernemers beheren hun **eigen team**; superadmins beheren **alle gebruikers**.

## Mijn team (ondernemer)

Onder **Beheer → Mijn team** zie je de medewerkers van je winkel. Je kunt:

- nieuwe collega's **uitnodigen** (ze krijgen een e-mail om een wachtwoord in te stellen);
- aanmeldingen **goedkeuren**;
- gegevens van teamleden **aanpassen**.

## Gebruikers (superadmin)

Onder **Beheer → Gebruikers** beheer je alle accounts. Klik een gebruiker aan om:

- naam, e-mail, rol en winkel aan te passen;
- een **welkomst- of reset-mail** te sturen;
- de gebruiker te **verwijderen** — daarbij kun je zijn/haar kaarten **overdragen** aan een andere
  gebruiker, zodat er niets verloren gaat.

> **Tip:** het e-mailadres is de inlognaam. Zorg dat het klopt, anders kan iemand niet inloggen.
""",
    },
    {
        'slug': 'rollen-rechten', 'category': 'Beheer', 'icon': 'fa-user-shield',
        'sort_index': 31, 'title': 'Rollen & rechten',
        'summary': 'Wat mogen medewerker, ondernemer en superadmin — en hoe pas je rechten aan?',
        'body': """# Rollen & rechten

Elke gebruiker heeft een **rol** die bepaalt wat hij of zij mag.

## De rollen

- **Medewerker** — maakt schapkaarten, scankaarten en (met recht) labels.
- **Ondernemer** — alles van een medewerker, plus het **eigen team** beheren en het logboek van de eigen
  winkel bekijken.
- **Superadmin (beheerder)** — mag **alles**: alle winkels, gebruikers, filialen, rollen, mailinstellingen
  en de kennisbank.

## Rechten aanpassen (superadmin)

Onder **Beheer → Rollen & rechten** stel je per rol in welke onderdelen beschikbaar zijn, bijvoorbeeld:

- Labels maken / Labelhistorie / Producten beheren;
- Eigen team beheren;
- Logboek bekijken;
- Winkelpakketten synchroniseren.

Een superadmin heeft altijd alle rechten; die kun je niet uitzetten.
""",
    },
    {
        'slug': 'feedback-beheren', 'category': 'Beheer', 'icon': 'fa-inbox',
        'sort_index': 32, 'title': 'Feedback beheren (voor beheerders)',
        'summary': 'Het ticketsysteem: meldingen lezen, status geven, zoeken en afhandelen.',
        'body': """# Feedback beheren

Alle meldingen die gebruikers via het **?-knopje** insturen, komen binnen onder
**Beheer → Feedback**. Naast het menu-item zie je een **telbolletje** met het aantal **ongelezen**
meldingen.

## De lijst

De feedbacklijst werkt als een klein **ticketsysteem**:

- **Filter** op type (Probleem / Suggestie / Idee) en op status.
- **Zoek** op tekst — dat matcht in de titel, de melding zelf, de afzender, de winkel en de pagina.
- Ongelezen meldingen zijn **dikgedrukt** gemarkeerd.

## Een melding afhandelen

Klik een melding open. Je ziet:

- **wie** het meldde, van welke **winkel**, op welk **moment**, vanaf welke **pagina**, plus IP en browser;
- de **schermafbeelding** (indien meegestuurd);
- een **logboek** van alles wat er met de melding gebeurde.

Zet de **status** op *In behandeling*, *Opgelost* of *Afgewezen*, en voeg eventueel een **notitie** toe.
Elke wijziging wordt in het logboek vastgelegd. Zodra je een melding opent, telt hij niet meer mee als
ongelezen.

## Deze kennisbank bewerken

Onder **Beheer → Kennisbank** kun je alle hulp-artikelen **aanpassen en uitbreiden**. Zie het artikel
*De kennisbank onderhouden*.
""",
    },
    {
        'slug': 'kennisbank-onderhouden', 'category': 'Beheer', 'icon': 'fa-book-open',
        'sort_index': 33, 'title': 'De kennisbank onderhouden',
        'summary': 'Voor superadmins: artikelen maken, bewerken en opmaken met Markdown.',
        'body': """# De kennisbank onderhouden

Superadmins kunnen de hele kennisbank zelf **beheren, aanpassen en aanvullen** onder
**Beheer → Kennisbank**.

## Een artikel maken of bewerken

1. Ga naar **Beheer → Kennisbank**.
2. Klik op **Nieuw artikel**, of op **Bewerken** bij een bestaand artikel.
3. Vul in:
   - **Titel** — de kop van het artikel.
   - **Categorie** — artikelen met dezelfde categorie worden samen gegroepeerd (bv. *Modules*).
   - **Icoon** — een FontAwesome-klasse, bijvoorbeeld `fa-tags` of `fa-print`.
   - **Korte samenvatting** — één zin die op de overzichtspagina verschijnt.
   - **Sorteervolgorde** — een getal; lager staat hoger in de lijst.
   - **Inhoud** — de tekst in **Markdown** (zie hieronder).
4. Klik op **Opslaan**.

## Opmaak met Markdown

De inhoud schrijf je in Markdown. De belangrijkste opmaak:

```
# Grote kop
## Kleinere kop

Gewone tekst met **vet** en *cursief*.

- Opsomming
- Nog een punt

1. Genummerd
2. Tweede stap

> Een tip of belangrijke opmerking.

[Een link](https://www.plus.nl)
![Beschrijving van de afbeelding](https://.../screenshot.png)
```

## Screenshots toevoegen

Wil je een **screenshot** in een artikel? Zet de afbeelding online (of in de map `static/img/kb/`) en
verwijs ernaar met `![omschrijving](url)`. De afbeelding schaalt automatisch mee.

> **Tip:** houd artikelen kort en concreet, met duidelijke koppen. Eén onderwerp per artikel leest het
prettigst.
""",
    },

    # ── BEVEILIGING ────────────────────────────────────────────────────────────
    {
        'slug': 'tweefactor-2fa', 'category': 'Beveiliging', 'icon': 'fa-shield-halved',
        'sort_index': 40, 'title': 'Tweefactor-beveiliging (2FA)',
        'summary': 'Extra beveiliging met een authenticator-app. Verplicht voor superadmins.',
        'body': """# Tweefactor-beveiliging (2FA)

Met **tweefactor-authenticatie (2FA)** beveilig je je account extra: naast je wachtwoord vul je bij het
inloggen een **6-cijferige code** in uit een authenticator-app.

Voor **superadmins is 2FA verplicht**. Andere gebruikers kunnen het vrijwillig inschakelen.

## Instellen

1. Installeer een authenticator-app op je telefoon (bijv. Google Authenticator, Microsoft Authenticator
   of een wachtwoordmanager met TOTP).
2. Ga naar **Mijn profiel** en start het instellen van tweefactor.
3. **Scan de QR-code** met de app.
4. Vul de **6-cijferige code** uit de app in om te bevestigen. Klaar.

Vanaf nu vraagt PLUSLokaal bij het inloggen om die code.

## Je toegang kwijt?

Ben je je telefoon/authenticator kwijt? Dan kan een beheerder je 2FA opnieuw instellen via je
gebruikerspagina. Neem daarvoor contact op met je beheerder.
""",
    },

    # ── OVERIG ─────────────────────────────────────────────────────────────────
    {
        'slug': 'veelgestelde-vragen', 'category': 'Overig', 'icon': 'fa-circle-question',
        'sort_index': 50, 'title': 'Veelgestelde vragen',
        'summary': 'Korte antwoorden op de meest voorkomende vragen.',
        'body': """# Veelgestelde vragen

**Ik kan niet inloggen.**
Controleer of je je **e-mailadres** goed typt (hoofdletters maken niet uit). Wachtwoord kwijt? Gebruik
*Wachtwoord vergeten* op de inlogpagina.

**Er komt niets uit de printer.**
Kies eerst (als superadmin) de juiste **winkel** rechtsboven. Staat er *"geen printer"*? Dan moet je
beheerder de winkelprinter nog instellen. Zie *Printen naar de winkelprinter*.

**Mijn kaart ziet er op papier anders uit dan verwacht.**
Het **live voorbeeld** in de editor is een exacte weergave van de print. Klopt het voorbeeld, dan klopt
de print. Zo niet: pas de kaart aan vóór je print.

**Ik mis een knop of module.**
Waarschijnlijk heb je er (nog) geen **recht** voor. Vraag je ondernemer of beheerder om het recht toe te
kennen. Zie *Rollen & rechten*.

**Ik heb een idee of ik vind iets onduidelijk.**
Top! Gebruik het **?-knopje** rechtsonder om je idee of vraag door te geven. We lezen alles.

**Hoe krijg ik een screenshot mee in mijn melding?**
Dat gaat vanzelf: bij het openen van het feedback-venster wordt automatisch een schermafbeelding
gemaakt. Je kunt die verwijderen of een eigen afbeelding uploaden.
""",
    },
]
