"""
discord_client.py
=================
Posts orders for fulfilment to the Discord #fulfillment channel.

The fulfilment team is moving from the Telegram group to Discord. Each order
is one message, built with Discord's Components V2 layout:

    ## AM1234
    customer, phone, address, amount
    ─────────────
    Bag A · Size M · x1                  [photo]
    Cost: -
    [ Enter cost ]

Each item carries its own photo beside it and its own button under it, so
whoever enters a cost can see which item it is (Discord's pop-up form cannot
show a picture, so the photo has to be in the message, next to the button).

The "Enter cost" button carries the GRQ OS order_items id (custom_id
"cost:<id>"). A click goes to the CRM (crm.grq.ae/api/discord/interactions),
which opens a form, saves the cost on that item and updates this message
in place - so costs reach the costing desk without a VA copying replies.

Posting needs only the bot token and the channel id (DISCORD_BOT_TOKEN,
DISCORD_FULFILMENT_CHANNEL_ID). Inert when either is missing.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger("order_bridge.discord")

API = "https://discord.com/api/v10"
IS_COMPONENTS_V2 = 1 << 15
MAX_COMPONENTS = 40          # Discord's cap per message, nested ones included
ITEMS_PER_MESSAGE = 7        # 3 fixed components + 5 per item stays under 40


def _token() -> str:
    return os.getenv("DISCORD_BOT_TOKEN", "").strip()


def _channel() -> str:
    return os.getenv("DISCORD_FULFILMENT_CHANNEL_ID", "").strip()


def enabled() -> bool:
    return bool(_token() and _channel())


def _md(text) -> str:
    """Plain text that cannot be read as Discord markdown or a mention."""
    s = "" if text is None else str(text)
    for ch in ("\\", "*", "_", "~", "`", "|", ">", "#"):
        s = s.replace(ch, "\\" + ch)
    return s.replace("@", "@​")


def _money(total, known=True) -> str:
    if total is None or known is False:
        return "-"
    try:
        return f"AED {float(total):,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return f"AED {total}"


def _item_line(it: dict) -> str:
    bits = [f"**{_md(it.get('product_title') or it.get('sku') or 'Item')}**"]
    if it.get("variant") and str(it["variant"]).lower() != "default title":
        bits.append(_md(it["variant"]))
    bits.append(f"x{int(it.get('quantity') or 1)}")
    cost = it.get("unit_cost")
    cost_line = f"✅ Cost: **AED {float(cost):g}**" if cost is not None else "Cost: -"
    return " · ".join(bits) + "\n" + cost_line


def build_messages(raw: dict, image_urls: list[str]) -> list[dict]:
    """The message payload(s) for one claimed order. Pure; no network."""
    items = [it for it in (raw.get("items") or []) if it.get("id")]
    code = raw.get("order_code") or "?"
    details = [
        f"## {_md(code)}",
        f"**Customer:** {_md(raw.get('customer') or '-')}",
        f"**Phone:** {_md(raw.get('phone') or '-')}",
        f"**Address:** {_md(', '.join(x for x in [raw.get('address'), raw.get('city')] if x) or '-')}",
        f"**Amount:** {_money(raw.get('total'), raw.get('total_known', True))}"
        + (f" · {_md(raw.get('payment_method'))}" if raw.get("payment_method") else ""),
    ]
    if raw.get("internal_note"):
        details.append(f"**Note:** {_md(raw['internal_note'])[:500]}")

    photos = [u for u in image_urls if isinstance(u, str) and u.startswith("http")]

    def photo_for(idx: int, it: dict) -> str | None:
        url = it.get("image_url")
        if isinstance(url, str) and url.startswith("http"):
            return url
        return photos[idx] if idx < len(photos) else None

    chunks = [items[i:i + ITEMS_PER_MESSAGE] for i in range(0, len(items), ITEMS_PER_MESSAGE)] or [[]]

    messages = []
    for n, chunk in enumerate(chunks):
        inner: list[dict] = [{"type": 10, "content": "\n".join(details) if n == 0
                              else f"## {_md(code)} (continued)"}]
        inner.append({"type": 14, "divider": True, "spacing": 1})
        for k, it in enumerate(chunk):
            text = {"type": 10, "content": _item_line(it)}
            photo = photo_for(n * ITEMS_PER_MESSAGE + k, it)
            # The photo beside the item; a Section needs an accessory, so an
            # item with no photo is a plain text block instead.
            inner.append({"type": 9, "components": [text], "accessory": {"type": 11, "media": {"url": photo}}}
                         if photo else text)
            inner.append({"type": 1, "components": [{
                "type": 2, "style": 2 if it.get("unit_cost") is not None else 1,
                "label": "Edit cost" if it.get("unit_cost") is not None else "Enter cost",
                "custom_id": f"cost:{it['id']}",
            }]})
        if not chunk:
            inner.append({"type": 10, "content": "_No items on this order in the CRM._"})
        messages.append({
            "flags": IS_COMPONENTS_V2,
            "allowed_mentions": {"parse": []},
            "components": [{"type": 17, "accent_color": 0x2E7D5B, "components": inner}],
        })
    return messages


def _count(components: list[dict]) -> int:
    n = 0
    for c in components:
        n += 1
        n += _count(c.get("components") or [])
        if c.get("accessory"):
            n += 1
    return n


def post_order(raw: dict, image_urls: list[str]) -> list[str]:
    """Post one order. Returns the message ids posted (empty when disabled or failed)."""
    if not enabled():
        return []
    ids: list[str] = []
    for payload in build_messages(raw, image_urls):
        assert _count(payload["components"]) <= MAX_COMPONENTS, "too many components for one message"
        for attempt in range(3):
            try:
                r = httpx.post(f"{API}/channels/{_channel()}/messages", json=payload, timeout=20,
                               headers={"Authorization": f"Bot {_token()}", "User-Agent": "GRQBot (fulfilment, 1.0)"})
            except httpx.TransportError as e:
                log.warning("Discord post for %s did not get through (%s); retrying", raw.get("order_code"), e)
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 429:  # rate limited: Discord says how long to wait
                wait = float((r.json() or {}).get("retry_after", 2))
                log.warning("Discord rate limit for %s; waiting %.1fs", raw.get("order_code"), wait)
                time.sleep(min(wait, 30) + 0.25)
                continue
            if r.status_code >= 300:
                log.error("Discord post for %s refused: %s %s", raw.get("order_code"), r.status_code, r.text[:300])
                return ids
            ids.append(r.json().get("id"))
            break
        else:
            log.error("Discord post for %s failed after retries", raw.get("order_code"))
            return ids
    log.info("  ✓ %s posted to Discord #fulfillment (%d message(s))", raw.get("order_code"), len(ids))
    return ids
