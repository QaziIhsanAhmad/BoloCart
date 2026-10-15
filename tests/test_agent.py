import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import clients, db, main
from app.config import S
from app.phone import normalize_pk

CUST = "923234470000"
GID = "gid://shopify/Order/555"


class Fake:
    """Records outgoing HTTP calls and returns canned responses."""

    def __init__(self):
        self.calls, self.llm_reply, self.llm_down = [], "Anti-Acne Serum ki qeemat Rs 1,799 hai.", False

    def __call__(self, req: httpx.Request):
        body = json.loads(req.content or b"{}")
        self.calls.append((req.url.host, req.url.path, body))
        if "graph.facebook.com" in req.url.host:
            if req.url.path.endswith("/messages"):
                return httpx.Response(200, json={"messages": [{"id": f"wamid.out{len(self.calls)}"}]})
            return httpx.Response(200, json={"data": []})
        if "myshopify.com" in req.url.host:
            return httpx.Response(200, json={"data": {"order": {"name": "#1003",
                                                                 "displayFulfillmentStatus": "UNFULFILLED"}}})
        if req.url.path == "/api/chat":
            if self.llm_down:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"message": {"content": self.llm_reply}})
        return httpx.Response(404)

    def wa_sends(self):
        return [b for h, p, b in self.calls if p.endswith("/messages")]

    def shop_calls(self):
        return [b for h, p, b in self.calls if "myshopify" in h]


@pytest.fixture
def env(tmp_path):
    S.DB_PATH = str(tmp_path / "t.db")
    S.SEND_MODE, S.LIVE_APPROVED, S.TEST_RECIPIENTS = "test", False, {CUST}
    S.WA_APP_SECRET, S.WA_VERIFY_TOKEN = "appsecret", "verifyme"
    S.WA_TOKEN, S.WA_PHONE_NUMBER_ID, S.WA_WABA_ID = "tok", "111", ""  # no WABA -> template setup skipped
    S.SHOP_API_SECRET, S.SHOP_TOKEN, S.SHOP_DOMAIN, S.PUBLIC_URL = "shopsecret", "shptok", "x.myshopify.com", ""
    S.MAX_TEMPLATES_PER_DAY, S.HEALTH_KEY = 30, "hk"
    S.KB_PATH = "kb/demo.md"
    db.reset()
    fake = Fake()
    clients.HTTP = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    with TestClient(main.app) as c:
        yield c, fake
    db.reset()


def order(gw="Cash on Delivery (COD)", phone="0323-4470000", oid=555):
    return {"id": oid, "admin_graphql_api_id": f"gid://shopify/Order/{oid}", "name": "#1003",
            "total_price": "1799.00", "payment_gateway_names": [gw],
            "shipping_address": {"phone": phone, "first_name": "Ayesha"}}


def post_shop(c, payload, secret="shopsecret"):
    raw = json.dumps(payload).encode()
    sig = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
    return c.post("/webhook/shopify/orders-create", content=raw, headers={"x-shopify-hmac-sha256": sig})


def post_wa(c, value, secret="appsecret"):
    raw = json.dumps({"entry": [{"changes": [{"value": value}]}]}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return c.post("/webhook/whatsapp", content=raw, headers={"x-hub-signature-256": sig})


def msg(mid, **kw):
    return {"messages": [{"id": mid, "from": CUST, **kw}]}


def status_of(gid=GID):
    return db.q("SELECT status FROM orders WHERE order_gid=?", (gid,))[0]["status"]


# ---------- unit ----------

@pytest.mark.parametrize("raw,want", [
    ("03234470000", CUST), ("0323-4470000", CUST), ("+92 323 4470000", CUST),
    ("00923234470000", CUST), ("3234470000", CUST), ("042-35761234", None), ("", None), ("12345", None)])
def test_normalize(raw, want):
    assert normalize_pk(raw) == want


# ---------- security ----------

def test_shopify_bad_hmac_rejected(env):
    c, fake = env
    assert post_shop(c, order(), secret="wrong").status_code == 401
    assert fake.wa_sends() == []


def test_whatsapp_bad_signature_rejected(env):
    c, _ = env
    assert post_wa(c, msg("w1", type="text", text={"body": "hi"}), secret="bad").status_code == 401


def test_whatsapp_verify(env):
    c, _ = env
    ok = c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verifyme",
                                            "hub.challenge": "42"})
    assert ok.text == "42"
    assert c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x"}).status_code == 403


# ---------- order flow ----------

def test_cod_order_sends_template_with_buttons(env):
    c, fake = env
    assert post_shop(c, order()).status_code == 200
    sends = fake.wa_sends()
    assert len(sends) == 1
    t = sends[0]
    assert t["to"] == CUST and t["type"] == "template"
    assert t["template"]["components"][0]["parameters"][2]["text"] == "1,799"
    assert t["template"]["components"][1]["parameters"][0]["payload"] == f"CONFIRM|{GID}"
    assert status_of() == "pending"
    assert any(b["variables"].get("t") == ["WA-Pending"] for b in fake.shop_calls())


def test_duplicate_order_webhook_sends_once(env):
    c, fake = env
    post_shop(c, order())
    post_shop(c, order())
    assert len(fake.wa_sends()) == 1


def test_prepaid_order_skipped(env):
    c, fake = env
    post_shop(c, order(gw="Bank Deposit"))
    assert fake.wa_sends() == [] and status_of() == "skipped_prepaid"


def test_landline_skipped(env):
    c, fake = env
    post_shop(c, order(phone="042-35761234"))
    assert fake.wa_sends() == [] and status_of() == "skipped_no_phone"


# ---------- cost guard ----------

def test_dry_run_sends_nothing(env):
    c, fake = env
    S.SEND_MODE = "dry_run"
    post_shop(c, order())
    assert fake.wa_sends() == []
    assert db.q("SELECT result FROM messages WHERE direction='out'")[0]["result"] == "blocked:dry_run"


def test_test_mode_blocks_unlisted_number(env):
    c, fake = env
    post_shop(c, order(phone="03001112222"))
    assert fake.wa_sends() == []


def test_live_requires_approval(env):
    c, fake = env
    S.SEND_MODE = "live"
    post_shop(c, order())
    assert fake.wa_sends() == []
    S.LIVE_APPROVED = True
    post_shop(c, order(oid=556))
    assert len(fake.wa_sends()) == 1


def test_daily_template_cap(env):
    c, fake = env
    S.MAX_TEMPLATES_PER_DAY = 1
    post_shop(c, order(oid=1))
    post_shop(c, order(oid=2))
    assert len(fake.wa_sends()) == 1


def test_no_free_text_outside_window(env):
    c, fake = env
    import asyncio
    from app import whatsapp as wa
    r = asyncio.run(wa.send(wa.text(CUST, "hi"), "session"))
    assert r == {"blocked": "window_closed"}


# ---------- replies ----------

def test_button_confirm(env):
    c, fake = env
    post_shop(c, order())
    post_wa(c, msg("w1", type="button", button={"payload": f"CONFIRM|{GID}", "text": "آرڈر کی تصدیق کریں"}))
    assert status_of() == "confirmed"
    assert "کنفرم" in fake.wa_sends()[-1]["text"]["body"]
    assert any(b["variables"].get("t") == ["WA-Confirmed"] for b in fake.shop_calls())


def test_button_cancel_does_not_cancel_in_shopify(env):
    c, fake = env
    post_shop(c, order())
    post_wa(c, msg("w1", type="button", button={"payload": f"CANCEL|{GID}", "text": "x"}))
    assert status_of() == "cancel_requested"
    assert not any("orderCancel" in b["query"] for b in fake.shop_calls())


def test_text_haan_confirms(env):
    c, _ = env
    post_shop(c, order())
    post_wa(c, msg("w1", type="text", text={"body": "Haan!"}))
    assert status_of() == "confirmed"


def test_text_urdu_nahi_cancels(env):
    c, _ = env
    post_shop(c, order())
    post_wa(c, msg("w1", type="text", text={"body": "نہیں"}))
    assert status_of() == "cancel_requested"


def test_faq_uses_llm_with_kb(env):
    c, fake = env
    post_wa(c, msg("w1", type="text", text={"body": "anti acne serum ki price kya hai?"}))
    chat = [b for h, p, b in fake.calls if p == "/api/chat"][0]
    assert "Rs 1,799" in chat["messages"][0]["content"]
    assert fake.wa_sends()[-1]["text"]["body"] == fake.llm_reply


def test_status_question_includes_order_context(env):
    c, fake = env
    post_shop(c, order())
    post_wa(c, msg("w1", type="text", text={"body": "mera order kab aayega?"}))
    chat = [b for h, p, b in fake.calls if p == "/api/chat"][0]
    assert "#1003" in chat["messages"][0]["content"]


def test_llm_down_hands_off(env):
    c, fake = env
    fake.llm_down = True
    post_wa(c, msg("w1", type="text", text={"body": "refund chahiye"}))
    assert fake.wa_sends()[-1]["text"]["body"] == main.MSG_HANDOFF
    assert db.q("SELECT COUNT(*) n FROM events WHERE level='needs_human'")[0]["n"] == 1


def test_voice_note_fallback(env):
    c, fake = env
    post_wa(c, msg("w1", type="audio", audio={"id": "m1"}))
    assert fake.wa_sends()[-1]["text"]["body"] == main.MSG_VOICE


def test_meta_retry_deduplicated(env):
    c, fake = env
    post_wa(c, msg("w1", type="text", text={"body": "hello"}))
    post_wa(c, msg("w1", type="text", text={"body": "hello"}))
    assert len(fake.wa_sends()) == 1


# ---------- billing verification + health ----------

def test_billing_recorded_and_reported(env):
    c, _ = env
    post_wa(c, {"statuses": [
        {"id": "wamid.a", "status": "delivered", "pricing": {"billable": True, "category": "utility", "type": "regular"}},
        {"id": "wamid.b", "status": "delivered",
         "pricing": {"billable": False, "category": "service", "type": "free_customer_service"}}]})
    h = c.get("/healthz", params={"key": "hk"}).json()
    got = {(r["category"], r["billable"]) for r in h["billing_7d"]}
    assert got == {("utility", 1), ("service", 0)}
    assert "orders" not in c.get("/healthz").json()  # details need the key


def test_no_reply_sweep(env):
    c, fake = env
    import asyncio
    post_shop(c, order())
    db.x("UPDATE orders SET created_at=?", (time.time() - 25 * 3600,))
    asyncio.run(main.sweep_once())
    assert status_of() == "no_reply"


@pytest.mark.parametrize("text,want", [
    ("nahi mujhe nahi chahiye", "cancel_requested"), ("order cancel kar do", "cancel_requested"),
    ("ji theek hai", "confirmed"), ("haan bhej do", "confirmed"), ("haan nahi", "pending")])
def test_free_text_decisions(env, text, want):
    c, _ = env
    post_shop(c, order())
    post_wa(c, msg("w1", type="text", text={"body": text}))
    assert status_of() == want


def test_question_with_pending_order_goes_to_llm(env):
    c, fake = env
    post_shop(c, order())
    post_wa(c, msg("w1", type="text", text={"body": "ji, kitne din lagenge?"}))
    assert status_of() == "pending"
    assert any(p == "/api/chat" for h, p, b in fake.calls)
