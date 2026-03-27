import os
import requests
import json
import re

# Simple manual .env parser so we don't need 'dotenv' library
api_token = ""
def load_token():
    global api_token
    try:
        if os.path.exists(".env"):
            with open(".env", "r") as f:
                content = f.read()
                match = re.search(r"WHATCHIMP_API_TOKEN=(.*)", content)
                if match:
                    api_token = match.group(1).strip().strip('"').strip("'")
    except:
        pass

def inspect_fields():
    load_token()
    if not api_token:
        print("❌ Error: WHATCHIMP_API_TOKEN not found in .env file.")
        return

    print(f"--- WhatChimp Custom Field Inspector ---")
    url = "https://app.whatchimp.com/api/v1/whatsapp/subscriber/custom-fields/list"
    payload = {"apiToken": api_token}
    
    try:
        resp = requests.post(url, data=payload)
        data = resp.json()
        if str(data.get("status")) == "1":
            print("\n✅ Successfully retrieved custom fields:")
            print(json.dumps(data.get("data", []), indent=2))
        else:
            print(f"\n❌ API Error: {data.get('message')}")
    except Exception as e:
        print(f"\n❌ Request failed: {e}")

if __name__ == "__main__":
    inspect_fields()
