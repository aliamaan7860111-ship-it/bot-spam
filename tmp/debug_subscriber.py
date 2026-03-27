import os
import requests
import json
import re

def load_env():
    env = {}
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env

env = load_env()
api_token = env.get("WHATCHIMP_API_TOKEN", "")
phone_number_id = env.get("WHATCHIMP_PHONE_NUMBER_ID", "")

# The phone number from the webhook
phone_number = "971555614190"

url = "https://app.whatchimp.com/api/v1/whatsapp/subscriber/chat/get-details"
payload = {
    "apiToken": api_token,
    "phone_number_id": phone_number_id,
    "phone_number": phone_number
}

print(f"--- WhatChimp get-details for {phone_number} ---")
resp = requests.post(url, data=payload)
data = resp.json()

print(json.dumps(data, indent=2))
