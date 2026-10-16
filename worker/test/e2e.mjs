// End-to-end test against `wrangler dev` (local workerd + local D1) with a mock Graph API.
// Usage: node test/e2e.mjs  (expects the worker on http://127.0.0.1:8788, see test/run.sh)
import http from "node:http";
import crypto from "node:crypto";
import assert from "node:assert/strict";

const W = "http://127.0.0.1:8788";
const CUST = "923234470000";
const sent = [];
const graph = http.createServer((req, res) => {
  let b = ""; req.on("data", (c) => (b += c));
  req.on("end", () => {
    if (req.url.endsWith("/messages")) { sent.push(JSON.parse(b)); res.end(JSON.stringify({ messages: [{ id: `wamid.out${sent.length}` }] })); }
    else res.end(JSON.stringify({ data: [] }));
  });
}).listen(9911);

const hk = (s, p) => crypto.createHash("sha256").update(`${s}:${p}`).digest("hex").slice(0, 24);
const HEALTH = hk("appsecret", "health"), VERIFY = hk("appsecret", "verify");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms * 2.5)); // generous: CI runners are slower
const wa = (value, secret = "appsecret") => {
  const raw = JSON.stringify({ entry: [{ changes: [{ value }] }] });
  const sig = "sha256=" + crypto.createHmac("sha256", secret).update(raw).digest("hex");
  return fetch(W + "/webhook/whatsapp", { method: "POST", body: raw, headers: { "x-hub-signature-256": sig } });
};
const shop = (o, secret = "shopsecret") => {
  const raw = JSON.stringify(o);
  const sig = crypto.createHmac("sha256", secret).update(raw).digest("base64");
  return fetch(W + "/webhook/shopify/orders-create", { method: "POST", body: raw, headers: { "x-shopify-hmac-sha256": sig } });
};
const order = (id, phone = "0323-4470000", gw = "Cash on Delivery (COD)") => ({ id, admin_graphql_api_id: `gid://shopify/Order/${id}`,
  name: `#${id}`, total_price: "1799.00", payment_gateway_names: [gw], shipping_address: { phone, first_name: "Ayesha" } });
const health = async () => (await fetch(`${W}/healthz?key=${HEALTH}`)).json();
const msg = (id, extra) => ({ messages: [{ id, from: CUST, ...extra }] });
let pass = 0;
const t = async (name, fn) => { await fn(); pass++; console.log("ok -", name); };

try {
  await t("health public", async () => { const h = await (await fetch(W + "/healthz")).json(); assert.equal(h.mode, "test"); assert.equal(h.orders, undefined); });
  await t("demo redirects to public page", async () => { const r = await fetch(W + "/demo", { redirect: "manual" }); assert.equal(r.status, 302); assert.match(r.headers.get("location"), /github\.io\/BoloCart\/demo/); });
  await t("webhook handshake", async () => {
    assert.equal(await (await fetch(`${W}/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=${VERIFY}&hub.challenge=42`)).text(), "42");
    assert.equal((await fetch(`${W}/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=bad&hub.challenge=42`)).status, 403);
  });
  await t("bad signatures rejected", async () => {
    assert.equal((await wa(msg("x1", { type: "text", text: { body: "hi" } }), "bad")).status, 401);
    assert.equal((await shop(order(1), "bad")).status, 401);
  });
  await t("COD order -> Urdu template with buttons", async () => {
    assert.equal((await shop(order(5001))).status, 200); await sleep(400);
    const tpl = sent.at(-1); assert.equal(tpl.type, "template"); assert.equal(tpl.to, CUST);
    assert.equal(tpl.template.components[1].parameters[0].payload, "CONFIRM|gid://shopify/Order/5001");
    assert.equal(tpl.template.components[0].parameters[2].text, "1,799");
  });
  await t("duplicate order webhook sends once", async () => { const n = sent.length; await shop(order(5001)); await sleep(300); assert.equal(sent.length, n); });
  await t("prepaid skipped, non-test number blocked", async () => {
    const n = sent.length; await shop(order(5002, "0323-4470000", "Bank Deposit")); await shop(order(5003, "03001112222")); await sleep(400);
    assert.equal(sent.length, n); const h = await health(); assert.equal(h.orders.skipped_prepaid, 1); assert.ok(h.out_24h["blocked:not_test_recipient"] >= 1);
  });
  await t("button confirm -> confirmed + Urdu reply", async () => {
    await wa(msg("in1", { type: "button", button: { payload: "CONFIRM|gid://shopify/Order/5001", text: "x" } })); await sleep(500);
    assert.match(sent.at(-1).text.body, /کنفرم/); assert.equal((await health()).orders.confirmed, 1);
  });
  await t("Meta retry deduplicated", async () => { const n = sent.length; await wa(msg("in1", { type: "text", text: { body: "hello" } })); await sleep(300); assert.equal(sent.length, n); });
  await t("text 'nahi chahiye' -> cancel requested", async () => {
    await shop(order(5004)); await sleep(300);
    await wa(msg("in2", { type: "text", text: { body: "nahi mujhe nahi chahiye" } })); await sleep(500);
    assert.equal((await health()).orders.cancel_requested, 1);
  });
  await t("mixed yes/no asks to clarify", async () => {
    await shop(order(5005)); await sleep(300);
    await wa(msg("in3", { type: "text", text: { body: "haan nahi" } })); await sleep(400);
    assert.match(sent.at(-1).text.body, /واضح/);
  });
  await t("question without AI -> polite handoff, flagged", async () => {
    await wa(msg("in4", { type: "text", text: { body: "refund kab milega?" } })); await sleep(400);
    assert.match(sent.at(-1).text.body, /ٹیم/); assert.ok((await health()).events_7d.some((e) => e.level === "needs_human"));
  });
  await t("voice note fallback", async () => { await wa(msg("in5", { type: "audio", audio: { id: "m" } })); await sleep(300); assert.match(sent.at(-1).text.body, /وائس نوٹ/); });
  await t("billing flags recorded", async () => {
    await wa({ statuses: [{ id: "wamid.a", status: "delivered", pricing: { billable: true, category: "utility", type: "regular" } },
      { id: "wamid.b", status: "delivered", pricing: { billable: false, category: "service", type: "free_customer_service" } }] }); await sleep(300);
    const b = (await health()).billing_7d; assert.ok(b.some((r) => r.category === "utility" && r.billable === 1)); assert.ok(b.some((r) => r.category === "service" && r.billable === 0));
  });
  await t("sample-order endpoint needs key", async () => {
    assert.equal((await fetch(`${W}/test/sample-order`, { method: "POST", body: "{}" })).status, 403);
    const r = await (await fetch(`${W}/test/sample-order?key=${HEALTH}`, { method: "POST", body: JSON.stringify({ phone: "03234470000" }) })).json();
    assert.equal(r.result.status, "pending");
  });
  console.log(`\nE2E PASSED (${pass} checks)`);
} catch (e) {
  console.error("E2E FAILED:", e.message); process.exitCode = 1;
} finally { graph.close(); }
