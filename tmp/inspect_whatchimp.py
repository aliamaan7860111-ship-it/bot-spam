import os
import requests
import json
from dotenv import load_dotenv
from pathlib import Path

# Load env variables
load_dotenv(".env")

WHATCHIMP_API_TOKEN = os.getenv("WHATCHIMP_API_TOKEN", "")
WHATCHIMP_PHONE_NUMBER_ID = os.getenv("WHATCHIMP_PHONE_NUMBER_ID", "")
API_BASE = "https://app.whatchimp.com/api/v1/whatsapp"

def inspect_fields():
    print(f"--- WhatChimp Custom Field Inspector ---")
    url = f"{API_BASE}/subscriber/custom-fields/list"
    payload = {"apiToken": WHATCHIMP_API_TOKEN}
    
    try:
        resp = requests.post(url, data=payload)
        data = resp.json()
        if str(data.get("status")) == "1":
            print("\n✅ Successfully retrieved custom fields:")
            print(json.dumps(data.get("data", []), indent=2))
        else:
            print(f"\n❌ Error: {data.get('message')}")
    except Exception as e:
        print(f"\n❌ Request failed: {e}")

if __name__ == "__main__":
    inspect_fields()
