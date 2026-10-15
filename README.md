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

## Deploy (free)
GitHub Actions (`.github/workflows/bolocart.yml`) runs the tests, then deploys to a free Hugging Face Docker Space and checks the demo page and the webhook handshake. Results are written to `deploy/status/`.

Repository secrets: `HF_TOKEN` (Hugging Face write token). For the WhatsApp test number also `WA_APP_ID`, `WA_APP_SECRET`, `WA_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_WABA_ID`, `TEST_RECIPIENTS`.
The Space always runs with `SEND_MODE=test` and `LIVE_APPROVED=no`.

Self-hosting on your own server: `./install.sh` (Docker, optional local LLM via Ollama).
