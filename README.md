---
title: BoloCart
emoji: 📦
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
---

# BoloCart

Urdu WhatsApp confirmation for cash-on-delivery orders, by [Xenova Solutions](https://xenovasolutions.com).

A new COD order triggers an approved WhatsApp template in Urdu with **Confirm** and **Cancel** buttons. Buyers can also reply in Roman Urdu or Urdu script ("haan", "nahi", "ہاں", "نہیں") or ask about products and delivery. The order is tagged in the store (`WA-Confirmed`, `WA-CancelRequested`, `WA-NoReply`) before it reaches the courier. Refunds and complaints are handed to the store's team.

- **Public demo:** `/demo` (sample orders only, no messages sent)
- **WhatsApp webhook:** `/webhook/whatsapp`
- **Health:** `/healthz` (details with `?key=HEALTH_KEY`)

## Cost guard
| `SEND_MODE` | Behaviour |
|---|---|
| `dry_run` (default) | Nothing is sent; every message is logged |
| `test` | Sends only to numbers in `TEST_RECIPIENTS` |
| `live` | Sends to customers, only if `LIVE_APPROVED=yes` |

Plus a daily template cap and free-form replies only inside WhatsApp's 24-hour customer-service window. Meta's `pricing.billable` flag on every delivered message is recorded, so costs are measured, not assumed.

## Run and test
```bash
pip install -r requirements.txt pytest
python -m pytest -q          # 37 tests
python -m demo.run_demo      # sample orders through the real agent, simulated network
```

## Deploy (free): Cloudflare Workers
The hosted agent is `worker/` — a Cloudflare Worker (free plan, no sleep) with D1 for storage, Workers AI's free daily allowance for FAQ answers (human handoff when unavailable), and an hourly cron that marks unanswered orders. Free-plan limits (Cloudflare docs): 100,000 requests/day, 10 ms CPU per request (waiting on the network doesn't count), D1 5 GB, Workers AI 10,000 neurons/day.

GitHub Actions runs the Python tests, the sample-order demo and a Worker end-to-end test (local D1 + mock WhatsApp API), then deploys with Wrangler and checks the live Worker from outside. Results go to `deploy/status/`.

Repository secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`. For the WhatsApp test number also `WA_APP_ID`, `WA_APP_SECRET`, `WA_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_WABA_ID`, `TEST_RECIPIENTS`. The webhook verify token and health key are derived from the app secret and never stored in the repo. The Worker always runs with `SEND_MODE=test`, `LIVE_APPROVED=no`.

Public demo (static): https://qaziihsanahmad.github.io/BoloCart/demo/

The Python app in `app/` is the same agent for self-hosting.
Self-hosting on your own server: `./install.sh` (Docker, optional local LLM via Ollama).
