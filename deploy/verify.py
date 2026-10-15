"""Check the deployed Worker from outside and write deploy/status/deploy.json (no secrets)."""
import datetime, hashlib, json, os, time, urllib.error, urllib.request

OUT = "deploy/status/deploy.json"
url = os.getenv("BOLOCART_URL", "").rstrip("/")
secret = os.getenv("WA_APP_SECRET", "").strip()
derive = lambda p: hashlib.sha256(f"{secret}:{p}".encode()).hexdigest()[:24] if secret else ""
out = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
       "host": "cloudflare-workers-free", "url": url, "webhook": url + "/webhook/whatsapp"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def get(path, method="GET", redirect=True):
    opener = urllib.request.build_opener() if redirect else urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url + path, method=method, data=b"" if method == "POST" else None,
                                 headers={"User-Agent": "Mozilla/5.0 (BoloCart deploy check)"})
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, "", dict(e.headers or {})
    except Exception as e:  # noqa: BLE001
        return 0, type(e).__name__, {}


for _ in range(12):
    c, b, _h = get("/healthz")
    if c == 200:
        break
    time.sleep(5)
out["healthz"] = c
if c == 200:
    out["mode"] = json.loads(b).get("mode")
c, _b, h = get("/demo", redirect=False)
out["demo_redirect_ok"] = c == 302 and "github.io/BoloCart/demo" in h.get("Location", h.get("location", ""))
c, _b, _h = get("/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=1")
out["webhook_rejects_bad_token"] = c == 403
if secret:
    c, b, _h = get(f"/webhook/whatsapp?hub.mode=subscribe&hub.verify_token={derive('verify')}&hub.challenge=4217")
    out["webhook_verify_ok"] = c == 200 and b.strip() == "4217"
    c, b, _h = get(f"/admin/ensure-template?key={derive('health')}", method="POST")
    out["template"] = json.loads(b).get("template") if c == 200 else f"http {c}"
    c, b, _h = get(f"/healthz?key={derive('health')}")
    if c == 200:
        d = json.loads(b)
        out["stats"] = {k: d.get(k) for k in ("orders", "out_24h", "billing_7d")}
        seen, ev = set(), []
        for e in d.get("events_7d") or []:
            t = f'{e.get("level")}: {e.get("text", "")[:200]}'
            if t not in seen:
                seen.add(t)
                ev.append(t)
        out["recent_events"] = ev[:15]
ok = out.get("healthz") == 200 and out.get("mode") == "test" and out["demo_redirect_ok"] and out["webhook_rejects_bad_token"]
out["state"] = "ok" if ok and out.get("webhook_verify_ok", True) else "check-failed"
os.makedirs("deploy/status", exist_ok=True)
json.dump(out, open(OUT, "w"), indent=2)
gh = os.getenv("GITHUB_ENV")
if gh:
    open(gh, "a").write(f"BOLOCART_VERIFY={derive('verify')}\n")
print("state:", out["state"])
