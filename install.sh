#!/usr/bin/env bash
# PLUSLokaal - installatiescript. Draai vanuit de repo-map:  bash install.sh
set -e

echo "── PLUSLokaal installatie ─────────────────────────────────────────────"

# 1) Python-check
if ! command -v python3 >/dev/null; then echo "❌ python3 niet gevonden (installeer Python 3.10+)"; exit 1; fi
echo "• Python: $(python3 --version)"

# 2) Virtuele omgeving
if [ ! -d .venv ]; then
  echo "• Virtuele omgeving aanmaken (.venv)…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 3) Python-pakketten
echo "• Pakketten installeren…"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

# 4) Headless browser (voor plus.nl-zoek + Winkelpakketten)
echo "• Chromium (Playwright) installeren…"
python -m playwright install chromium
echo "• Systeembibliotheken voor Chromium (kan sudo vragen)…"
python -m playwright install-deps chromium 2>/dev/null || \
  echo "  ⚠ Sla systeem-deps over. Installeer ze zo nodig handmatig: sudo python -m playwright install-deps chromium"

echo ""
echo "✅ Klaar. Starten:"
echo "     source .venv/bin/activate"
echo "     python app.py            # ontwikkeling → http://localhost:5000"
echo ""
echo "   Productie (gunicorn):"
echo "     python -m gunicorn -c gunicorn_conf.py app:app"
echo ""
echo "   Eerste login: gebruiker 'admin', wachtwoord 'admin' - wijzig dit direct."
