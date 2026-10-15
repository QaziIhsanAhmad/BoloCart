#!/usr/bin/env bash
# Local end-to-end test: fresh local D1, worker under wrangler dev, mock Graph API.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf .wrangler/state
[ -f .dev.vars ] || printf "WA_TOKEN=test-token\nWA_PHONE_NUMBER_ID=111\nWA_WABA_ID=222\nWA_APP_SECRET=appsecret\nTEST_RECIPIENTS=923234470000\nSHOP_API_SECRET=shopsecret\n" > .dev.vars
npx wrangler d1 migrations apply bolocart --local -c wrangler.test.toml >/dev/null
npx wrangler dev -c wrangler.test.toml --port 8788 --ip 127.0.0.1 >/tmp/bolocart-dev.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
for i in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8788/healthz && break; sleep 1; done
node test/e2e.mjs
