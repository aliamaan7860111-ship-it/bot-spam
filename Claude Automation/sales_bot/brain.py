"""
Sales Bot Brain — the core AI logic.
Loads context, calls Claude, returns a sales-appropriate response.
"""

import os
import json
import httpx
import anthropic
from dotenv import load_dotenv
from supabase_client import (
    get_customer, create_customer, update_customer, update_last_message,
    save_message, get_conversation_history, search_products, cancel_follow_ups
)

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
STORE = os.getenv("DEFAULT_STORE", "amarasroom")
CURRENCY = os.getenv("STORE_CURRENCY", "AED")

# ── System Prompt ──────────────────────────────────────────
# Basic version — will be replaced with full training manual later

SYSTEM_PROMPT = f"""You are a friendly and confident sales assistant for Amara's Room — a premium luxury accessories store based in Dubai.

# Your Personality
- Warm, approachable, and professional
- You know your products inside out
- You match the customer's language — if they write in Arabic, Urdu, or Roman Urdu, reply in the same language
- You keep messages short and WhatsApp-friendly (2-4 lines max per message)
- You use emojis sparingly and naturally
- You never sound robotic or scripted

# Your Job
1. Greet warmly and understand what the customer is looking for
2. Search your product catalog and recommend matching items
3. Share product details: name, color, price, and what's included
4. Handle questions and concerns confidently
5. When ready, collect order details: name, address, phone number, and confirm the product
6. Confirm the order clearly before finalizing

# What You Know
- All prices are in {CURRENCY}
- All items come with presentation box and standard accessories
- Cash on delivery is available
- Shipping is available across the UAE

# Rules
- NEVER claim items are "original" or "authentic" — just describe the quality and craftsmanship
- NEVER make up product information — only reference products from the catalog data provided
- NEVER continue messaging after a customer says "stop" or "not interested"
- If you don't have an answer, say "Let me check with the team and get back to you"
- If the customer seems frustrated or angry, offer to connect them with a team member
- One question at a time — don't overwhelm with multiple questions
- Keep it conversational, not transactional

# Product Recommendations
When recommending products, format them clearly:
- Product name — Color | Price {CURRENCY}
- Brief one-line description if helpful
- Offer to show photos

# Order Collection
When a customer wants to buy, collect these one at a time:
1. Full name
2. Shipping address (full address including area and city)
3. Phone number for the delivery rider
4. Confirm: product, quantity, total price, address
Only after they confirm all details, say "Order confirmed!" and nothing more about the order.

# When You Have No Matching Products
If the customer asks for something not in your catalog, say something like:
"We don't have that exact piece right now, but let me show you some similar options we have!"
Then suggest the closest matches from your catalog.
"""


def format_products_for_context(products: list) -> str:
    """Format product list as context for Claude."""
    if not products:
        return "No matching products found in catalog."

    lines = ["Available products matching the query:"]
    for p in products:
        line = f"- {p['title']} | Color: {p.get('color', 'N/A')} | {p['price']} {p.get('currency', CURRENCY)}"
        if p.get('compare_at_price'):
            line += f" (was {p['compare_at_price']} {p.get('currency', CURRENCY)})"
        if p.get('material'):
            line += f" | Material: {p['material']}"
        if p.get('description'):
            line += f"\n  Description: {p['description'][:150]}"
        if p.get('studio_images'):
            line += f"\n  Images available: {len(p['studio_images'])} photos"
        line += f"\n  Product ID: {p['id']}"
        lines.append(line)

    return "\n".join(lines)


def format_conversation_history(history: list) -> list:
    """Convert DB conversation history to Claude messages format."""
    messages = []
    for msg in history:
        role = "user" if msg["role"] == "customer" else "assistant"
        content = msg["content"]
        if msg.get("message_type") == "image" and msg.get("image_url"):
            content = f"[Customer sent an image: {msg['image_url']}]\n{content}"
        messages.append({"role": role, "content": content})
    return messages


async def identify_image(image_url: str) -> str:
    """Use Claude Vision to identify a product from an image."""
    # Download the image
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(image_url)
        if resp.status_code != 200:
            return "Could not download the image."

    import base64
    image_data = base64.b64encode(resp.content).decode("utf-8")

    # Determine media type
    content_type = resp.headers.get("content-type", "image/jpeg")
    if "png" in content_type:
        media_type = "image/png"
    elif "webp" in content_type:
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"

    vision_resp = client.messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": image_data},
                },
                {
                    "type": "text",
                    "text": "Identify this product. What brand is it? What type of item (bag, wallet, shoes, etc.)? What color? What specific model name if you can tell? Reply in this format only:\nBrand: ...\nType: ...\nColor: ...\nModel: ...",
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
    image_context = ""
    if message_type == "image" and image_url:
        image_context = await identify_image(image_url)
        save_message(phone, "customer", f"[Sent an image]", message_type="image", image_url=image_url)
        # Use the Vision identification to search products
        message_text = f"Customer sent an image. Vision identified: {image_context}"
    else:
        save_message(phone, "customer", message_text, message_type="text")

    # 5. Load conversation history
    history = get_conversation_history(phone)
    claude_messages = format_conversation_history(history)

    # 6. Search for products based on the message
    # Extract potential search terms from the message
    products = []
    search_text = message_text.lower()

    # Search by brand keywords
    brands = ["chanel", "hermes", "louis vuitton", "lv", "gucci", "dior", "prada", "fendi", "celine", "balenciaga", "ysl", "bottega"]
    colors = ["black", "white", "gold", "silver", "brown", "beige", "red", "blue", "green", "pink", "metallic"]
    categories = ["bag", "bags", "wallet", "wallets", "shoes", "belt", "belts", "scarf", "scarves"]

    matched_brand = None
    matched_color = None
    matched_category = None

    for b in brands:
        if b in search_text:
            matched_brand = b
            break

    for c in colors:
        if c in search_text:
            matched_color = c
            break

    for cat in categories:
        if cat in search_text:
            matched_category = cat
            break

    # If image was identified, parse Vision output for search
    if image_context:
        ic_lower = image_context.lower()
        for b in brands:
            if b in ic_lower:
                matched_brand = b
                break
        for c in colors:
            if c in ic_lower:
                matched_color = c
                break

    # Search with whatever we found
    if matched_brand or matched_color or matched_category:
        products = search_products(
            brand=matched_brand,
            color=matched_color,
            category=matched_category,
            store=STORE
        )
    elif any(word in search_text for word in ["product", "item", "what do you have", "show me", "catalog", "collection"]):
        # Generic browse — show everything
        products = search_products(store=STORE)

    # 7. Build the context for Claude
    product_context = format_products_for_context(products) if products else ""

    customer_context = f"""Customer info:
- Phone: {phone}
- Name: {customer.get('name', 'Unknown')}
- Stage: {customer.get('funnel_stage', 'new')}
- Store: {customer.get('store', STORE)}"""

    # Add product context to the latest user message
    if product_context:
        # Append product data as a system-like injection in the user message
        if claude_messages and claude_messages[-1]["role"] == "user":
            claude_messages[-1]["content"] += f"\n\n[CATALOG DATA — use this to answer]\n{product_context}"
        else:
            claude_messages.append({
                "role": "user",
                "content": f"{message_text}\n\n[CATALOG DATA — use this to answer]\n{product_context}"
            })

    # Ensure messages alternate properly (Claude requirement)
    cleaned_messages = []
    last_role = None
    for msg in claude_messages:
        if msg["role"] == last_role:
            # Merge consecutive same-role messages
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

    # 9. Determine which product images to send
    images_to_send = []
    if products:
        # Check if Claude's reply mentions specific products — send images for first mentioned
        for p in products:
            title_words = p["title"].lower().split()
            if any(word in reply.lower() for word in title_words if len(word) > 3):
                if p.get("studio_images"):
                    images_to_send = p["studio_images"][:2]  # Send max 2 images
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
