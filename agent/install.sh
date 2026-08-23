#!/usr/bin/env bash
# PLUSLokaal Print-Agent - installatie op een Raspberry Pi (of andere Debian-achtige).
# Gebruik:  curl -fsSL https://pluslokaal.com/agent/install.sh | sudo bash
set -e
echo "── PLUSLokaal Print-Agent installeren ─────────────────────────"
if [ "$(id -u)" != "0" ]; then echo "Draai met sudo."; exit 1; fi

echo "• Pakketten (python3 + CUPS voor de winkelprinter)…"
apt-get update -qq
apt-get install -y -qq python3 cups cups-client >/dev/null

echo "• Agent downloaden…"
mkdir -p /opt/pluslokaal-agent
curl -fsSL "https://pluslokaal.com/api/agent/download" -o /opt/pluslokaal-agent/pluslokaal_agent.py
chmod 755 /opt/pluslokaal-agent/pluslokaal_agent.py

echo "• Service installeren…"
python3 /opt/pluslokaal-agent/pluslokaal_agent.py --install

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "✅ Klaar. Open nu in de browser:  http://${IP:-<pi-adres>}:8080"
echo "   1) Plak daar de agent-sleutel (Beheer → Filialen → Print-agent)."
echo "   2) Kies de USB-labelprinter en/of de CUPS-queue van de winkelprinter."
echo "   Tip: voeg de USB-winkelprinter eerst toe aan CUPS: http://${IP:-<pi-adres>}:631"
