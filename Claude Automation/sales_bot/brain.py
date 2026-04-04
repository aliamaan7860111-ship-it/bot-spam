"""
Sales Bot Brain — the core AI logic.
Loads context, calls Claude, returns a sales-appropriate response.
Uses the sales brain directive for conversation intelligence.
"""

import os
import json
import httpx
import anthropic
from pathlib import Path
from dotenv import load_dotenv
from supabase_client import (
    get_customer, create_customer, update_customer, update_last_message,
    save_message, get_conversation_history, search_products, cancel_follow_ups
)
from whatchimp_client import assign_label

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
VISION_MODEL = os.getenv("VISION_MODEL", "claude-haiku-4-5-20251001")
STORE = os.getenv("DEFAULT_STORE", "prettybyshd")
CURRENCY = os.getenv("STORE_CURRENCY", "AED")
HUMAN_LABEL_ID = os.getenv("WHATCHIMP_HUMAN_LABEL_ID", "294168")
ESCALATE_TAG = "[ESCALATE]"

# ── Load Sales Brain directive ──────────────────────────────
BRAIN_DIR = Path(__file__).parent.parent / "directives" / "sales_bot"
SALES_BRAIN = ""
brain_path = BRAIN_DIR / "sales_brain.md"
if brain_path.exists():
    SALES_BRAIN = brain_path.read_text(encoding="utf-8")
else:
    print(f"WARNING: Sales brain not found at {brain_path}")

# ── Brand aliases — maps common misspellings/abbreviations to DB brand names ──

BRAND_ALIASES = {
    # Chanel
    "chanel": "Chanel", "channel": "Chanel", "chanell": "Chanel", "coco": "Chanel",
    "coco chanel": "Chanel", "chanel bag": "Chanel", "cc": "Chanel",
    # Louis Vuitton
    "lv": "Louis Vuitton", "louis vuitton": "Louis Vuitton", "louis": "Louis Vuitton",
    "vuitton": "Louis Vuitton", "louie vuitton": "Louis Vuitton", "louie": "Louis Vuitton",
    "loui vuitton": "Louis Vuitton", "luis vuitton": "Louis Vuitton",
    # Hermes
    "hermes": "Hermes", "hermès": "Hermes", "hermez": "Hermes", "birkin": "Hermes",
    "kelly": "Hermes",
    # Gucci
    "gucci": "Gucci", "guchi": "Gucci", "goochi": "Gucci",
    # Dior
    "dior": "Dior", "christian dior": "Dior", "dior bag": "Dior",
    "lady dior": "Dior", "book tote": "Dior",
    # Prada
    "prada": "Prada", "pradaa": "Prada",
    # Fendi
    "fendi": "Fendi", "fendi bag": "Fendi", "baguette": "Fendi",
    # Celine
    "celine": "Celine", "céline": "Celine", "celin": "Celine",
    # Balenciaga
    "balenciaga": "Balenciaga", "balenciaga bag": "Balenciaga",
    "balenci": "Balenciaga", "city bag": "Balenciaga",
    # YSL
    "ysl": "YSL", "saint laurent": "YSL", "yves saint laurent": "YSL",
    "saint laurant": "YSL",
    # Bottega
    "bottega": "Bottega Veneta", "bottega veneta": "Bottega Veneta", "bv": "Bottega Veneta",
    # Givenchy
    "givenchy": "Givenchy", "givency": "Givenchy",
    # Versace
    "versace": "Versace", "versaci": "Versace",
    # Burberry
    "burberry": "Burberry", "burber": "Burberry", "burberry bag": "Burberry",
    # Goyard
    "goyard": "Goyard",
    # Loewe
    "loewe": "Loewe", "loewe bag": "Loewe",
}

COLOR_ALIASES = {
    "black": "Black", "blk": "Black", "noir": "Black",
    "white": "White", "wht": "White", "blanc": "White",
    "gold": "Gold", "golden": "Gold", "metallic gold": "Metallic Gold",
    "silver": "Silver", "metallic silver": "Silver",
    "brown": "Brown", "tan": "Brown", "camel": "Brown",
    "beige": "Beige", "cream": "Beige", "nude": "Beige",
    "red": "Red", "rouge": "Red",
    "blue": "Blue", "navy": "Blue",
    "green": "Green", "olive": "Green",
    "pink": "Pink", "rose": "Pink",
    "grey": "Grey", "gray": "Grey",
    "etoupe": "Etoupe",
}

CATEGORY_KEYWORDS = ["bag", "bags", "handbag", "handbags", "wallet", "wallets", "purse",
                     "shoes", "shoe", "sneakers", "belt", "belts", "scarf", "scarves",
                     "watch", "watches", "sunglasses", "jewelry", "jewellery"]

# ── System Prompt ──────────────────────────────────────────

SYSTEM_PROMPT = f"""{SALES_BRAIN}

---

# RUNTIME RULES (always active)

## Product Display
When you have product catalog data in the [CATALOG DATA] section, show those products to the customer immediately. Do NOT ask more qualifying questions when you already have matching products.

Format products like this:
1. Product Name — Color | Price {CURRENCY}
2. Product Name — Color | Price {CURRENCY}

Then ask which one they'd like to see photos of or if they'd like to order.

## Inventory Honesty
- If [CATALOG DATA] says "No matching products found" — be honest and suggest what you DO have
- NEVER make up product information — ONLY reference products from [CATALOG DATA]
- If catalog shows a different brand than requested, say so honestly

## Image Handling
- Product photos are sent automatically by the system when available — you do NOT need to announce or narrate this.
- NEVER write things like "*[Sending product images]*", "[Sending images]", or "Let me send you the photos" as a standalone action.
- Instead, just naturally describe the product and the system handles the rest.
- If you want to reference photos, say something like "here's what it looks like" or "you can see the finishing on this one" — as if the image is already there.

## Escalation Protocol
When the conversation requires a human — such as:
- Customer has a post-purchase issue (wrong item, damaged, refund, return, cancellation)
- Customer asks about delivery tracking or order status
- Customer is aggressive/abusive for 2+ messages
- Negotiation going in circles (3+ back-and-forth on price with no movement)
- Anything you genuinely don't know the answer to

Then start your reply with exactly: {ESCALATE_TAG}
Followed by your customer-facing message (e.g., "Let me connect you with someone from the team who can help with this directly.")
The system will automatically handle the handoff — you just need the tag.
"""


def format_products_for_context(products: list) -> str:
    """Format product list as context for Claude."""
    if not products:
        return "No matching products found in catalog."

    lines = ["MATCHING PRODUCTS IN YOUR CATALOG:"]
    for i, p in enumerate(products, 1):
        line = f"\n{i}. {p['title']}"
        line += f"\n   Color: {p.get('color', 'N/A')}"
        line += f"\n   Price: {p['price']} {p.get('currency', CURRENCY)}"
        if p.get('compare_at_price'):
            line += f" (was {p['compare_at_price']} {p.get('currency', CURRENCY)})"
        if p.get('material'):
            line += f"\n   Material: {p['material']}"
        if p.get('description'):
            line += f"\n   Description: {p['description'][:150]}"
        if p.get('studio_images'):
            line += f"\n   Has {len(p['studio_images'])} product photos available to send"
        line += f"\n   Product ID: {p['id']}"
        lines.append(line)

    lines.append("\nIMPORTANT: Show these products to the customer NOW. Do not ask more qualifying questions.")
    return "\n".join(lines)


def format_conversation_history(history: list) -> list:
    """Convert DB conversation history to Claude messages format."""
    messages = []
    for msg in history:
        role = "user" if msg["role"] == "customer" else "assistant"
        content = msg["content"]
        if msg.get("message_type") == "image" and msg.get("image_url"):
            content = f"[Customer sent an image]\n{content}"
        messages.append({"role": role, "content": content})
    return messages


def extract_search_terms(text: str) -> dict:
    """Extract brand, color, and category from message text using fuzzy matching."""
    text_lower = text.lower().strip()
    words = text_lower.split()

    matched_brand = None
    matched_color = None
    matched_category = None

    # Check multi-word aliases first (longest match wins)
    for alias in sorted(BRAND_ALIASES.keys(), key=len, reverse=True):
        if alias in text_lower:
            matched_brand = BRAND_ALIASES[alias]
            break

    for alias in sorted(COLOR_ALIASES.keys(), key=len, reverse=True):
        if alias in text_lower:
            matched_color = COLOR_ALIASES[alias]
            break

    for keyword in CATEGORY_KEYWORDS:
        if keyword in text_lower:
            matched_category = "Bags"  # For now, most categories map to bags
            break

    return {
        "brand": matched_brand,
        "color": matched_color,
        "category": matched_category,
    }


async def identify_image(image_url: str) -> str:
    """Use Claude Vision (Haiku) to identify a product from an image."""
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(image_url)
        if resp.status_code != 200:
            return "Could not download the image."

    import base64
    image_data = base64.b64encode(resp.content).decode("utf-8")

    content_type = resp.headers.get("content-type", "image/jpeg")
    if "png" in content_type:
        media_type = "image/png"
    elif "webp" in content_type:
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"

    vision_resp = client.messages.create(
        model=VISION_MODEL,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": image_data},
                },
                {
                    "type": "text",
                    "text": "Identify this product briefly. Reply ONLY in this format:\nBrand: ...\nType: ...\nColor: ...\nModel: ...",
                },
            ],
        }],
    )
    return vision_resp.content[0].text


async def process_message(phone: str, message_text: str, first_name: str = None,
                          message_type: str = "text", image_url: str = None) -> dict:
    """
    Main brain function. Processes an incoming message and returns a response.
    Returns: {"reply": str, "images": list[str], "products": list[dict]}
    """
    # 1. Get or create customer
    customer = create_customer(phone, name=first_name, store=STORE)
    update_last_message(phone)

    # 2. Cancel any pending follow-ups (customer is active)
    cancel_follow_ups(phone)

    # 3. Check if human has taken over
    if customer.get("human_takeover"):
        return {"reply": None, "images": [], "products": []}

    # 4. Handle image messages — identify the product
    # NOTE: Messages are already saved to DB by webhook_server.py (for batching)
    # We only need to do vision identification here, not save again
    image_context = ""
    if message_type == "image" and image_url:
        image_context = await identify_image(image_url)
        message_text = f"Customer sent an image. Vision identified: {image_context}"

    # 5. Load conversation history
    history = get_conversation_history(phone)
    claude_messages = format_conversation_history(history)

    # 6. ALWAYS search for products — use multiple strategies
    products = []

    # Strategy A: Extract search terms from ALL recent customer messages
    # (handles batched rapid messages — "hi" + "i need lv" + "what prices")
    search_terms = {"brand": None, "color": None, "category": None}
    for msg in reversed(history):
        if msg["role"] != "customer":
            break  # Stop at last bot reply — only scan new customer messages
        msg_terms = extract_search_terms(msg["content"])
        if msg_terms["brand"] and not search_terms["brand"]:
            search_terms["brand"] = msg_terms["brand"]
        if msg_terms["color"] and not search_terms["color"]:
            search_terms["color"] = msg_terms["color"]
        if msg_terms["category"] and not search_terms["category"]:
            search_terms["category"] = msg_terms["category"]

    # Also check the current message text (for image identification etc.)
    current_terms = extract_search_terms(message_text)
    if current_terms["brand"] and not search_terms["brand"]:
        search_terms["brand"] = current_terms["brand"]
    if current_terms["color"] and not search_terms["color"]:
        search_terms["color"] = current_terms["color"]
    if current_terms["category"] and not search_terms["category"]:
        search_terms["category"] = current_terms["category"]

    # Strategy B: If image was identified, parse vision output too
    if image_context:
        vision_terms = extract_search_terms(image_context)
        if vision_terms["brand"] and not search_terms["brand"]:
            search_terms["brand"] = vision_terms["brand"]
        if vision_terms["color"] and not search_terms["color"]:
            search_terms["color"] = vision_terms["color"]

    # Strategy C: Check older conversation history for context
    # (e.g., they asked about Chanel earlier, now they say "black")
    if not search_terms["brand"]:
        for msg in reversed(history):
            if msg["role"] == "customer":
                past_terms = extract_search_terms(msg["content"])
                if past_terms["brand"]:
                    search_terms["brand"] = past_terms["brand"]
                    break

    # Search with whatever we found (with retry for Supabase/Cloudflare flakiness)
    import time as _time

    def _search_with_retry(**kwargs):
        """Try search up to 2 times with a short delay on failure."""
        for attempt in range(2):
            try:
                return search_products(**kwargs)
            except Exception as e:
                if attempt == 0:
                    print(f"Product search failed (attempt 1), retrying in 1s: {e}")
                    _time.sleep(1)
                else:
                    print(f"Product search failed (attempt 2), giving up: {e}")
                    return []

    if search_terms["brand"] or search_terms["color"] or search_terms["category"]:
        products = _search_with_retry(
            brand=search_terms["brand"],
            color=search_terms["color"],
            category=search_terms["category"],
            store=STORE
        )

    # If no specific search matched, check if they're asking to see products generally
    if not products:
        general_triggers = ["what do you have", "show me", "catalog", "collection",
                           "products", "items", "available", "stock", "what kind",
                           "what bags", "what type", "options"]
        if any(trigger in message_text.lower() for trigger in general_triggers):
            products = _search_with_retry(store=STORE)

    # 7. Build the context for Claude
    product_context = format_products_for_context(products) if products else ""

    # Also always include what's in stock (brief summary) even if no specific search
    all_products = _search_with_retry(store=STORE)
    if not products and all_products:
        # Use general results for image matching too (so images still send)
        products = all_products
        inventory_summary = "CURRENT FULL INVENTORY (mention these if relevant):\n"
        for p in all_products:
            inventory_summary += f"- {p['title']} | {p.get('color', 'N/A')} | {p['price']} {CURRENCY}\n"
        product_context = inventory_summary

    customer_context = f"""Customer info:
- Phone: {phone}
- Name: {customer.get('name') or 'Unknown'}
- Stage: {customer.get('funnel_stage', 'new')}
- Store: {customer.get('store', STORE)}"""

    # Build the full user message with catalog data appended
    if claude_messages and claude_messages[-1]["role"] == "user":
        catalog_section = f"\n\n[CATALOG DATA]\n{product_context}" if product_context else "\n\n[CATALOG DATA]\nNo specific products matched this query. Check inventory summary."
        claude_messages[-1]["content"] += catalog_section
    else:
        content = message_text
        if product_context:
            content += f"\n\n[CATALOG DATA]\n{product_context}"
        claude_messages.append({"role": "user", "content": content})

    # Ensure messages alternate properly (Claude requirement)
    cleaned_messages = []
    last_role = None
    for msg in claude_messages:
        if msg["role"] == last_role:
            cleaned_messages[-1]["content"] += "\n" + msg["content"]
        else:
            cleaned_messages.append(msg)
            last_role = msg["role"]

    # Ensure first message is from user
    if cleaned_messages and cleaned_messages[0]["role"] != "user":
        cleaned_messages.insert(0, {"role": "user", "content": "(conversation started)"})

    # 8. Call Claude
    full_system = f"{SYSTEM_PROMPT}\n\n{customer_context}"

    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=full_system,
        messages=cleaned_messages,
    )

    reply = response.content[0].text

    # 8b. Check for escalation tag
    if reply.startswith(ESCALATE_TAG):
        reply = reply[len(ESCALATE_TAG):].strip()
        # Label the customer as "Human" in WhatChimp
        try:
            await assign_label(phone, HUMAN_LABEL_ID)
        except Exception as e:
            print(f"Failed to assign Human label for {phone}: {e}")
        # Flag in Supabase so bot stops replying
        update_customer(phone, human_takeover=True)
        # Save the escalation reply and return (no product images needed)
        save_message(phone, "bot", reply)
        return {"reply": reply, "images": [], "products": []}

    # 9. Determine which product images to send
    images_to_send = []
    if products:
        # If we found matching products, send images of the first one that Claude mentions
        reply_lower = reply.lower()
        for p in products:
            # Check if any significant word from the product title appears in the reply
            title_words = [w.lower() for w in p["title"].replace("|", "").split() if len(w) > 3]
            if any(word in reply_lower for word in title_words):
                if p.get("studio_images"):
                    images_to_send = p["studio_images"][:2]
                break

        # If Claude mentioned products but we couldn't match which one, send first product's images
        if not images_to_send and len(products) <= 3:
            for p in products:
                if p.get("studio_images"):
                    images_to_send = p["studio_images"][:1]
                    break

    # 10. Save bot reply
    product_ids = [p["id"] for p in products] if products else []
    save_message(phone, "bot", reply, products_mentioned=product_ids)

    # 11. Update funnel stage based on conversation
    if customer.get("funnel_stage") == "new" and products:
        update_customer(phone, funnel_stage="interested")

    return {
        "reply": reply,
        "images": images_to_send,
        "products": products,
    }
