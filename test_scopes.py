import os
import logging
from dotenv import load_dotenv
from slack_sdk import WebClient

load_dotenv()
client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))

print("--- Scope Test ---")
try:
    res = client.auth_test()
    print(f"Auth Success. User: {res.get('user')}")
except Exception as e:
    print(f"Auth Failed: {e}")

print("\n--- Files Test ---")
try:
    res = client.files_list(count=1)
    print("Files List Success!")
except Exception as e:
    print(f"Files List Failed (as expected): {e}")

print("\n--- History Test ---")
try:
    # Try a random channel ID (the one from logs earlier if I can find it)
    res = client.conversations_history(channel="C016H5K8C8E", limit=1) # Replace with a real one if needed
    print("History Success!")
except Exception as e:
    print(f"History Failed (as expected): {e}")
