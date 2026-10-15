#!/usr/bin/env bash
# One-time install on the Proxmox VM (Ubuntu/Debian with Docker + NVIDIA container toolkit).
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Docker missing: curl -fsSL https://get.docker.com | sh"; exit 1; }
[ -f .env ] || { cp .env.example .env; echo "Created .env — fill in the blank values, then re-run."; exit 0; }

# PUBLIC_URL must be literal for the app
DOMAIN=$(grep -E '^NGROK_DOMAIN=' .env | cut -d= -f2)
sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=https://${DOMAIN}|" .env

mkdir -p data ollama && chown 1000:1000 data
docker compose up -d --build
MODEL=$(grep -E '^OLLAMA_MODEL=' .env | cut -d= -f2)
docker compose exec -T ollama ollama pull "$MODEL"
docker compose restart agent
sleep 5
KEY=$(grep -E '^HEALTH_KEY=' .env | cut -d= -f2)
curl -s "http://localhost:7860/healthz?key=${KEY}" 2>/dev/null || docker compose exec -T agent \
  python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:7860/healthz?key=${KEY}').read().decode())"
echo; echo "Done. Webhook URL for Meta: https://${DOMAIN}/webhook/whatsapp"
