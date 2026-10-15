"""Deploy BoloCart to a free Hugging Face Docker Space and verify it end to end.

Runs in GitHub Actions. Writes deploy/status/deploy.json (no secrets). Never prints derived tokens.
MODE=deploy (default) uploads + configures; MODE=check only verifies the running Space.
"""
import datetime
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

OUT = "deploy/status/deploy.json"
out = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}


def save():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)


def get(url, timeout=20):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return 0, type(e).__name__


tok = os.getenv("HF_TOKEN", "").strip()
if not tok:
    out["state"] = "waiting: GitHub secret HF_TOKEN is not set"
    save()
    raise SystemExit(0)

from huggingface_hub import HfApi  # noqa: E402

api = HfApi(token=tok)
user = api.whoami()["name"]
repo = f"{user}/bolocart"
url = f"https://{user.lower().replace('_', '-').replace('.', '-')}-bolocart.hf.space"
seed = os.getenv("WA_APP_SECRET", "").strip() or tok
verify = hashlib.sha256((seed + ":verify").encode()).hexdigest()[:24]
health = hashlib.sha256((tok + ":health").encode()).hexdigest()[:24]
out.update({"space": f"https://huggingface.co/spaces/{repo}", "url": url,
            "demo": url + "/demo", "webhook": url + "/webhook/whatsapp"})

if os.getenv("MODE", "deploy") == "deploy":
    api.create_repo(repo, repo_type="space", space_sdk="docker", exist_ok=True, private=False)
    for k in ("WA_TOKEN", "WA_PHONE_NUMBER_ID", "WA_WABA_ID", "WA_APP_SECRET", "TEST_RECIPIENTS"):
        v = os.getenv(k, "").strip()
        if v:
            api.add_space_secret(repo, k, v)
    api.add_space_secret(repo, "WA_VERIFY_TOKEN", verify)
    api.add_space_secret(repo, "HEALTH_KEY", health)
    # Cost guard is fixed here: test mode, live sending not approved.
    for k, v in {"SEND_MODE": "test", "LIVE_APPROVED": "no", "MAX_TEMPLATES_PER_DAY": "20",
                 "STORE_NAME": "Demo Store", "KB_PATH": "kb/demo.md",
                 "OLLAMA_URL": "http://127.0.0.1:9", "PUBLIC_URL": url}.items():
        api.add_space_variable(repo, k, v)
    api.upload_folder(folder_path=".", repo_id=repo, repo_type="space",
                      allow_patterns=["app/*.py", "kb/*.md", "demo/index.html", "requirements.txt",
                                      "Dockerfile", "README.md"],
                      commit_message="Deploy BoloCart from GitHub Actions")
    api.restart_space(repo)
    out["deployed"] = True

# Wait for the Space to build and answer (up to ~15 min)
code, body = 0, ""
for _ in range(90):
    code, body = get(url + "/healthz")
    if code == 200:
        break
    time.sleep(10)
out["healthz"] = code
if code == 200:
    out["mode"] = json.loads(body).get("mode")
    c, b = get(url + "/healthz?key=" + health)
    if c == 200:
        d = json.loads(b)
        out["stats"] = {k: d.get(k) for k in ("setup", "orders", "out_24h", "billing_7d")}
        out["recent_events"] = [e.get("text", "")[:160] for e in (d.get("events_7d") or [])[:5]]
    c, b = get(url + "/demo")
    out["demo_ok"] = c == 200 and "BoloCart" in b
    c, b = get(url + "/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=" + verify + "&hub.challenge=4217")
    out["webhook_verify_ok"] = c == 200 and b.strip() == "4217"
out["state"] = "ok" if out.get("demo_ok") and out.get("webhook_verify_ok") else "check-failed"
save()

gh = os.getenv("GITHUB_ENV")
if gh:  # hand values to the next step without printing them
    with open(gh, "a") as f:
        f.write(f"BOLOCART_URL={url}\nBOLOCART_VERIFY={verify}\n")
