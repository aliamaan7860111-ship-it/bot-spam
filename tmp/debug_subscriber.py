import os
import requests
import json

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
phone_number = "971555614190"

# Try multiple endpoint variations to find the right one
endpoints = [
    "https://app.whatchimp.com/api/v1/whatsapp/subscriber/chat/get-details",
    "https://app.whatchimp.com/api/v1/whatsapp/subscriber/details",
    "https://app.whatchimp.com/api/v1/whatsapp/subscriber/get",
    "https://app.whatchimp.com/api/v1/whatsapp/subscriber/list",
]

for url in endpoints:
    print(f"\n--- Testing: {url} ---")
    try:
        payload = {
            "apiToken": api_token,
            "phone_number_id": phone_number_id,
            "phone_number": phone_number,
            "phoneNumberID": phone_number_id,
            "phoneNumber": phone_number,
        }
        resp = requests.post(url, data=payload, timeout=5)
        print(f"Status: {resp.status_code}")
        print(f"Raw Response: {resp.text[:500]}")
        if resp.text:
            try:
                print(json.dumps(resp.json(), indent=2))
            except:
                pass
    except Exception as e:
        print(f"Error: {e}")
