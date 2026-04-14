"""
Quick test: trigger the label-change webhook locally and see what happens.
Also: test what WhatChimp subscriber data looks like for a known phone.
Run on GCP: python test_label_webhook.py
"""

import os
import httpx
from dotenv import load_dotenv
load_dotenv()

API_TOKEN = os.getenv("WHATCHIMP_API_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATCHIMP_PHONE_NUMBER_ID")
BASE_URL = "https://app.whatchimp.com/api/v1"

TEST_PHONE = "971561511090"  # Ahmed's number from your test

print("=" * 50)
print("1. Testing: Get subscriber info from WhatChimp")
print("=" * 50)

resp = httpx.post(f"{BASE_URL}/whatsapp/subscriber/get", data={
    "apiToken": API_TOKEN,
    "phone_number_id": PHONE_NUMBER_ID,
    "phone_number": TEST_PHONE,
})
print(f"Status: {resp.status_code}")
import json
data = resp.json()
print(json.dumps(data, indent=2)[:2000])

print("\n" + "=" * 50)
print("2. Testing: List all custom fields in your account")
print("=" * 50)

resp2 = httpx.post(f"{BASE_URL}/whatsapp/subscriber/custom-fields/list", data={
    "apiToken": API_TOKEN,
})
print(f"Status: {resp2.status_code}")
print(json.dumps(resp2.json(), indent=2)[:1000])

print("\n" + "=" * 50)
print("3. Testing: Hit label-change webhook locally")
print("=" * 50)

# Test silence
try:
    resp3 = httpx.post("http://127.0.0.1:8000/webhook/label-change", json={
        "chat_id": TEST_PHONE,
        "action": "silence_bot",
    }, timeout=5)
    print(f"Silence test: {resp3.status_code} — {resp3.json()}")
except Exception as e:
    print(f"Silence test failed (is sales-bot running?): {e}")

# Test resume
try:
    resp4 = httpx.post("http://127.0.0.1:8000/webhook/label-change", json={
        "chat_id": TEST_PHONE,
        "action": "resume_bot",
    }, timeout=5)
    print(f"Resume test: {resp4.status_code} — {resp4.json()}")
except Exception as e:
    print(f"Resume test failed: {e}")

print("\n" + "=" * 50)
print("4. Assign 'phone' custom field to subscriber")
print("=" * 50)

resp5 = httpx.post(f"{BASE_URL}/whatsapp/subscriber/chat/assign-custom-fields", data={
    "apiToken": API_TOKEN,
    "phone_number_id": PHONE_NUMBER_ID,
    "phone_number": TEST_PHONE,
    "custom_fields": json.dumps({"phone": TEST_PHONE}),
})
print(f"Assign custom field: {resp5.status_code}")
print(json.dumps(resp5.json(), indent=2)[:500])

print("\nDone! If step 4 worked, you can now use #phone# in WhatChimp flows.")
