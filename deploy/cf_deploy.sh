#!/usr/bin/env bash
# Deploy BoloCart to Cloudflare Workers (free plan). Runs in GitHub Actions. Never prints secrets.
set -euo pipefail
cd worker
STATUS=../deploy/status/deploy.json
mkdir -p ../deploy/status
now() { date -u +%FT%TZ; }
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] || [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  printf '{"at":"%s","host":"cloudflare-workers-free","state":"waiting: GitHub secrets CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID"}\n' "$(now)" > $STATUS
  echo "skip=1" >> "$GITHUB_ENV"; exit 0
fi
fail() {
  MSG=$(grep -ihE "error|code|permission|auth" wrangler.err 2>/dev/null | sed 's/[^[:print:]]//g; s/"/'"'"'/g' | head -4 | tr '\n' ' ' | cut -c1-400)
  printf '{"at":"%s","host":"cloudflare-workers-free","state":"error","error":{"step":"%s","message":"%s"}}\n' "$(now)" "$1" "$MSG" > $STATUS; exit 1; }

# D1 database: reuse if it exists, otherwise create it
npx wrangler whoami >/dev/null 2>wrangler.err || fail auth
DBID=$(npx wrangler d1 list --json 2>wrangler.err | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const a=JSON.parse(s||"[]");const d=a.find(x=>x.name==="bolocart");process.stdout.write(d?d.uuid:"")})') || fail d1_list
if [ -z "$DBID" ]; then
  npx wrangler d1 create bolocart >/dev/null 2>wrangler.err || fail d1_create
  DBID=$(npx wrangler d1 list --json | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const d=JSON.parse(s).find(x=>x.name==="bolocart");process.stdout.write(d.uuid)})')
fi
sed -i "s/database_id = \".*\"/database_id = \"$DBID\"/" wrangler.toml
npx wrangler d1 migrations apply bolocart --remote >/dev/null 2>wrangler.err || fail d1_migrate

npx wrangler deploy > deploy.log 2>&1 || { cp deploy.log wrangler.err; fail deploy; }
URL=$(grep -oE 'https://[a-zA-Z0-9.-]+\.workers\.dev' deploy.log | head -1)
[ -n "$URL" ] || fail no_workers_dev_url

for k in WA_TOKEN WA_PHONE_NUMBER_ID WA_WABA_ID WA_APP_SECRET TEST_RECIPIENTS; do
  v="${!k:-}"; [ -n "$v" ] && printf '%s' "$v" | npx wrangler secret put "$k" >/dev/null 2>&1 || true
done
echo "BOLOCART_URL=$URL" >> "$GITHUB_ENV"
