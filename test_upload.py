import os
import logging
from slack_bolt import App
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

def test_upload():
    channel_id = "U054RV4JX4Y" # The user's ID or a test channel ID
    file_path = "final_ad.png"
    
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return

    # Note: files_upload_v2 requires a CHANNEL ID (starting with C, G, or D), not a USER ID (starting with U).
    # We must find the DM channel ID for the given User ID.
    try:
        print(f"Finding DM channel for {channel_id}...")
        im_list = app.client.conversations_list(types="im")
        target_channel = None
        for channel in im_list["channels"]:
            if channel["user"] == channel_id:
                target_channel = channel["id"]
                break
        
        if not target_channel:
            print("DM channel not found. Using the User ID as a final fallback (may fail if regex is strict).")
            target_channel = user_id
        else:
            print(f"Found DM channel: {target_channel}")

        print(f"Attempting to upload {file_path} to {target_channel}...")
        result = app.client.files_upload_v2(
            channel=target_channel,
            initial_comment="Test Upload via Antigravity Debugger",
            file=file_path,
            title="Test Image"
        )
        print("Upload successful!")
        print(result)
    except Exception as e:
        print(f"Upload failed: {e}")

if __name__ == "__main__":
    test_upload()
