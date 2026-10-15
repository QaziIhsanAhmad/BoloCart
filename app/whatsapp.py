import json
import time

from . import clients, db
from .config import S


def _url(path):
    return f"https://graph.facebook.com/{S.GRAPH_VERSION}/{path}"


def _auth():
    return {"Authorization": f"Bearer {S.WA_TOKEN}"}


def _guard(to, kind):
    """Return a reason string if sending must be blocked, else None. This is the cost guard."""
    if S.SEND_MODE == "dry_run":
        return "dry_run"
    if S.SEND_MODE == "test" and to not in S.TEST_RECIPIENTS:
        return "not_test_recipient"
    if S.SEND_MODE == "live" and not S.LIVE_APPROVED:
        return "live_not_approved"
    if S.SEND_MODE not in ("test", "live"):
        return "bad_mode"
    if kind == "template" and db.templates_sent_today() >= S.MAX_TEMPLATES_PER_DAY:
        return "daily_template_cap"
    if kind == "session" and not db.window_open(to):
        return "window_closed"
    return None


async def send(payload, kind):
    to = payload["to"]
    reason = _guard(to, kind)
    body = json.dumps(payload, ensure_ascii=False)
    if reason:
        db.x("INSERT INTO messages(direction,phone,kind,body,result,ts) VALUES('out',?,?,?,?,?)",
             (to, kind, body, f"blocked:{reason}", time.time()))
        return {"blocked": reason}
    r = await clients.HTTP.post(_url(f"{S.WA_PHONE_NUMBER_ID}/messages"), headers=_auth(), json=payload)
    data = r.json() if r.content else {}
    ok = r.status_code < 300
    wamid = (data.get("messages") or [{}])[0].get("id") if ok else None
    db.x("INSERT INTO messages(direction,phone,wamid,kind,body,result,ts) VALUES('out',?,?,?,?,?,?)",
         (to, wamid, kind, body, "sent" if ok else f"error:{r.status_code}", time.time()))
    if not ok:
        db.event("error", f"WhatsApp send failed {r.status_code}: {str(data)[:300]}")
    return {"ok": ok, "wamid": wamid, "status": r.status_code}


def order_template(to, first_name, order_name, total, order_gid):
    return {
        "messaging_product": "whatsapp", "to": to, "type": "template",
        "template": {
            "name": S.TEMPLATE_NAME, "language": {"code": S.TEMPLATE_LANG},
            "components": [
                {"type": "body", "parameters": [
                    {"type": "text", "text": first_name or "Customer"},
                    {"type": "text", "text": order_name},
                    {"type": "text", "text": total}]},
                {"type": "button", "sub_type": "quick_reply", "index": "0",
                 "parameters": [{"type": "payload", "payload": f"CONFIRM|{order_gid}"}]},
                {"type": "button", "sub_type": "quick_reply", "index": "1",
                 "parameters": [{"type": "payload", "payload": f"CANCEL|{order_gid}"}]},
            ]}}


def text(to, body):
    return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body[:4000]}}


# Template definition submitted to Meta for approval (UTILITY, Urdu).
TEMPLATE_DEF = {
    "name": None, "language": None, "category": "UTILITY",
    "components": [
        {"type": "BODY",
         "text": ("السلام علیکم {{1}}! {store} سے آپ کا آرڈر {{2}} موصول ہو گیا ہے۔ "
                  "کل رقم Rs {{3}} (کیش آن ڈیلیوری)۔ براہِ کرم نیچے بٹن دبا کر آرڈر کی تصدیق کریں "
                  "تاکہ ہم اسے روانہ کر سکیں۔"),
         "example": {"body_text": [["Ayesha", "#1003", "1,799"]]}},
        {"type": "BUTTONS", "buttons": [
            {"type": "QUICK_REPLY", "text": "آرڈر کی تصدیق کریں"},
            {"type": "QUICK_REPLY", "text": "آرڈر منسوخ کریں"}]},
    ]}


async def ensure_template():
    """Submit the order-confirmation template if it doesn't exist. Creating templates is free."""
    if not (S.WA_TOKEN and S.WA_WABA_ID):
        return "skipped:no_credentials"
    r = await clients.HTTP.get(_url(f"{S.WA_WABA_ID}/message_templates"), headers=_auth(),
                               params={"name": S.TEMPLATE_NAME})
    existing = [t for t in r.json().get("data", []) if t.get("name") == S.TEMPLATE_NAME]
    if existing:
        return f"exists:{existing[0].get('status')}"
    d = json.loads(json.dumps(TEMPLATE_DEF))
    d["name"], d["language"] = S.TEMPLATE_NAME, S.TEMPLATE_LANG
    d["components"][0]["text"] = d["components"][0]["text"].replace("{store}", S.STORE_NAME)
    r = await clients.HTTP.post(_url(f"{S.WA_WABA_ID}/message_templates"), headers=_auth(), json=d)
    if r.status_code >= 300:
        db.event("error", f"Template submit failed: {r.text[:300]}")
        return f"error:{r.status_code}"
    return f"submitted:{r.json().get('status')}"
