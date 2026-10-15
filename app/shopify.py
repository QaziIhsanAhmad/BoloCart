import base64
import hashlib
import hmac

from . import clients, db
from .config import S


def verify_hmac(raw: bytes, header: str) -> bool:
    if not (S.SHOP_API_SECRET and header):
        return False
    digest = base64.b64encode(hmac.new(S.SHOP_API_SECRET.encode(), raw, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(digest, header)


async def gql(query, variables=None):
    r = await clients.HTTP.post(
        f"https://{S.SHOP_DOMAIN}/admin/api/{S.SHOP_API_VERSION}/graphql.json",
        headers={"X-Shopify-Access-Token": S.SHOP_TOKEN},
        json={"query": query, "variables": variables or {}})
    data = r.json() if r.content else {}
    if r.status_code >= 300 or data.get("errors"):
        db.event("error", f"Shopify GraphQL {r.status_code}: {str(data)[:300]}")
    return data


async def tag(order_gid, add, remove=()):
    if not S.SHOP_TOKEN:
        return
    await gql("mutation($id:ID!,$t:[String!]!){tagsAdd(id:$id,tags:$t){userErrors{message}}}",
              {"id": order_gid, "t": list(add)})
    if remove:
        await gql("mutation($id:ID!,$t:[String!]!){tagsRemove(id:$id,tags:$t){userErrors{message}}}",
                  {"id": order_gid, "t": list(remove)})


async def order_status(order_gid):
    d = await gql("query($id:ID!){order(id:$id){name displayFulfillmentStatus cancelledAt "
                  "fulfillments(first:3){trackingInfo(first:1){company number url}}}}", {"id": order_gid})
    return ((d or {}).get("data") or {}).get("order")


async def ensure_webhook():
    if not (S.SHOP_TOKEN and S.PUBLIC_URL):
        return "skipped:no_credentials"
    uri = f"{S.PUBLIC_URL}/webhook/shopify/orders-create"
    d = await gql("{webhookSubscriptions(first:50,topics:[ORDERS_CREATE]){nodes{id uri}}}")
    nodes = (((d.get("data") or {}).get("webhookSubscriptions") or {}).get("nodes")) or []
    if any(n.get("uri") == uri for n in nodes):
        return "exists"
    d = await gql("mutation($s:WebhookSubscriptionInput!){webhookSubscriptionCreate("
                  "topic:ORDERS_CREATE,webhookSubscription:$s){webhookSubscription{id}userErrors{message}}}",
                  {"s": {"uri": uri, "format": "JSON"}})
    errs = (((d.get("data") or {}).get("webhookSubscriptionCreate") or {}).get("userErrors")) or []
    return "created" if not errs else f"error:{errs}"
