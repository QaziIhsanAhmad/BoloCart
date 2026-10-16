"""Connect the WhatsApp test number to the deployed webhook (no messages are sent here).

Needs GitHub secrets WA_APP_ID, WA_APP_SECRET, WA_TOKEN, WA_WABA_ID. Writes deploy/status/meta.json.
"""
import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request

OUT = "deploy/status/meta.json"
G = "https://graph.facebook.com/v25.0/"
out = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}


def call(method, path, params):
    data = urllib.parse.urlencode(params).encode()
    url = G + path + ("" if method == "POST" else "?" + data.decode())
    req = urllib.request.Request(url, data=data if method == "POST" else None, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}").get("error", {})
        except Exception:  # noqa: BLE001
            err = {}
        return e.code, {"error": {"message": err.get("message", ""), "code": err.get("code")}}


env = {k: os.getenv(k, "").strip() for k in
       ("WA_APP_ID", "WA_APP_SECRET", "WA_TOKEN", "WA_WABA_ID", "BOLOCART_URL", "BOLOCART_VERIFY")}
missing = [k for k in ("WA_APP_ID", "WA_APP_SECRET", "WA_TOKEN", "WA_WABA_ID") if not env[k]]
if missing or not env["BOLOCART_URL"]:
    out["state"] = "waiting: " + (", ".join(missing) if missing else "deployment URL")
else:
    app_token = env["WA_APP_ID"] + "|" + env["WA_APP_SECRET"]
    c, r = call("POST", env["WA_APP_ID"] + "/subscriptions", {
        "object": "whatsapp_business_account", "callback_url": env["BOLOCART_URL"] + "/webhook/whatsapp",
        "verify_token": env["BOLOCART_VERIFY"], "fields": "messages", "access_token": app_token})
    out["webhook_subscription"] = {"http": c, "ok": bool(r.get("success")), "error": r.get("error")}
    c, r = call("POST", env["WA_WABA_ID"] + "/subscribed_apps", {"access_token": env["WA_TOKEN"]})
    out["waba_subscribed"] = {"http": c, "ok": bool(r.get("success")), "error": r.get("error")}
    c, r = call("GET", env["WA_WABA_ID"] + "/message_templates",
                {"name": "cod_order_confirm", "fields": "name,status,category,language", "access_token": env["WA_TOKEN"]})
    out["template"] = r.get("data") if c == 200 else {"http": c, "error": r.get("error")}
    # Diagnostics (no secrets): where Meta will deliver webhooks for this number
    c, r = call("GET", env["WA_APP_ID"] + "/subscriptions", {"access_token": app_token})
    out["app_subscriptions"] = [{"object": x.get("object"), "callback_url": x.get("callback_url"), "active": x.get("active"),
                                 "fields": [f.get("name") for f in x.get("fields", [])]} for x in r.get("data", [])] if c == 200 else {"http": c, "error": r.get("error")}
    c, r = call("GET", env["WA_WABA_ID"] + "/subscribed_apps", {"access_token": env["WA_TOKEN"]})
    out["waba_apps"] = [{"id": (x.get("whatsapp_business_api_data") or {}).get("id"), "name": (x.get("whatsapp_business_api_data") or {}).get("name"),
                         "override_callback_uri": x.get("override_callback_uri")} for x in r.get("data", [])] if c == 200 else {"http": c, "error": r.get("error")}
    pid = os.getenv("WA_PHONE_NUMBER_ID", "").strip()
    if pid:
        c, r = call("GET", pid, {"fields": "display_phone_number,verified_name,status,quality_rating,code_verification_status,webhook_configuration,messaging_limit_tier",
                                 "access_token": env["WA_TOKEN"]})
        out["phone"] = r if c == 200 else {"http": c, "error": r.get("error")}
    ok = out["webhook_subscription"]["ok"] and out["waba_subscribed"]["ok"]
    out["state"] = "connected" if ok else "check-failed"

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    json.dump(out, f, indent=2)
