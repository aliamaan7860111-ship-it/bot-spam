"""
WhatChimp API client for the WhatsApp Sales Bot.
Handles sending text messages, template messages, and label management.
Multi-store aware — phone_number_id passed per-call.
"""

import os
import httpx
from dotenv import load_dotenv
from config import retry, get_logger

load_dotenv()

log = get_logger("whatchimp")

BASE_URL = os.getenv("WHATCHIMP_BASE_URL", "https://app.whatchimp.com/api/v1")
API_TOKEN = os.getenv("WHATCHIMP_API_TOKEN")
# Legacy fallback — new code passes phone_number_id per-call from store config
_DEFAULT_PHONE_NUMBER_ID = os.getenv("WHATCHIMP_PHONE_NUMBER_ID")


@retry(max_attempts=2, delay=0.5)
async def send_text(phone: str, message: str, phone_number_id: str = None) -> dict:
    """Send a text message (within 24h session window)."""
    pnid = phone_number_id or _DEFAULT_PHONE_NUMBER_ID
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{BASE_URL}/whatsapp/send", data={
            "apiToken": API_TOKEN,
            "phone_number_id": pnid,
            "phone_number": phone,
            "message": message,
        })
        result = resp.json()
        log.info(f"send_text to {phone}: status={resp.status_code}")
        return result


async def send_template(phone: str, template_name: str, language: str = "en_US",
                        variables: dict = None, phone_number_id: str = None) -> dict:
    """Send a template message (works outside 24h window)."""
    pnid = phone_number_id or _DEFAULT_PHONE_NUMBER_ID
    data = {
        "apiToken": API_TOKEN,
        "phone_number_id": pnid,
        "phone_number": phone,
        "template_name": template_name,
        "language_code": language,
    }
    if variables:
        for key, value in variables.items():
            data[key] = value

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{BASE_URL}/whatsapp/send", data=data)
        return resp.json()


async def assign_label(phone: str, label_ids: str, phone_number_id: str = None) -> dict:
    """Assign labels to a subscriber. label_ids = comma-separated IDs."""
    pnid = phone_number_id or _DEFAULT_PHONE_NUMBER_ID
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/whatsapp/subscriber/chat/assign-labels",
            data={
                "apiToken": API_TOKEN,
                "phone_number_id": pnid,
                "phone_number": phone,
                "label_ids": label_ids,
            }
        )
        return resp.json()


async def remove_label(phone: str, label_ids: str, phone_number_id: str = None) -> dict:
    """Remove labels from a subscriber."""
    pnid = phone_number_id or _DEFAULT_PHONE_NUMBER_ID
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/whatsapp/subscriber/chat/remove-labels",
            data={
                "apiToken": API_TOKEN,
                "phone_number_id": pnid,
                "phone_number": phone,
                "label_ids": label_ids,
            }
        )
        return resp.json()


async def get_subscriber(phone: str, phone_number_id: str = None) -> dict | None:
    """Get subscriber details including labels."""
    pnid = phone_number_id or _DEFAULT_PHONE_NUMBER_ID
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/whatsapp/subscriber/get",
            data={
                "apiToken": API_TOKEN,
                "phone_number_id": pnid,
                "phone_number": phone,
            }
        )
        data = resp.json()
        if data.get("status") == "1" and data.get("message"):
            return data["message"][0] if isinstance(data["message"], list) else data["message"]
        return None


async def has_human_label(phone: str, human_label_id: str = "294168",
                          phone_number_id: str = None) -> bool:
    """Check if subscriber has the Human label in WhatChimp."""
    sub = await get_subscriber(phone, phone_number_id)
    if not sub:
        return False
    label_names = sub.get("label_names") or ""
    label_ids = sub.get("label_ids") or ""
    # Check by name or ID
    if "Human" in str(label_names):
        return True
    if str(human_label_id) in str(label_ids):
        return True
    return False


async def assign_to_team_member(phone: str, team_member_id: int,
                                 phone_number_id: str = None) -> dict:
    """Assign chat to a human team member (for escalation)."""
    pnid = phone_number_id or _DEFAULT_PHONE_NUMBER_ID
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/whatsapp/subscriber/chat/assign-to-team-member",
            data={
                "apiToken": API_TOKEN,
                "phone_number_id": pnid,
                "phone_number": phone,
                "team_member_id": str(team_member_id),
            }
        )
        return resp.json()


async def add_note(phone: str, note: str, phone_number_id: str = None) -> dict:
    """Add an internal note to subscriber chat."""
    pnid = phone_number_id or _DEFAULT_PHONE_NUMBER_ID
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/whatsapp/subscriber/chat/add-notes",
            data={
                "apiToken": API_TOKEN,
                "phone_number_id": pnid,
                "phone_number": phone,
                "note_text": note,
            }
        )
        return resp.json()
