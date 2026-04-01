"""
Supabase client for the WhatsApp Sales Bot.
Handles all DB operations: customers, conversations, products, follow-ups.
"""

import os
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY")
)


# ── Customers ──────────────────────────────────────────────

def get_customer(phone: str) -> dict | None:
    """Get customer by phone number."""
    res = supabase.table("customers").select("*").eq("phone", phone).execute()
    if res.data and len(res.data) > 0:
        return res.data[0]
    return None


def create_customer(phone: str, name: str = None, store: str = None) -> dict:
    """Create a new customer. Returns existing if phone already exists."""
    existing = get_customer(phone)
    if existing:
        # Update name if we got one and they didn't have one
        if name and not existing.get("name"):
            supabase.table("customers").update({"name": name}).eq("phone", phone).execute()
            existing["name"] = name
        return existing

    data = {
        "phone": phone,
        "store": store or os.getenv("DEFAULT_STORE", "amarasroom"),
        "funnel_stage": "new",
    }
    if name:
        data["name"] = name

    res = supabase.table("customers").insert(data).execute()
    return res.data[0]


def update_customer(phone: str, **fields) -> dict:
    """Update customer fields. Pass any column as keyword arg."""
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = supabase.table("customers").update(fields).eq("phone", phone).execute()
    return res.data[0] if res.data else None


def update_last_message(phone: str):
    """Bump last_message_at timestamp."""
    supabase.table("customers").update({
        "last_message_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("phone", phone).execute()


# ── Conversations ──────────────────────────────────────────

def save_message(customer_phone: str, role: str, content: str,
                 message_type: str = "text", image_url: str = None,
                 products_mentioned: list = None, wa_message_id: str = None) -> dict:
    """Save a message to conversation history."""
    data = {
        "customer_phone": customer_phone,
        "role": role,
        "content": content,
        "message_type": message_type,
    }
    if image_url:
        data["image_url"] = image_url
    if products_mentioned:
        data["products_mentioned"] = products_mentioned
    if wa_message_id:
        data["wa_message_id"] = wa_message_id

    res = supabase.table("conversations").insert(data).execute()
    return res.data[0]


def get_conversation_history(customer_phone: str, limit: int = None) -> list:
    """Get recent conversation history for a customer, oldest first."""
    if limit is None:
        limit = int(os.getenv("MAX_CONVERSATION_HISTORY", "20"))

    res = (supabase.table("conversations")
           .select("role, content, message_type, image_url, created_at")
           .eq("customer_phone", customer_phone)
           .order("created_at", desc=True)
           .limit(limit)
           .execute())

    # Reverse so oldest message is first (chronological order)
    return list(reversed(res.data)) if res.data else []


# ── Products ───────────────────────────────────────────────

def search_products(query: str = None, brand: str = None, color: str = None,
                    category: str = None, store: str = None) -> list:
    """Search products by various filters. Uses searchable_name for text search."""
    q = supabase.table("products").select("*").eq("stock_status", "in_stock")

    if store:
        q = q.eq("store", store)
    if brand:
        q = q.ilike("brand", f"%{brand}%")
    if color:
        q = q.ilike("color", f"%{color}%")
    if category:
        q = q.ilike("category", f"%{category}%")
    if query:
        q = q.ilike("searchable_name", f"%{query}%")

    res = q.execute()
    return res.data or []


def get_product_by_id(product_id: str) -> dict | None:
    """Get a single product by its UUID."""
    res = supabase.table("products").select("*").eq("id", product_id).maybe_single().execute()
    return res.data


# ── Follow-up Queue ────────────────────────────────────────

def add_follow_up(customer_phone: str, stage: int, scheduled_at: datetime,
                  template_name: str = None) -> dict:
    """Schedule a follow-up message."""
    data = {
        "customer_phone": customer_phone,
        "stage": stage,
        "scheduled_at": scheduled_at.isoformat(),
    }
    if template_name:
        data["template_name"] = template_name

    res = supabase.table("follow_up_queue").insert(data).execute()
    return res.data[0]


def get_pending_follow_ups() -> list:
    """Get all follow-ups that are due and haven't been sent."""
    now = datetime.now(timezone.utc).isoformat()
    res = (supabase.table("follow_up_queue")
           .select("*, customers(name, funnel_stage, human_takeover)")
           .eq("sent", False)
           .lte("scheduled_at", now)
           .execute())
    return res.data or []


def mark_follow_up_sent(follow_up_id: str):
    """Mark a follow-up as sent."""
    supabase.table("follow_up_queue").update({"sent": True}).eq("id", follow_up_id).execute()


def cancel_follow_ups(customer_phone: str):
    """Cancel all pending follow-ups for a customer (they replied or ordered)."""
    supabase.table("follow_up_queue").update({"sent": True}).eq(
        "customer_phone", customer_phone
    ).eq("sent", False).execute()
