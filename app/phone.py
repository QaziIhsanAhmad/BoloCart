import re


def normalize_pk(raw):
    """Return a Pakistani mobile number as WhatsApp wa_id (e.g. 923001234567) or None."""
    if not raw:
        return None
    d = re.sub(r"\D", "", str(raw))
    if d.startswith("0092"):
        d = d[2:]
    if d.startswith("92") and len(d) == 12:
        pass
    elif d.startswith("0") and len(d) == 11:
        d = "92" + d[1:]
    elif d.startswith("3") and len(d) == 10:
        d = "92" + d
    else:
        return None
    return d if d[2] == "3" else None
