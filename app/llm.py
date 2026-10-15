from pathlib import Path

from . import clients, db
from .config import S

HANDOFF = "HANDOFF"

SYSTEM = """You are the WhatsApp assistant for {store}, a Pakistani skincare store.
Reply in the SAME language and script the customer used (Urdu script, Roman Urdu, or English).
Keep replies short (max 3 sentences), polite and friendly.
Use ONLY the store information below. Never invent prices, ingredients, offers or delivery times.
Do not give medical advice or promise results. For skin conditions, allergies, pregnancy or
reactions, suggest a patch test and seeing a dermatologist.
If the question is outside the information below, or the customer is angry, wants a refund,
or asks for a human, reply with exactly: HANDOFF

STORE INFORMATION:
{kb}
"""


def kb():
    p = Path(S.KB_PATH)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p.read_text(encoding="utf-8") if p.exists() else ""


async def answer(user_text, extra=""):
    """Return reply text, or HANDOFF if the model can't/shouldn't answer."""
    system = SYSTEM.format(store=S.STORE_NAME, kb=kb() + ("\n" + extra if extra else ""))
    try:
        r = await clients.HTTP.post(f"{S.OLLAMA_URL}/api/chat", json={
            "model": S.OLLAMA_MODEL, "stream": False, "options": {"temperature": 0.2},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user_text[:1500]}]})
        out = (r.json().get("message") or {}).get("content", "").strip()
    except Exception as e:  # LLM down: hand off, never go silent
        db.event("error", f"LLM unavailable: {e!r}"[:300])
        return HANDOFF
    if not out or HANDOFF in out:
        return HANDOFF
    return out
