"""
Supabase client for the WhatsApp Sales Bot.
Handles all DB operations: customers, conversations, products, follow-ups, outcomes.
"""

import os
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv import load_dotenv
from config import retry, get_logger

load_dotenv()

log = get_logger("supabase_client")

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY")
)


# ── Customers ──────────────────────────────────────────────

@retry(max_attempts=2, delay=0.5, fallback=None)
def get_customer(phone: str) -> dict | None:
    """Get customer by phone number."""
    res = supabase.table("customers").select("*").eq("phone", phone).execute()
    if res.data and len(res.data) > 0:
        return res.data[0]
    return None


def create_customer(phone: str, name: str = None, store: str = None) -> dict:
    """Create a new customer. Returns existing if phone already exists.
    Store must be passed explicitly from webhook routing — no default."""
    existing = get_customer(phone)
    if existing:
        if name and not existing.get("name"):
            supabase.table("customers").update({"name": name}).eq("phone", phone).execute()
            existing["name"] = name
        return existing

    data = {
        "phone": phone,
        "store": store or "unknown",
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

def check_message_exists(wa_message_id: str) -> bool:
    """Check if a message with this WhatChimp ID already exists (deduplication)."""
    if not wa_message_id:
        return False
    res = (supabase.table("conversations")
           .select("id")
           .eq("wa_message_id", wa_message_id)
           .limit(1)
           .execute())
    return bool(res.data)


@retry(max_attempts=2, delay=0.5)
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
        limit = int(os.getenv("MAX_CONVERSATION_HISTORY", "10"))

    res = (supabase.table("conversations")
           .select("role, content, message_type, image_url, created_at")
           .eq("customer_phone", customer_phone)
           .order("created_at", desc=True)
           .limit(limit)
           .execute())

    return list(reversed(res.data)) if res.data else []


def get_smart_history(customer_phone: str, first_n: int = 2, last_n: int = 8) -> list:
    """Get first N + last N messages for better context in long conversations."""
    # Get the most recent messages
    recent = (supabase.table("conversations")
              .select("role, content, message_type, image_url, created_at")
              .eq("customer_phone", customer_phone)
              .order("created_at", desc=True)
              .limit(last_n)
              .execute())
    recent_msgs = list(reversed(recent.data)) if recent.data else []

    # Get the earliest messages
    earliest = (supabase.table("conversations")
                .select("role, content, message_type, image_url, created_at")
                .eq("customer_phone", customer_phone)
                .order("created_at", desc=False)
                .limit(first_n)
                .execute())
    earliest_msgs = earliest.data if earliest.data else []

    # Merge: earliest first, then recent, avoiding duplicates
    seen_times = set()
    combined = []
    for msg in earliest_msgs + recent_msgs:
        key = msg["created_at"]
        if key not in seen_times:
            seen_times.add(key)
            combined.append(msg)

    # Sort chronologically
    combined.sort(key=lambda m: m["created_at"])
    return combined


# ── Products ───────────────────────────────────────────────

@retry(max_attempts=2, delay=1.0, fallback=[])
def search_products(query: str = None, brand: str = None, color: str = None,
                    category: str = None, store: str = None) -> list:
    """Search products by various filters."""
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
    """Cancel all pending follow-ups for a customer."""
    supabase.table("follow_up_queue").update({"sent": True}).eq(
        "customer_phone", customer_phone
    ).eq("sent", False).execute()


# ── Experience Layer: Conversation Outcomes ────────────────

def save_conversation_outcome(customer_phone: str, store: str,
                               intent: str = None, archetype: str = None,
                               objection_type: str = None,
                               bot_response_summary: str = None,
                               outcome: str = None,
                               products_discussed: list = None,
                               message_count: int = 0) -> dict:
    """Save a conversation outcome for the experience layer."""
    data = {
        "customer_phone": customer_phone,
        "store": store,
    }
    if intent:
        data["intent_detected"] = intent
    if archetype:
        data["archetype_detected"] = archetype
    if objection_type:
        data["objection_type"] = objection_type
    if bot_response_summary:
        data["bot_response_summary"] = bot_response_summary
    if outcome:
        data["outcome"] = outcome
    if products_discussed:
        data["products_discussed"] = products_discussed
    if message_count:
        data["message_count"] = message_count

    try:
        res = supabase.table("conversation_outcomes").insert(data).execute()
        return res.data[0] if res.data else {}
    except Exception as e:
        log.warning(f"Failed to save conversation outcome: {e}")
        return {}


def get_winning_examples(intent: str = None, archetype: str = None,
                         store: str = None, limit: int = 3) -> list:
    """Fetch past winning conversation examples for the experience layer.
    Returns empty list until sufficient data is collected."""
    try:
        q = supabase.table("conversation_outcomes").select("*").eq("outcome", "converted")
        if intent:
            q = q.eq("intent_detected", intent)
        if archetype:
            q = q.eq("archetype_detected", archetype)
        if store:
            q = q.eq("store", store)
        q = q.order("created_at", desc=True).limit(limit)
        res = q.execute()
        return res.data or []
    except Exception:
        return []
