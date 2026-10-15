"""Run sample COD orders through the real agent code.

Shopify, WhatsApp and the LLM are simulated in-process, so nothing is sent and nothing is charged.
Usage:  python -m demo.run_demo        (from the wa-agent folder)
Set OLLAMA_URL to a running Ollama to use the real model for FAQ answers instead of canned ones.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile

import httpx
from fastapi.testclient import TestClient

from app import clients, db, main
from app.config import S

USE_REAL_LLM = bool(os.getenv("DEMO_REAL_LLM"))
CANNED = {
    "price": "Anti-Acne Serum ki qeemat Rs 1,799 hai. Yeh oily aur acne-prone skin ke liye hai.",
    "order": "Aap ka order #2003 confirm ho chuka hai aur abhi dispatch ka intezar hai.",
}

ORDERS = [  # (order, customer phone as typed at checkout, customer's reply)
    ({"id": 2001, "name": "#2001", "total_price": "1799.00", "first": "Ayesha"},
     "0323-4470001", {"type": "button", "payload": "CONFIRM"}),
    ({"id": 2002, "name": "#2002", "total_price": "4799.00", "first": "Bilal"},
     "+92 300 1112222", {"type": "text", "body": "nahi mujhe nahi chahiye"}),
    ({"id": 2003, "name": "#2003", "total_price": "3598.00", "first": "Sana"},
     "03451234567", {"type": "text", "body": "Haan"}),
    ({"id": 2004, "name": "#2004", "total_price": "1799.00", "first": "Usman"},
     "03219876543", None),  # never replies -> WA-NoReply after 24h
    ({"id": 2005, "name": "#2005", "total_price": "1799.00", "first": "Hina", "prepaid": True},
     "03331231234", None),  # prepaid -> no message needed
]
FAQ = [("923451234567", "anti acne serum ki price kya hai?"),
       ("923451234567", "mera order kab aayega?"),
       ("923451234567", "mujhe refund chahiye")]


class SimNet:
    def __init__(self):
        self.log = []

    def __call__(self, req):
        body = json.loads(req.content or b"{}")
        host = req.url.host
        if "graph.facebook.com" in host and req.url.path.endswith("/messages"):
            self.log.append(("wa", body))
            return httpx.Response(200, json={"messages": [{"id": f"wamid.sim{len(self.log)}"}]})
        if "myshopify" in host:
            v = body.get("variables", {})
            if "tagsAdd" in body.get("query", ""):
                self.log.append(("tag", v))
            return httpx.Response(200, json={"data": {"order": {"name": "#2003",
                                  "displayFulfillmentStatus": "UNFULFILLED"}}})
        if req.url.path == "/api/chat":
            q = body["messages"][-1]["content"].lower()
            if "refund" in q:
                out = "HANDOFF"
            else:
                out = CANNED["order"] if "order" in q else CANNED["price"]
            return httpx.Response(200, json={"message": {"content": out}})
        return httpx.Response(404)


def main_demo():
    tmp = tempfile.mkdtemp()
    S.DB_PATH = os.path.join(tmp, "demo.db")
    S.SEND_MODE, S.LIVE_APPROVED = "live", True  # safe: the network below is simulated
    S.WA_TOKEN, S.WA_PHONE_NUMBER_ID, S.WA_WABA_ID = "sim", "sim", ""
    S.WA_APP_SECRET, S.SHOP_API_SECRET = "sim-app", "sim-shop"
    S.SHOP_TOKEN, S.SHOP_DOMAIN, S.PUBLIC_URL, S.HEALTH_KEY = "sim", "demo.myshopify.com", "", "demo"
    db.reset()
    net = SimNet()
    clients.HTTP = httpx.AsyncClient(transport=httpx.MockTransport(net))
    if USE_REAL_LLM:
        print("(using real Ollama for FAQ answers)")

    def shop(payload):
        raw = json.dumps(payload).encode()
        sig = base64.b64encode(hmac.new(b"sim-shop", raw, hashlib.sha256).digest()).decode()
        return c.post("/webhook/shopify/orders-create", content=raw, headers={"x-shopify-hmac-sha256": sig})

    def wa(value):
        raw = json.dumps({"entry": [{"changes": [{"value": value}]}]}).encode()
        sig = "sha256=" + hmac.new(b"sim-app", raw, hashlib.sha256).hexdigest()
        return c.post("/webhook/whatsapp", content=raw, headers={"x-hub-signature-256": sig})

    def show_new(start):
        for kind, b in net.log[start:]:
            if kind == "wa" and b["type"] == "template":
                p = [x["text"] for x in b["template"]["components"][0]["parameters"]]
                print(f"   BOT -> {b['to']}: [template] Salam {p[0]}, order {p[1]}, Rs {p[2]} (COD) "
                      f"[Confirm] [Cancel]")
            elif kind == "wa":
                print(f"   BOT -> {b['to']}: {b['text']['body']}")
            elif kind == "tag":
                print(f"   SHOPIFY tag {v_short(b)}")
        return len(net.log)

    def v_short(v):
        return f"{v['id'].split('/')[-1]} + {v['t']}"

    with TestClient(main.app) as c:
        mark = 0
        print("\n=== 1. New orders arrive from the store ===")
        for o, phone, _ in ORDERS:
            gw = "Bank Deposit" if o.get("prepaid") else "Cash on Delivery (COD)"
            print(f"\n ORDER {o['name']}  Rs {o['total_price']}  {gw}  phone '{phone}'")
            assert shop({"id": o["id"], "admin_graphql_api_id": f"gid://shopify/Order/{o['id']}",
                         "name": o["name"], "total_price": o["total_price"], "payment_gateway_names": [gw],
                         "shipping_address": {"phone": phone, "first_name": o["first"]}}).status_code == 200
            mark = show_new(mark)

        print("\n=== 2. Customers reply on WhatsApp ===")
        for i, (o, phone, reply) in enumerate(ORDERS):
            if not reply:
                continue
            wid = db.q("SELECT phone FROM orders WHERE order_name=?", (o["name"],))[0]["phone"]
            gid = f"gid://shopify/Order/{o['id']}"
            if reply["type"] == "button":
                m = {"id": f"in{i}", "from": wid, "type": "button",
                     "button": {"payload": f"{reply['payload']}|{gid}", "text": "آرڈر کی تصدیق کریں"}}
                print(f"\n CUSTOMER {wid}: [taps Confirm]")
            else:
                m = {"id": f"in{i}", "from": wid, "type": "text", "text": {"body": reply["body"]}}
                print(f"\n CUSTOMER {wid}: {reply['body']}")
            wa({"messages": [m]})
            mark = show_new(mark)

        print("\n=== 3. Customer questions ===")
        for j, (wid, text) in enumerate(FAQ):
            print(f"\n CUSTOMER {wid}: {text}")
            wa({"messages": [{"id": f"faq{j}", "from": wid, "type": "text", "text": {"body": text}}]})
            mark = show_new(mark)

        print("\n=== 4. 24 hours later: no-reply sweep ===")
        import asyncio
        import time
        db.x("UPDATE orders SET created_at=?", (time.time() - 25 * 3600,))
        asyncio.run(main.sweep_once())
        mark = show_new(mark)

        h = c.get("/healthz", params={"key": "demo"}).json()
        print("\n=== 5. Result ===")
        print(" Orders by status:", h["orders"])
        print(" Handoffs to a human:", [e["text"] for e in h["events_7d"] if e["level"] == "needs_human"])
        ok = h["orders"] == {"confirmed": 2, "cancel_requested": 1, "no_reply": 1, "skipped_prepaid": 1}
        print("\nDEMO", "PASSED" if ok else "FAILED")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_demo())
