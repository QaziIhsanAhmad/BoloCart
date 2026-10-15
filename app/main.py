import asyncio
import contextlib
import hashlib
import hmac
import json
import re
import time

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pathlib import Path

from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from . import db, llm, shopify, whatsapp as wa
from .config import S
from .phone import normalize_pk

CONFIRM_WORDS = {"haan", "han", "ha", "haa", "ji", "jee", "g", "yes", "y", "confirm", "confirmed", "ok",
                 "okay", "theek", "theek hai", "thik hai", "ہاں", "ہاں جی", "جی", "جی ہاں", "ٹھیک ہے",
                 "کنفرم", "تصدیق", "آرڈر کی تصدیق کریں"}
CANCEL_WORDS = {"nahi", "nhi", "nai", "no", "n", "cancel", "mansookh", "nahi chahiye", "نہیں", "منسوخ",
                "کینسل", "نہیں چاہیے", "آرڈر منسوخ کریں"}
STATUS_HINTS = ("order", "status", "kab", "kahan", "kahaan", "delivery", "track", "parcel",
                "آرڈر", "کب", "کہاں", "ڈیلیوری", "پارسل")

MSG_CONFIRMED = "شکریہ! آپ کا آرڈر {name} کنفرم ہو گیا ہے اور جلد روانہ کر دیا جائے گا۔"
MSG_CANCELLED = "آپ کا آرڈر {name} منسوخ کرنے کی درخواست موصول ہو گئی ہے۔ اگر یہ غلطی سے ہوا ہے تو یہیں بتا دیں۔"
MSG_HANDOFF = "شکریہ! ہماری ٹیم جلد آپ سے رابطہ کرے گی۔"
MSG_VOICE = "آپ کا وائس نوٹ موصول ہو گیا ہے۔ فی الحال براہِ کرم اپنا سوال لکھ کر بھیجیں، شکریہ!"


MSG_UNCLEAR = "براہِ کرم واضح کریں: آرڈر کنفرم کرنے کے لیے \"ہاں\" اور منسوخ کرنے کے لیے \"نہیں\" لکھیں۔"
CANCEL_TOKENS = {"nahi", "nhi", "nai", "nahin", "no", "cancel", "mansookh", "نہیں", "منسوخ", "کینسل"}


def _norm(t):
    return re.sub(r"[^\w\s؀-ۿ]", "", (t or "").strip().lower())


def _decision(n):
    """CONFIRM / CANCEL / UNCLEAR / None for a short reply to a pending order."""
    words = set(n.split())
    cancel = n in CANCEL_WORDS or bool(words & CANCEL_TOKENS)
    confirm = n in CONFIRM_WORDS or bool(words & {w for w in CONFIRM_WORDS if " " not in w})
    if cancel and confirm:
        return "UNCLEAR"
    return "CANCEL" if cancel else "CONFIRM" if confirm else None


# ---------------- Shopify: new order -> confirmation template ----------------

async def handle_order(o):
    gid = o.get("admin_graphql_api_id") or f"gid://shopify/Order/{o.get('id')}"
    if db.q("SELECT 1 FROM orders WHERE order_gid=?", (gid,)):
        return
    gateways = " ".join(o.get("payment_gateway_names") or []).lower()
    ship, bill, cust = o.get("shipping_address") or {}, o.get("billing_address") or {}, o.get("customer") or {}
    phone = next((p for p in (normalize_pk(ship.get("phone")), normalize_pk(bill.get("phone")),
                              normalize_pk(o.get("phone")), normalize_pk(cust.get("phone"))) if p), None)
    first = ship.get("first_name") or cust.get("first_name") or ""
    try:
        total = f"{float(o.get('total_price') or 0):,.0f}"
    except ValueError:
        total = str(o.get("total_price"))
    is_cod = "cash on delivery" in gateways or "cod" in gateways.split() or "(cod)" in gateways
    status = "pending" if (is_cod and phone) else ("skipped_no_phone" if is_cod else "skipped_prepaid")
    now = time.time()
    db.x("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,NULL)", (gid, o.get("name"), phone, first, total, status, now, now))
    if status != "pending":
        return
    res = await wa.send(wa.order_template(phone, first, o.get("name"), total, gid), "template")
    if res.get("ok") is False:
        db.set_status(gid, "send_failed")
        await shopify.tag(gid, ["WA-SendFailed"])
        return
    if res.get("wamid"):
        db.x("UPDATE orders SET wamid=? WHERE order_gid=?", (res["wamid"], gid))
    await shopify.tag(gid, ["WA-Pending"])


# ---------------- WhatsApp: inbound messages ----------------

async def _resolve(order, decision, phone):
    if not order or order["status"] != "pending":
        return False
    if decision == "CONFIRM":
        db.set_status(order["order_gid"], "confirmed")
        await shopify.tag(order["order_gid"], ["WA-Confirmed"], ["WA-Pending"])
        await wa.send(wa.text(phone, MSG_CONFIRMED.format(name=order["order_name"])), "session")
    else:
        db.set_status(order["order_gid"], "cancel_requested")
        await shopify.tag(order["order_gid"], ["WA-CancelRequested"], ["WA-Pending"])
        await wa.send(wa.text(phone, MSG_CANCELLED.format(name=order["order_name"])), "session")
        db.event("action", f"Cancel requested for {order['order_name']} — review in Shopify")
    return True


async def handle_message(m):
    phone, mtype = m.get("from"), m.get("type")
    payload = None
    if mtype == "button":
        payload = (m.get("button") or {}).get("payload")
    elif mtype == "interactive":
        payload = ((m.get("interactive") or {}).get("button_reply") or {}).get("id")
    if payload and "|" in payload:
        decision, gid = payload.split("|", 1)
        r = db.q("SELECT * FROM orders WHERE order_gid=? AND phone=?", (gid, phone))
        if await _resolve(r[0] if r else None, decision, phone):
            return

    if mtype in ("audio", "voice"):
        await wa.send(wa.text(phone, MSG_VOICE), "session")  # Week 2: Whisper transcription
        return

    body = (m.get("text") or {}).get("body") if mtype == "text" else (m.get("button") or {}).get("text")
    if not body:
        await wa.send(wa.text(phone, MSG_HANDOFF), "session")
        db.event("needs_human", f"{phone}: unsupported message type {mtype}")
        return

    n = _norm(body)
    pending = db.latest_pending(phone)
    if pending and len(n) <= 40 and "?" not in body and "؟" not in body:
        decision = _decision(n)
        if decision == "UNCLEAR":
            await wa.send(wa.text(phone, MSG_UNCLEAR), "session")
            return
        if decision:
            await _resolve(pending, decision, phone)
            return

    extra = ""
    if any(h in n for h in STATUS_HINTS):
        last = db.latest_order(phone)
        if last:
            info = await shopify.order_status(last["order_gid"]) if S.SHOP_TOKEN else None
            extra = (f"CUSTOMER'S LATEST ORDER: {last['order_name']}, total Rs {last['total']}, "
                     f"confirmation status: {last['status']}, shopify: {json.dumps(info, ensure_ascii=False)}")
    reply = await llm.answer(body, extra)
    if reply == llm.HANDOFF:
        db.event("needs_human", f"{phone}: {body[:200]}")
        reply = MSG_HANDOFF
    await wa.send(wa.text(phone, reply), "session")


def record_status(s):
    p = s.get("pricing") or {}
    if p:
        db.x("INSERT OR REPLACE INTO costs VALUES(?,?,?,?,?)",
             (s.get("id"), p.get("category"), int(bool(p.get("billable"))), p.get("type"), time.time()))
    if s.get("status") == "failed":
        db.event("error", f"Delivery failed {s.get('recipient_id')}: {str(s.get('errors'))[:200]}")


# ---------------- Background sweep ----------------

async def sweep_once():
    cutoff = time.time() - S.NO_REPLY_HOURS * 3600
    for o in db.q("SELECT * FROM orders WHERE status='pending' AND created_at<?", (cutoff,)):
        db.set_status(o["order_gid"], "no_reply")
        await shopify.tag(o["order_gid"], ["WA-NoReply"], ["WA-Pending"])


async def _sweeper():
    while True:
        with contextlib.suppress(Exception):
            await sweep_once()
        await asyncio.sleep(900)


@contextlib.asynccontextmanager
async def lifespan(app):
    db.conn()
    app.state.setup = {}
    for name, fn in (("template", wa.ensure_template), ("webhook", shopify.ensure_webhook)):
        try:
            app.state.setup[name] = await fn()
        except Exception as e:
            app.state.setup[name] = f"error:{e!r}"[:200]
            db.event("error", f"setup {name}: {e!r}"[:300])
    task = asyncio.create_task(_sweeper())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/webhook/whatsapp")
def wa_verify(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and S.WA_VERIFY_TOKEN and p.get("hub.verify_token") == S.WA_VERIFY_TOKEN:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403)


@app.post("/webhook/whatsapp")
async def wa_webhook(request: Request, bg: BackgroundTasks):
    raw = await request.body()
    sig = request.headers.get("x-hub-signature-256", "")
    good = "sha256=" + hmac.new(S.WA_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not S.WA_APP_SECRET or not hmac.compare_digest(sig, good):
        raise HTTPException(401)
    data = json.loads(raw or b"{}")
    for entry in data.get("entry", []):
        for ch in entry.get("changes", []):
            v = ch.get("value") or {}
            for s in v.get("statuses", []):
                record_status(s)
            for m in v.get("messages", []):
                if db.seen_inbound(m.get("id")):
                    continue
                db.touch_window(m.get("from"))
                bg.add_task(handle_message, m)
    return {"ok": True}


@app.post("/webhook/shopify/orders-create")
async def shop_webhook(request: Request, bg: BackgroundTasks):
    raw = await request.body()
    if not shopify.verify_hmac(raw, request.headers.get("x-shopify-hmac-sha256", "")):
        raise HTTPException(401)
    bg.add_task(handle_order, json.loads(raw))
    return {"ok": True}


@app.get("/healthz")
def healthz(key: str = ""):
    base = {"ok": True, "mode": S.SEND_MODE, "live_approved": S.LIVE_APPROVED}
    if not S.HEALTH_KEY or key != S.HEALTH_KEY:
        return base
    day, week = time.time() - 86400, time.time() - 7 * 86400
    base.update({
        "setup": getattr(app.state, "setup", {}),
        "orders": {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM orders GROUP BY status")},
        "out_24h": {r["result"]: r["n"] for r in db.q(
            "SELECT result, COUNT(*) n FROM messages WHERE direction='out' AND ts>? GROUP BY result", (day,))},
        "billing_7d": [dict(r) for r in db.q(
            "SELECT category, pricing_type, billable, COUNT(*) n FROM costs WHERE ts>? "
            "GROUP BY category, pricing_type, billable", (week,))],
        "events_7d": [dict(r) for r in db.q(
            "SELECT level, text, ts FROM events WHERE ts>? ORDER BY ts DESC LIMIT 20", (week,))],
    })
    return base


DEMO = Path(__file__).resolve().parent.parent / "demo" / "index.html"


@app.get("/demo", response_class=HTMLResponse)
def demo():
    if not DEMO.exists():
        raise HTTPException(404)
    return HTMLResponse(DEMO.read_text(encoding="utf-8"))


@app.get("/")
def root():
    return RedirectResponse("/demo")
