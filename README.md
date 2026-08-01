# PLUSLokaal — Schapkaarten

Flask-applicatie voor het maken van **PLUS-schapkaarten** (print-klare PDF's), plus scankaarten, labels,
een Canva-achtige designer, het **Portaal** (pluslokaal.nl in eigen jasje), **Winkelpakketten** (W2P),
gebruikers-/winkelbeheer, e-mail en printen. UI en kaartteksten zijn in het Nederlands.

> **Let op:** geheimen en data zitten **niet** in deze repo (zie `.gitignore`). De database, `.secret_key`,
> `.portaal_secret` en de gecachte W2P-PDF's worden per installatie lokaal aangemaakt/opgebouwd.

---

## Schermafbeeldingen

**Dashboard — je schapkaarten**
![Dashboard](docs/screenshots/dashboard.png)

**Kaart-editor — live voorbeeld + artikelinfo overnemen van plus.nl**
![Kaart-editor](docs/screenshots/editor.png)

**Winkelpakketten (W2P) — kant-en-klare weekpakket-kaarten**
![Winkelpakketten](docs/screenshots/winkelpakketten.png)

**Inloggen**
![Inloggen](docs/screenshots/login.png)

---

## Snel starten (ontwikkeling)

Vereist: **Python 3.10+**.

```bash
git clone <deze-repo> pluslokaal
cd pluslokaal

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # headless browser voor plus.nl-zoek + Winkelpakketten

python3 app.py                       # start op http://localhost:5000
```

Bij de **eerste start** gebeurt automatisch:
- `.secret_key` / `.portaal_secret` worden aangemaakt (sessie-ondertekening + versleuteling).
- de SQLite-database `instance/pluslokaal.db` wordt aangemaakt + gemigreerd.
- een **admin-account** wordt aangemaakt: gebruikersnaam `admin`, wachtwoord `admin` → **wijzig dit direct**.

Inloggen kan met e-mailadres of met de naam `admin`.

---

## Productie (aanbevolen)

Draai met **gunicorn** (config staat in `gunicorn_conf.py`):

```bash
python3 -m gunicorn -c gunicorn_conf.py app:app
```

De config gebruikt bewust **1 worker + threads** … zie de uitleg boven in `gunicorn_conf.py`. Voor méér
doorvoer over alle cores kan het naar meerdere workers (de gedeelde state staat al in SQLite/`sharedstate.py`).

### Als systemd-service (Linux)

Zie `deploy/pluslokaal.service` — pas het pad aan en:

```bash
sudo cp deploy/pluslokaal.service /etc/systemd/system/pluslokaal.service
sudo systemctl daemon-reload
sudo systemctl enable --now pluslokaal
```

Achter een reverse proxy / Cloudflare-tunnel: de app staat achter `ProxyFix` (leest `X-Forwarded-*`),
dus HTTPS/host worden correct herkend.

---

## Configuratie (na installatie, via Beheer)

Als **superadmin** (`admin`) onder **Beheer**:
- **Mailinstellingen** — SMTP (bv. Resend) voor uitnodigingen/reset-mails.
- **Filialen** — winkels + winkelprinters (IPP).
- **Winkelpakket-accounts** — pluslokaal.nl-accounts (max. 6) voor W2P-downloads; meer accounts = sneller.
  Ook: per admin mail-bij-mislukte-sync/download aan/uit.
- **Opslag** — W2P-cache beheren (weken downloaden/verwijderen).
- **Demo-account** — aan/uit (login `demo`/`demo`, gesimuleerd printen).

Het **Portaal** koppel je per gebruiker via het profiel (pluslokaal.nl-account).

---

## Structuur

| Bestand | Functie |
|---|---|
| `app.py` | de volledige backend (modellen, routes, PDF-renderer) |
| `w2p_client.py` | Winkelpakketten (W2P) download-workers (Playwright) |
| `plus_search.py` | plus.nl-productzoek (gedeelde warme browser + service) |
| `sharedstate.py` | proces-overstijgende state (print/W2P-jobs, rate-limiting) |
| `templates/`, `static/` | Jinja-templates en de PLUS-stijl (`static/css/plus.css`) |
| `docs/` | stijlgids, logo's, presentatie, integratie-documenten |
| `gunicorn_conf.py` | productie-serverconfig |

Zie `CLAUDE.md` voor de uitgebreide ontwikkelaarsgids.
