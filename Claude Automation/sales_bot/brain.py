"""
Sales Bot Brain v2 — the core AI logic.
Multi-store aware, with retry logic, vision escalation, and experience layer hooks.
"""

import os
import httpx
import asyncio
import anthropic
from pathlib import Path
from dotenv import load_dotenv
from config import get_logger
from supabase_client import (
    create_customer, update_customer, update_last_message,
    save_message, get_smart_history, search_products, cancel_follow_ups,
    save_conversation_outcome, get_winning_examples
)
from whatchimp_client import assign_label

load_dotenv()

log = get_logger("brain")

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
VISION_MODEL = os.getenv("VISION_MODEL", "claude-haiku-4-5-20251001")
ESCALATE_TAG = "[ESCALATE]"
INTENT_TAG_PREFIX = "[INTENT:"
ARCHETYPE_TAG_PREFIX = "[ARCHETYPE:"

# ── Load Sales Brain directive ──────────────────────────────
BRAIN_DIR = Path(__file__).parent.parent / "directives" / "sales_bot"
SALES_BRAIN = ""
brain_path = BRAIN_DIR / "sales_brain.md"
if brain_path.exists():
    SALES_BRAIN = brain_path.read_text(encoding="utf-8")
else:
    log.warning(f"Sales brain not found at {brain_path}")

# ── Brand aliases — maps common misspellings/abbreviations to DB brand names ──
# TODO: Migrate to Supabase tables for live updates without redeployment

BRAND_ALIASES = {
    "chanel": "Chanel", "channel": "Chanel", "chanell": "Chanel", "coco": "Chanel",
    "coco chanel": "Chanel", "chanel bag": "Chanel", "cc": "Chanel",
    "lv": "Louis Vuitton", "louis vuitton": "Louis Vuitton", "louis": "Louis Vuitton",
    "vuitton": "Louis Vuitton", "louie vuitton": "Louis Vuitton", "louie": "Louis Vuitton",
    "loui vuitton": "Louis Vuitton", "luis vuitton": "Louis Vuitton",
    "hermes": "Hermes", "hermès": "Hermes", "hermez": "Hermes", "birkin": "Hermes",
    "kelly": "Hermes",
    "gucci": "Gucci", "guchi": "Gucci", "goochi": "Gucci",
    "dior": "Dior", "christian dior": "Dior", "dior bag": "Dior",
    "lady dior": "Dior", "book tote": "Dior",
    "prada": "Prada", "pradaa": "Prada",
    "fendi": "Fendi", "fendi bag": "Fendi", "baguette": "Fendi",
    "celine": "Celine", "céline": "Celine", "celin": "Celine",
    "balenciaga": "Balenciaga", "balenciaga bag": "Balenciaga",
    "balenci": "Balenciaga", "city bag": "Balenciaga",
    "ysl": "YSL", "saint laurent": "YSL", "yves saint laurent": "YSL",
    "saint laurant": "YSL",
    "bottega": "Bottega Veneta", "bottega veneta": "Bottega Veneta", "bv": "Bottega Veneta",
    "givenchy": "Givenchy", "givency": "Givenchy",
    "versace": "Versace", "versaci": "Versace",
    "burberry": "Burberry", "burber": "Burberry", "burberry bag": "Burberry",
    "goyard": "Goyard",
    "loewe": "Loewe", "loewe bag": "Loewe",
    "rolex": "Rolex", "rollex": "Rolex",
    "omega": "Omega", "omegaa": "Omega",
    "patek": "Patek Philippe", "patek philippe": "Patek Philippe",
    "ap": "Audemars Piguet", "audemars": "Audemars Piguet", "audemars piguet": "Audemars Piguet",
    "cartier": "Cartier", "cartier watch": "Cartier",
    "hublot": "Hublot",
    "richard mille": "Richard Mille", "rm": "Richard Mille",
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
                     "shoes", "shoe", "sneakers", "loafer", "loafers",
                     "belt", "belts", "scarf", "scarves",
                     "watch", "watches", "sunglasses", "jewelry", "jewellery",
                     "cap", "caps", "hat"]


def build_system_prompt(store_config: dict) -> str:
    """Build the system prompt with store-specific config."""
    currency = store_config.get("currency", "AED")

    return f"""{SALES_BRAIN}

---

# RUNTIME RULES (always active)

## Message Length (CRITICAL)
This is WhatsApp. Keep messages SHORT — max 3-4 short lines.
- Line break between each thought
- One idea per line. No paragraphs.
- Say the right thing, not more things.

## Product Display
When you have product catalog data in [CATALOG DATA], show products immediately.
Format: 1. Product Name — Color | Price {currency}
Then ask one question: which one they'd like, or if they'd like to order.

## Product Images
The [CATALOG DATA] contains photo URLs. Copy-paste the EXACT URL into your message.
Never write placeholder text like "[Image URL]" or "[Sending images]".
Maximum 1 URL per message.

## Inventory Honesty
- If no matching products found — be honest and suggest what you DO have
- NEVER make up products — only reference what's in [CATALOG DATA]
- If catalog shows a different brand than requested, say so

## Escalation Protocol
When the conversation requires a human, start your reply with exactly: {ESCALATE_TAG}
Then write your customer-facing message after the tag.
The system handles the handoff automatically.

## Intent & Archetype Tagging
At the END of every reply, append these tags on their own line (system strips them before sending):
{INTENT_TAG_PREFIX}<detected_intent>] {ARCHETYPE_TAG_PREFIX}<detected_archetype>]

Intent options: greeting, product_search, price_inquiry, objection, order_intent, follow_up, image_request, general_question, escalation_needed
Archetype options: speed_buyer, impulse, comparison, bargain, hesitant, research, skeptical, need_based, product_focused, window, returning, high_value, aggressive, unknown
"""


def format_products_for_context(products: list, currency: str = "AED") -> str:
    """Format product list as context for Claude."""
    if not products:
        return "No matching products found in catalog."

    lines = ["MATCHING PRODUCTS IN YOUR CATALOG:"]
    for i, p in enumerate(products, 1):
        line = f"\n{i}. {p['title']}"
        line += f"\n   Color: {p.get('color', 'N/A')}"
        line += f"\n   Price: {p['price']} {p.get('currency', currency)}"
        if p.get('compare_at_price'):
            line += f" (was {p['compare_at_price']} {p.get('currency', currency)})"
        if p.get('material'):
            line += f"\n   Material: {p['material']}"
        if p.get('description'):
            line += f"\n   Description: {p['description'][:150]}"
        if p.get('studio_images'):
            line += f"\n   Photo URLs:"
            for img_url in p['studio_images'][:2]:
                line += f"\n   {img_url}"
        line += f"\n   Product ID: {p['id']}"
        lines.append(line)

    lines.append("\nShow these products to the customer NOW.")
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

    matched_brand = None
    matched_color = None
    matched_category = None

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
            matched_category = keyword.capitalize()
            break

    return {"brand": matched_brand, "color": matched_color, "category": matched_category}


async def identify_image(image_url: str) -> tuple[str | None, bool]:
    """
    Use Claude Vision (Haiku) to identify a product from an image.
    Returns (identification_text, success).
    On failure: returns (None, False) — caller should escalate to human.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(image_url)
            if resp.status_code != 200:
                log.warning(f"Image download failed: status={resp.status_code}")
                return None, False

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
                        "text": "Identify this product briefly. Reply ONLY in this format:\nBrand: ...\nType: ...\nColor: ...\nModel: ...\n\nIf you cannot identify the product clearly, reply with: UNIDENTIFIED",
                    },
                ],
            }],
        )
        result = vision_resp.content[0].text

        # Check if vision couldn't identify
        if "UNIDENTIFIED" in result.upper() or "sorry" in result.lower() or "cannot" in result.lower():
            log.info(f"Vision could not identify image: {result[:100]}")
            return None, False

        return result, True

    except Exception as e:
        log.error(f"Vision identification failed: {e}")
        return None, False


def parse_tags(reply: str) -> tuple[str, str | None, str | None]:
    """
    Parse and strip intent/archetype tags from Claude's reply.
    Returns (cleaned_reply, intent, archetype).
    """
    intent = None
    archetype = None
    cleaned = reply

    # Extract intent tag
    if INTENT_TAG_PREFIX in cleaned:
        start = cleaned.index(INTENT_TAG_PREFIX)
        end = cleaned.index("]", start) + 1
        tag = cleaned[start:end]
        intent = tag[len(INTENT_TAG_PREFIX):-1]
        cleaned = cleaned[:start] + cleaned[end:]

    # Extract archetype tag
    if ARCHETYPE_TAG_PREFIX in cleaned:
        start = cleaned.index(ARCHETYPE_TAG_PREFIX)
        end = cleaned.index("]", start) + 1
        tag = cleaned[start:end]
        archetype = tag[len(ARCHETYPE_TAG_PREFIX):-1]
        cleaned = cleaned[:start] + cleaned[end:]

    return cleaned.strip(), intent, archetype


async def process_message(phone: str, message_text: str, first_name: str = None,
                          message_type: str = "text", image_url: str = None,
                          store_config: dict = None) -> dict:
    """
    Main brain function. Processes an incoming message and returns a response.
    Returns: {"reply": str | None, "images": list, "products": list}
    """
    # Use store config or defaults
    store = store_config.get("store", "elara") if store_config else "elara"
    currency = store_config.get("currency", "AED") if store_config else "AED"
    human_label_id = store_config.get("human_label_id", "294168") if store_config else "294168"
    phone_number_id = store_config.get("phone_number_id") if store_config else None

    # 1. Get or create customer
    customer = create_customer(phone, name=first_name, store=store)
    update_last_message(phone)

    # 2. Cancel any pending follow-ups (customer is active)
    cancel_follow_ups(phone)

    # 3. Check if human has taken over
    if customer.get("human_takeover"):
        return {"reply": None, "images": [], "products": []}

    # 4. Handle image messages — identify the product
    image_context = ""
    vision_success = True
    if message_type == "image" and image_url:
        identification, vision_success = await identify_image(image_url)

        if not vision_success:
            # Vision failed — escalate to human
            escalation_msg = "Let me get someone from our team to take a look at this — they'll help you find exactly what you're looking for."
            try:
                await assign_label(phone, human_label_id, phone_number_id=phone_number_id)
            except Exception as e:
                log.error(f"Failed to assign Human label for {phone}: {e}")
            update_customer(phone, human_takeover=True)
            save_message(phone, "bot", escalation_msg)
            # Save outcome
            save_conversation_outcome(phone, store, intent="image_request",
                                       outcome="escalated",
                                       bot_response_summary="Vision failed, escalated to human")
            return {"reply": escalation_msg, "images": [], "products": []}

        image_context = identification
        message_text = f"Customer sent an image. Vision identified: {image_context}"

    # 5. Load conversation history (smart: first 2 + last 8)
    history = get_smart_history(phone)
    claude_messages = format_conversation_history(history)

    # 6. Search for products — multi-strategy
    products = []

    # Strategy A: Extract from recent customer messages
    search_terms = {"brand": None, "color": None, "category": None}
    for msg in reversed(history):
        if msg["role"] != "customer":
            break
        msg_terms = extract_search_terms(msg["content"])
        if msg_terms["brand"] and not search_terms["brand"]:
            search_terms["brand"] = msg_terms["brand"]
        if msg_terms["color"] and not search_terms["color"]:
            search_terms["color"] = msg_terms["color"]
        if msg_terms["category"] and not search_terms["category"]:
            search_terms["category"] = msg_terms["category"]

    # Also check current message
    current_terms = extract_search_terms(message_text)
    for key in ("brand", "color", "category"):
        if current_terms[key] and not search_terms[key]:
            search_terms[key] = current_terms[key]

    # Strategy B: Parse vision output
    if image_context:
        vision_terms = extract_search_terms(image_context)
        for key in ("brand", "color"):
            if vision_terms[key] and not search_terms[key]:
                search_terms[key] = vision_terms[key]

    # Strategy C: Older history for brand context
    if not search_terms["brand"]:
        for msg in reversed(history):
            if msg["role"] == "customer":
                past_terms = extract_search_terms(msg["content"])
                if past_terms["brand"]:
                    search_terms["brand"] = past_terms["brand"]
                    break

    # Search with whatever we found
    if search_terms["brand"] or search_terms["color"] or search_terms["category"]:
        products = search_products(
            brand=search_terms["brand"],
            color=search_terms["color"],
            category=search_terms["category"],
            store=store
        )

    # General triggers fallback
    if not products:
        general_triggers = ["what do you have", "show me", "catalog", "collection",
                           "products", "items", "available", "stock", "what kind",
                           "what bags", "what type", "options"]
        if any(trigger in message_text.lower() for trigger in general_triggers):
            products = search_products(store=store)

    # 7. Build context for Claude
    product_context = format_products_for_context(products, currency) if products else ""

    if not products:
        all_products = search_products(store=store)
        if all_products:
            products = all_products
            product_context = format_products_for_context(all_products, currency)

    customer_context = f"""Customer info:
- Phone: {phone}
- Name: {customer.get('name') or 'Unknown'}
- Stage: {customer.get('funnel_stage', 'new')}
- Store: {store}"""

    # Experience layer injection
    winning_examples = get_winning_examples(store=store)
    experience_context = ""
    if winning_examples:
        examples = []
        for w in winning_examples:
            if w.get("bot_response_summary"):
                examples.append(f"- {w.get('intent_detected', 'general')}: {w['bot_response_summary']}")
        if examples:
            experience_context = "\n\n## Past Winning Responses\n" + "\n".join(examples)

    # Append catalog data to last user message
    if claude_messages and claude_messages[-1]["role"] == "user":
        catalog_section = f"\n\n[CATALOG DATA]\n{product_context}" if product_context else ""
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

    if cleaned_messages and cleaned_messages[0]["role"] != "user":
        cleaned_messages.insert(0, {"role": "user", "content": "(conversation started)"})

    # 8. Call Claude (with retry)
    system_prompt = build_system_prompt(store_config or {})
    full_system = f"{system_prompt}\n\n{customer_context}{experience_context}"

    reply = None
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=full_system,
            messages=cleaned_messages,
        )
        reply = response.content[0].text
    except Exception as e:
        log.error(f"Claude API call failed: {e}")
        # Retry once
        try:
            await asyncio.sleep(1)
            response = client.messages.create(
                model=MODEL,
                max_tokens=300,
                system=full_system,
                messages=cleaned_messages,
            )
            reply = response.content[0].text
        except Exception as e2:
            log.error(f"Claude API retry failed: {e2}")
            fallback_msg = "Give me a moment, checking on that for you."
            save_message(phone, "bot", fallback_msg)
            return {"reply": fallback_msg, "images": [], "products": []}

    # 8b. Parse intent/archetype tags
    reply, detected_intent, detected_archetype = parse_tags(reply)

    # 8c. Check for escalation tag
    if reply.startswith(ESCALATE_TAG):
        reply = reply[len(ESCALATE_TAG):].strip()
        try:
            await assign_label(phone, human_label_id, phone_number_id=phone_number_id)
        except Exception as e:
            log.error(f"Failed to assign Human label for {phone}: {e}")
        update_customer(phone, human_takeover=True)
        save_message(phone, "bot", reply)
        save_conversation_outcome(phone, store, intent=detected_intent,
                                   archetype=detected_archetype,
                                   outcome="escalated",
                                   bot_response_summary=reply[:200])
        return {"reply": reply, "images": [], "products": []}

    # 9. Save bot reply
    product_ids = [p["id"] for p in products] if products else []
    save_message(phone, "bot", reply, products_mentioned=product_ids)

    # 10. Update funnel stage
    if customer.get("funnel_stage") == "new" and products:
        update_customer(phone, funnel_stage="interested")

    # 11. Save outcome metadata (for experience layer)
    if detected_intent or detected_archetype:
        save_conversation_outcome(
            phone, store,
            intent=detected_intent,
            archetype=detected_archetype,
            products_discussed=product_ids,
            message_count=len(history),
        )

    return {
        "reply": reply,
        "images": [],  # Image sending disabled — handled by human escalation
        "products": products,
    }
