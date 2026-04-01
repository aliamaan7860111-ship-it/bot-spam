"""
WhatChimp API client for the WhatsApp Sales Bot.
Handles sending text messages, template messages, and image messages.
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://app.whatchimp.com/api/v1"
API_TOKEN = os.getenv("WHATCHIMP_API_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATCHIMP_PHONE_NUMBER_ID")


async def send_text(phone: str, message: str) -> dict:
    """Send a text message (within 24h session window)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{BASE_URL}/whatsapp/send", data={
            "apiToken": API_TOKEN,
            "phone_number_id": PHONE_NUMBER_ID,
            "phone_number": phone,
            "message": message,
        })
        return resp.json()


async def send_image(phone: str, image_url: str, caption: str = "") -> dict:
    """Send an image message with optional caption."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{BASE_URL}/whatsapp/send", data={
            "apiToken": API_TOKEN,
            "phone_number_id": PHONE_NUMBER_ID,
            "phone_number": phone,
            "message": caption,
            "image_url": image_url,
        })
        return resp.json()


async def send_template(phone: str, template_name: str, language: str = "en_US",
                        variables: dict = None) -> dict:
    """Send a template message (works outside 24h window)."""
    data = {
        "apiToken": API_TOKEN,
        "phone_number_id": PHONE_NUMBER_ID,
        "phone_number": phone,
        "template_name": template_name,
        "language_code": language,
    }
    if variables:
        for key, value in variables.items():
            data[key] = value  # variable1=X, variable2=Y, etc.

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{BASE_URL}/whatsapp/send", data=data)
        return resp.json()


async def assign_label(phone: str, label_ids: str) -> dict:
    """Assign labels to a subscriber. label_ids = comma-separated IDs."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/whatsapp/subscriber/chat/assign-labels",
            data={
                "apiToken": API_TOKEN,
                "phone_number_id": PHONE_NUMBER_ID,
                "phone_number": phone,
                "label_ids": label_ids,
            }
        )
        return resp.json()


async def assign_to_team_member(phone: str, team_member_id: int) -> dict:
    """Assign chat to a human team member (for escalation)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/whatsapp/subscriber/chat/assign-to-team-member",
            data={
                "apiToken": API_TOKEN,
                "phone_number_id": PHONE_NUMBER_ID,
                "phone_number": phone,
                "team_member_id": str(team_member_id),
            }
        )
        return resp.json()


async def add_note(phone: str, note: str) -> dict:
    """Add an internal note to subscriber chat."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/whatsapp/subscriber/chat/add-notes",
            data={
                "apiToken": API_TOKEN,
                "phone_number_id": PHONE_NUMBER_ID,
                "phone_number": phone,
                "note_text": note,
            }
        )
        return resp.json()
