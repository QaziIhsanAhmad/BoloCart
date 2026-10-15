"""Configure, deploy and verify BoloCart on Render's free web service (runs in GitHub Actions).

Writes deploy/status/deploy.json (no secrets, never prints tokens).
MODE=deploy (default) pushes env vars and triggers a deploy; MODE=check only verifies.
"""
import datetime
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

OUT = "deploy/status/deploy.json"
API = "https://api.render.com/v1/"
out = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "host": "render-free"}


def save():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)


def fail(step, msg):
    out.update(state="error", error={"step": step, "message": str(msg)[:400]})
    save()
    raise SystemExit(1)


def http(url, method="GET", body=None, key=None, timeout=30):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return 0, type(e).__name__


def derive(secret, purpose):
    return hashlib.sha256(f"{secret}:{purpose}".encode()).hexdigest()[:24] if secret else ""


key = os.getenv("RENDER_API_KEY", "").strip()
if not key:
    out["state"] = "waiting: GitHub secret RENDER_API_KEY is not set"
    save()
    raise SystemExit(0)

code, body = http(API + "services?name=bolocart&limit=20", key=key)
if code != 200:
    fail("find_service", f"HTTP {code}: {body}")
items = [i.get("service", i) for i in json.loads(body)]
svc = next((s for s in items if s.get("name") == "bolocart" and s.get("type") == "web_service"), None)
if not svc:
    out["state"] = "waiting: no Render web service named 'bolocart' (create it from the Blueprint)"
    save()
    raise SystemExit(0)
sid = svc["id"]
url = (svc.get("serviceDetails") or {}).get("url", "").rstrip("/")
app_secret = os.getenv("WA_APP_SECRET", "").strip()
verify, health = derive(app_secret, "verify"), derive(app_secret, "health")
out.update({"service": sid, "url": url, "demo": url + "/demo", "webhook": url + "/webhook/whatsapp"})

if os.getenv("MODE", "deploy") == "deploy":
    env = {"PORT": "7860", "DB_PATH": "/tmp/agent.db", "SEND_MODE": "test", "LIVE_APPROVED": "no",
           "MAX_TEMPLATES_PER_DAY": "20", "STORE_NAME": "Demo Store", "KB_PATH": "kb/demo.md",
           "OLLAMA_URL": "http://127.0.0.1:9", "PUBLIC_URL": url}
    for k in ("WA_TOKEN", "WA_PHONE_NUMBER_ID", "WA_WABA_ID", "WA_APP_SECRET", "TEST_RECIPIENTS"):
        v = os.getenv(k, "").strip()
        if v:
            env[k] = v
    code, body = http(API + f"services/{sid}/env-vars", "PUT",
                      [{"key": k, "value": v} for k, v in env.items()], key=key)
    if code != 200:
        fail("set_env", f"HTTP {code}: {body}")
    out["env_keys"] = sorted(env)  # names only
    code, body = http(API + f"services/{sid}/deploys", "POST", {"clearCache": "do_not_clear"}, key=key)
    if code not in (200, 201, 202):
        fail("deploy", f"HTTP {code}: {body}")
    dep = json.loads(body).get("id")
    status = ""
    for _ in range(80):  # up to ~20 min
        c, b = http(API + f"services/{sid}/deploys/{dep}", key=key)
        status = json.loads(b).get("status", "") if c == 200 else f"http{c}"
        if status in ("live", "build_failed", "update_failed", "canceled", "deactivated"):
            break
        time.sleep(15)
    out["deploy_status"] = status
    if status != "live":
        fail("deploy_wait", f"deploy ended as {status}")

# Verify from outside (free instances may need ~1 min to wake up)
code = 0
for _ in range(20):
    code, body = http(url + "/healthz", timeout=90)
    if code == 200:
        break
    time.sleep(15)
out["healthz"] = code
if code == 200:
    out["mode"] = json.loads(body).get("mode")
    if health:
        c, b = http(url + "/healthz?key=" + health)
        if c == 200:
            d = json.loads(b)
            out["stats"] = {k: d.get(k) for k in ("setup", "orders", "out_24h", "billing_7d")}
            out["recent_events"] = [e.get("text", "")[:160] for e in (d.get("events_7d") or [])[:5]]
    c, b = http(url + "/demo")
    out["demo_ok"] = c == 200 and "BoloCart" in b
    c, _ = http(url + "/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=1")
    out["webhook_rejects_bad_token"] = c == 403
    if verify:
        c, b = http(url + "/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=" + verify + "&hub.challenge=4217")
        out["webhook_verify_ok"] = c == 200 and b.strip() == "4217"
ok = out.get("demo_ok") and out.get("webhook_rejects_bad_token") and out.get("webhook_verify_ok", True)
out["state"] = "ok" if ok else "check-failed"
save()

gh = os.getenv("GITHUB_ENV")
if gh:
    with open(gh, "a") as f:
        f.write(f"BOLOCART_URL={url}\nBOLOCART_VERIFY={verify}\n")
