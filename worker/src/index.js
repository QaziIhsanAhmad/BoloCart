// BoloCart on Cloudflare Workers (free plan): Urdu WhatsApp confirmation for COD orders.
// Storage: D1. FAQ answers: Workers AI (free daily allowance), falls back to a human handoff.

const CONFIRM_WORDS = new Set(["haan", "han", "ha", "haa", "ji", "jee", "g", "yes", "y", "confirm", "confirmed",
  "ok", "okay", "theek", "theek hai", "thik hai", "ہاں", "ہاں جی", "جی", "جی ہاں", "ٹھیک ہے", "کنفرم", "تصدیق",
  "آرڈر کی تصدیق کریں"]);
const CANCEL_WORDS = new Set(["nahi", "nhi", "nai", "no", "n", "cancel", "mansookh", "nahi chahiye", "نہیں",
  "منسوخ", "کینسل", "نہیں چاہیے", "آرڈر منسوخ کریں"]);
const CANCEL_TOKENS = new Set(["nahi", "nhi", "nai", "nahin", "no", "cancel", "mansookh", "نہیں", "منسوخ", "کینسل"]);
const CONFIRM_TOKENS = new Set([...CONFIRM_WORDS].filter((w) => !w.includes(" ")));
const STATUS_HINTS = ["order", "status", "kab", "kahan", "kahaan", "delivery", "track", "parcel",
  "آرڈر", "کب", "کہاں", "ڈیلیوری", "پارسل"];

export const MSG = {
  confirmed: (n) => `شکریہ! آپ کا آرڈر ${n} کنفرم ہو گیا ہے اور جلد روانہ کر دیا جائے گا۔`,
  cancelled: (n) => `آپ کا آرڈر ${n} منسوخ کرنے کی درخواست موصول ہو گئی ہے۔ اگر یہ غلطی سے ہوا ہے تو یہیں بتا دیں۔`,
  handoff: "شکریہ! ہماری ٹیم جلد آپ سے رابطہ کرے گی۔",
  voice: "آپ کا وائس نوٹ موصول ہو گیا ہے۔ فی الحال براہِ کرم اپنا سوال لکھ کر بھیجیں، شکریہ!",
  unclear: "براہِ کرم واضح کریں: آرڈر کنفرم کرنے کے لیے \"ہاں\" اور منسوخ کرنے کے لیے \"نہیں\" لکھیں۔",
};

const KB = `Demo Store (fictional, for demonstration). Ships to Pakistan only. Payment: Cash on Delivery.
Sample products (PKR): Vitamin C Serum Rs 1,799 (daily brightening); Anti-Acne Serum Rs 1,799 (oily and
acne-prone skin, patch test before first use); Bright Serum Rs 1,799 (dark spots, uneven tone);
3-in-1 Serum Set Rs 4,799 (all three). Orders are dispatched after confirmation; sample delivery time
2-4 working days. Refunds, exchanges, discounts and complaints: hand off to the team.`;

const now = () => Math.floor(Date.now() / 1000);
const json = (d, s = 200) => new Response(JSON.stringify(d), { status: s, headers: { "content-type": "application/json" } });

export function normalizePk(raw) {
  if (!raw) return null;
  let d = String(raw).replace(/\D/g, "");
  if (d.startsWith("0092")) d = d.slice(2);
  if (d.startsWith("92") && d.length === 12) { /* ok */ }
  else if (d.startsWith("0") && d.length === 11) d = "92" + d.slice(1);
  else if (d.startsWith("3") && d.length === 10) d = "92" + d;
  else return null;
  return d[2] === "3" ? d : null;
}

const norm = (t) => (t || "").trim().toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, "").replace(/\s+/g, " ");

export function decision(n) {
  const words = new Set(n.split(" "));
  const cancel = CANCEL_WORDS.has(n) || [...words].some((w) => CANCEL_TOKENS.has(w));
  const confirm = CONFIRM_WORDS.has(n) || [...words].some((w) => CONFIRM_TOKENS.has(w));
  if (cancel && confirm) return "UNCLEAR";
  return cancel ? "CANCEL" : confirm ? "CONFIRM" : null;
}

async function sha256hex(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
}
const derive = async (secret, purpose) => (secret ? (await sha256hex(`${secret}:${purpose}`)).slice(0, 24) : "");

async function hmac(secret, raw, enc) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, raw));
  return enc === "base64" ? btoa(String.fromCharCode(...sig)) : [...sig].map((x) => x.toString(16).padStart(2, "0")).join("");
}
function safeEq(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

// ---------------- storage ----------------
const db = (env) => env.DB;
const q = async (env, sql, ...a) => (await db(env).prepare(sql).bind(...a).all()).results || [];
const x = (env, sql, ...a) => db(env).prepare(sql).bind(...a).run();
const event = (env, level, text) => x(env, "INSERT INTO events(level,text,ts) VALUES(?,?,?)", level, String(text).slice(0, 500), now());

async function seenInbound(env, wamid) {
  try { await x(env, "INSERT INTO messages(direction,wamid,ts) VALUES('in',?,?)", wamid, now()); return false; }
  catch { return true; }
}
async function windowOpen(env, phone) {
  const r = await q(env, "SELECT last_inbound FROM windows WHERE phone=?", phone);
  return r.length > 0 && now() - r[0].last_inbound < 23.5 * 3600;
}
async function templatesToday(env) {
  const r = await q(env, "SELECT COUNT(*) n FROM messages WHERE direction='out' AND kind='template' AND result='sent' AND ts>?", now() - 86400);
  return r[0].n;
}
const setStatus = (env, gid, s) => x(env, "UPDATE orders SET status=?, updated_at=? WHERE order_gid=?", s, now(), gid);

// ---------------- WhatsApp (with cost guard) ----------------
async function guard(env, to, kind) {
  const mode = env.SEND_MODE || "dry_run";
  if (mode === "dry_run") return "dry_run";
  const allow = new Set((env.TEST_RECIPIENTS || "").replace(/\s/g, "").split(",").filter(Boolean));
  if (mode === "test" && !allow.has(to)) return "not_test_recipient";
  if (mode === "live" && env.LIVE_APPROVED !== "yes") return "live_not_approved";
  if (mode !== "test" && mode !== "live") return "bad_mode";
  if (!env.WA_TOKEN || !env.WA_PHONE_NUMBER_ID) return "no_credentials";
  if (kind === "template" && (await templatesToday(env)) >= Number(env.MAX_TEMPLATES_PER_DAY || 20)) return "daily_template_cap";
  if (kind === "session" && !(await windowOpen(env, to))) return "window_closed";
  return null;
}

export async function send(env, payload, kind) {
  const to = payload.to;
  const body = JSON.stringify(payload);
  const reason = await guard(env, to, kind);
  if (reason) {
    await x(env, "INSERT INTO messages(direction,phone,kind,body,result,ts) VALUES('out',?,?,?,?,?)", to, kind, body, `blocked:${reason}`, now());
    return { blocked: reason };
  }
  const r = await fetch(`${env.GRAPH_BASE}/${env.WA_PHONE_NUMBER_ID}/messages`, {
    method: "POST", headers: { authorization: `Bearer ${env.WA_TOKEN}`, "content-type": "application/json" }, body });
  const data = await r.json().catch(() => ({}));
  const ok = r.status < 300;
  const wamid = ok ? data?.messages?.[0]?.id || null : null;
  await x(env, "INSERT INTO messages(direction,phone,wamid,kind,body,result,ts) VALUES('out',?,?,?,?,?,?)",
    to, wamid, kind, body, ok ? "sent" : `error:${r.status}`, now());
  if (!ok) await event(env, "error", `WhatsApp send failed ${r.status}: ${JSON.stringify(data).slice(0, 300)}`);
  return { ok, wamid, status: r.status };
}

export const textMsg = (to, body) => ({ messaging_product: "whatsapp", to, type: "text", text: { body: body.slice(0, 4000) } });
export function orderTemplate(env, to, first, name, total, gid) {
  return { messaging_product: "whatsapp", to, type: "template", template: {
    name: env.TEMPLATE_NAME, language: { code: env.TEMPLATE_LANG }, components: [
      { type: "body", parameters: [{ type: "text", text: first || "Customer" }, { type: "text", text: name }, { type: "text", text: total }] },
      { type: "button", sub_type: "quick_reply", index: "0", parameters: [{ type: "payload", payload: `CONFIRM|${gid}` }] },
      { type: "button", sub_type: "quick_reply", index: "1", parameters: [{ type: "payload", payload: `CANCEL|${gid}` }] }] } };
}

async function ensureTemplate(env) {
  if (!env.WA_TOKEN || !env.WA_WABA_ID) return "skipped:no_credentials";
  const auth = { authorization: `Bearer ${env.WA_TOKEN}` };
  const r = await fetch(`${env.GRAPH_BASE}/${env.WA_WABA_ID}/message_templates?name=${env.TEMPLATE_NAME}`, { headers: auth });
  const existing = ((await r.json().catch(() => ({}))).data || []).find((t) => t.name === env.TEMPLATE_NAME);
  if (existing) return `exists:${existing.status}`;
  const def = { name: env.TEMPLATE_NAME, language: env.TEMPLATE_LANG, category: "UTILITY", components: [
    { type: "BODY", text: `السلام علیکم {{1}}! ${env.STORE_NAME} سے آپ کا آرڈر {{2}} موصول ہو گیا ہے۔ کل رقم Rs {{3}} (کیش آن ڈیلیوری)۔ براہِ کرم نیچے بٹن دبا کر آرڈر کی تصدیق کریں تاکہ ہم اسے روانہ کر سکیں۔`,
      example: { body_text: [["Ayesha", "#1003", "1,799"]] } },
    { type: "BUTTONS", buttons: [{ type: "QUICK_REPLY", text: "آرڈر کی تصدیق کریں" }, { type: "QUICK_REPLY", text: "آرڈر منسوخ کریں" }] }] };
  const c = await fetch(`${env.GRAPH_BASE}/${env.WA_WABA_ID}/message_templates`, {
    method: "POST", headers: { ...auth, "content-type": "application/json" }, body: JSON.stringify(def) });
  const d = await c.json().catch(() => ({}));
  if (c.status >= 300) { await event(env, "error", `Template submit failed: ${JSON.stringify(d).slice(0, 300)}`); return `error:${c.status}`; }
  return `submitted:${d.status}`;
}

// ---------------- Shopify (only when a client store is connected) ----------------
async function shopTag(env, gid, add, remove = []) {
  if (!env.SHOP_TOKEN || !env.SHOP_DOMAIN) return;
  const call = (query, variables) => fetch(`https://${env.SHOP_DOMAIN}/admin/api/2026-04/graphql.json`, {
    method: "POST", headers: { "X-Shopify-Access-Token": env.SHOP_TOKEN, "content-type": "application/json" },
    body: JSON.stringify({ query, variables }) });
  await call("mutation($id:ID!,$t:[String!]!){tagsAdd(id:$id,tags:$t){userErrors{message}}}", { id: gid, t: add });
  if (remove.length) await call("mutation($id:ID!,$t:[String!]!){tagsRemove(id:$id,tags:$t){userErrors{message}}}", { id: gid, t: remove });
}

// ---------------- core flows ----------------
export async function handleOrder(env, o) {
  const gid = o.admin_graphql_api_id || `gid://shopify/Order/${o.id}`;
  if ((await q(env, "SELECT 1 FROM orders WHERE order_gid=?", gid)).length) return;
  const gw = (o.payment_gateway_names || []).join(" ").toLowerCase();
  const ship = o.shipping_address || {}, bill = o.billing_address || {}, cust = o.customer || {};
  const phone = [ship.phone, bill.phone, o.phone, cust.phone].map(normalizePk).find(Boolean) || null;
  const first = ship.first_name || cust.first_name || "";
  const total = Number(o.total_price || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
  const isCod = gw.includes("cash on delivery") || gw.split(/\s+/).includes("cod") || gw.includes("(cod)");
  const status = isCod && phone ? "pending" : isCod ? "skipped_no_phone" : "skipped_prepaid";
  await x(env, "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,NULL)", gid, o.name, phone, first, total, status, now(), now());
  if (status !== "pending") return { status };
  const res = await send(env, orderTemplate(env, phone, first, o.name, total, gid), "template");
  if (res.ok === false) { await setStatus(env, gid, "send_failed"); await shopTag(env, gid, ["WA-SendFailed"]); return { status: "send_failed" }; }
  if (res.wamid) await x(env, "UPDATE orders SET wamid=? WHERE order_gid=?", res.wamid, gid);
  await shopTag(env, gid, ["WA-Pending"]);
  return { status, send: res };
}

async function resolve(env, order, dec, phone) {
  if (!order || order.status !== "pending") return false;
  if (dec === "CONFIRM") {
    await setStatus(env, order.order_gid, "confirmed");
    await shopTag(env, order.order_gid, ["WA-Confirmed"], ["WA-Pending"]);
    await send(env, textMsg(phone, MSG.confirmed(order.order_name)), "session");
  } else {
    await setStatus(env, order.order_gid, "cancel_requested");
    await shopTag(env, order.order_gid, ["WA-CancelRequested"], ["WA-Pending"]);
    await send(env, textMsg(phone, MSG.cancelled(order.order_name)), "session");
    await event(env, "action", `Cancel requested for ${order.order_name} — review in the store`);
  }
  return true;
}

async function answer(env, text, extra) {
  if (!env.AI) return "HANDOFF";
  const system = `You are the WhatsApp assistant for ${env.STORE_NAME}, a Pakistani online store.
Reply in the SAME language and script the customer used (Urdu script, Roman Urdu, or English). Max 3 short sentences.
Use ONLY the store information below; never invent prices, offers or delivery times. No medical advice.
If the question is outside the information, or the customer is angry, wants a refund, or asks for a human, reply exactly: HANDOFF
STORE INFORMATION:
${KB}
${extra || ""}`;
  // Try each configured model in order, so a retired model falls back instead of failing.
  for (const model of (env.AI_MODEL || "").split(",").map((m) => m.trim()).filter(Boolean)) {
    try {
      const r = await env.AI.run(model, { messages: [{ role: "system", content: system }, { role: "user", content: text.slice(0, 1500) }], max_tokens: 200 });
      const out = (r?.response || "").trim();
      return !out || out.includes("HANDOFF") ? "HANDOFF" : out;
    } catch (e) {
      await event(env, "error", `AI model ${model} failed: ${String(e).slice(0, 160)}`);
    }
  }
  return "HANDOFF";
}

export async function handleMessage(env, m) {
  const phone = m.from, type = m.type;
  let payload = null;
  if (type === "button") payload = m.button?.payload;
  else if (type === "interactive") payload = m.interactive?.button_reply?.id;
  if (payload && payload.includes("|")) {
    const [dec, gid] = payload.split("|", 2);
    const r = await q(env, "SELECT * FROM orders WHERE order_gid=? AND phone=?", gid, phone);
    if (await resolve(env, r[0], dec, phone)) return;
  }
  if (type === "audio" || type === "voice") { await send(env, textMsg(phone, MSG.voice), "session"); return; }
  const body = type === "text" ? m.text?.body : m.button?.text;
  if (!body) {
    await send(env, textMsg(phone, MSG.handoff), "session");
    await event(env, "needs_human", `${phone}: unsupported message type ${type}`);
    return;
  }
  const n = norm(body);
  const pending = (await q(env, "SELECT * FROM orders WHERE phone=? AND status='pending' ORDER BY created_at DESC LIMIT 1", phone))[0];
  if (pending && n.length <= 40 && !body.includes("?") && !body.includes("؟")) {
    const d = decision(n);
    if (d === "UNCLEAR") { await send(env, textMsg(phone, MSG.unclear), "session"); return; }
    if (d) { await resolve(env, pending, d, phone); return; }
  }
  let extra = "";
  if (STATUS_HINTS.some((h) => n.includes(h))) {
    const last = (await q(env, "SELECT * FROM orders WHERE phone=? ORDER BY created_at DESC LIMIT 1", phone))[0];
    if (last) extra = `CUSTOMER'S LATEST ORDER: ${last.order_name}, total Rs ${last.total}, confirmation status: ${last.status}`;
  }
  let reply = await answer(env, body, extra);
  if (reply === "HANDOFF") { await event(env, "needs_human", `${phone}: ${body.slice(0, 200)}`); reply = MSG.handoff; }
  await send(env, textMsg(phone, reply), "session");
}

async function recordStatus(env, s) {
  const p = s.pricing || {};
  if (s.id && Object.keys(p).length)
    await x(env, "INSERT OR REPLACE INTO costs VALUES(?,?,?,?,?)", s.id, p.category || null, p.billable ? 1 : 0, p.type || null, now());
  if (s.status === "failed") await event(env, "error", `Delivery failed ${s.recipient_id}: ${JSON.stringify(s.errors).slice(0, 200)}`);
}

export async function sweep(env) {
  const cutoff = now() - Number(env.NO_REPLY_HOURS || 24) * 3600;
  for (const o of await q(env, "SELECT * FROM orders WHERE status='pending' AND created_at<?", cutoff)) {
    await setStatus(env, o.order_gid, "no_reply");
    await shopTag(env, o.order_gid, ["WA-NoReply"], ["WA-Pending"]);
  }
}

async function health(env, key) {
  const base = { ok: true, mode: env.SEND_MODE || "dry_run", live_approved: env.LIVE_APPROVED === "yes" };
  const hk = await derive(env.WA_APP_SECRET, "health");
  if (!hk || key !== hk) return base;
  const day = now() - 86400, week = now() - 7 * 86400;
  const grp = (rows, k) => Object.fromEntries(rows.map((r) => [r[k], r.n]));
  return { ...base,
    orders: grp(await q(env, "SELECT status, COUNT(*) n FROM orders GROUP BY status"), "status"),
    out_24h: grp(await q(env, "SELECT result, COUNT(*) n FROM messages WHERE direction='out' AND ts>? GROUP BY result", day), "result"),
    billing_7d: await q(env, "SELECT category, pricing_type, billable, COUNT(*) n FROM costs WHERE ts>? GROUP BY category, pricing_type, billable", week),
    events_7d: await q(env, "SELECT level, text, ts FROM events WHERE ts>? ORDER BY ts DESC LIMIT 20", week) };
}

// ---------------- HTTP ----------------
export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const p = url.pathname;
    if (req.method === "GET" && (p === "/" || p === "/demo" || p === "/demo/")) return Response.redirect(env.DEMO_URL, 302);
    if (req.method === "GET" && p === "/healthz") return json(await health(env, url.searchParams.get("key") || ""));

    if (p === "/webhook/whatsapp" && req.method === "GET") {
      const verify = env.WA_VERIFY_TOKEN || (await derive(env.WA_APP_SECRET, "verify"));
      const sp = url.searchParams;
      if (sp.get("hub.mode") === "subscribe" && verify && sp.get("hub.verify_token") === verify)
        return new Response(sp.get("hub.challenge") || "", { headers: { "content-type": "text/plain" } });
      return new Response("forbidden", { status: 403 });
    }
    if (p === "/webhook/whatsapp" && req.method === "POST") {
      const raw = new Uint8Array(await req.arrayBuffer());
      const good = env.WA_APP_SECRET ? "sha256=" + (await hmac(env.WA_APP_SECRET, raw, "hex")) : "";
      if (!safeEq(req.headers.get("x-hub-signature-256") || "", good)) {
        await event(env, "warn", `Webhook POST rejected: bad signature (${raw.length} bytes, ua=${(req.headers.get("user-agent") || "").slice(0, 40)})`);
        return new Response("unauthorized", { status: 401 });
      }
      const data = JSON.parse(new TextDecoder().decode(raw) || "{}");
      const counts = (data.entry || []).flatMap((e) => e.changes || []).reduce((a, c) => {
        a.m += (c.value?.messages || []).length; a.s += (c.value?.statuses || []).length; return a; }, { m: 0, s: 0 });
      await event(env, "info", `Webhook received: ${counts.m} message(s), ${counts.s} status update(s)`);
      const jobs = [];
      for (const entry of data.entry || []) for (const ch of entry.changes || []) {
        const v = ch.value || {};
        for (const s of v.statuses || []) jobs.push(recordStatus(env, s));
        for (const m of v.messages || []) {
          if (await seenInbound(env, m.id)) continue;
          await x(env, "INSERT INTO windows VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET last_inbound=excluded.last_inbound", m.from, now());
          jobs.push(handleMessage(env, m).catch((e) => event(env, "error", `handleMessage: ${e}`)));
        }
      }
      ctx.waitUntil(Promise.all(jobs));
      return json({ ok: true });
    }
    if (p === "/webhook/shopify/orders-create" && req.method === "POST") {
      const raw = new Uint8Array(await req.arrayBuffer());
      const good = env.SHOP_API_SECRET ? await hmac(env.SHOP_API_SECRET, raw, "base64") : "";
      if (!safeEq(req.headers.get("x-shopify-hmac-sha256") || "", good)) return new Response("unauthorized", { status: 401 });
      ctx.waitUntil(handleOrder(env, JSON.parse(new TextDecoder().decode(raw))).catch((e) => event(env, "error", `handleOrder: ${e}`)));
      return json({ ok: true });
    }
    // Owner-only test: create a sample COD order for a test recipient (needs the health key).
    if (p === "/test/sample-order" && req.method === "POST") {
      const hk = await derive(env.WA_APP_SECRET, "health");
      if (!hk || url.searchParams.get("key") !== hk) return new Response("forbidden", { status: 403 });
      const b = await req.json().catch(() => ({}));
      const id = Date.now();
      const r = await handleOrder(env, { id, name: `#T${String(id).slice(-5)}`, total_price: "1799.00",
        payment_gateway_names: ["Cash on Delivery (COD)"], shipping_address: { phone: b.phone, first_name: b.name || "Test" } });
      return json({ ok: true, result: r || null });
    }
    if (p === "/admin/ensure-template" && req.method === "POST") {
      const hk = await derive(env.WA_APP_SECRET, "health");
      if (!hk || url.searchParams.get("key") !== hk) return new Response("forbidden", { status: 403 });
      return json({ template: await ensureTemplate(env) });
    }
    return new Response("not found", { status: 404 });
  },
  async scheduled(_evt, env, ctx) { ctx.waitUntil(sweep(env)); },
};
