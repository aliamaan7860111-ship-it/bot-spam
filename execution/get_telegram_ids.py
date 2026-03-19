import os
import asyncio
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()

async def get_group_ids():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or ":" not in token:
        print("❌ Error: Valid TELEGRAM_BOT_TOKEN not found in .env file.")
        print("Please update your .env file with your new bot token first.")
        return

    bot = Bot(token=token)
    try:
        me = await bot.get_me()
        print(f"✅ Successfully connected to bot: @{me.username}")
        print("\n⏳ Fetching recent messages...")
        
        updates = await bot.get_updates(limit=100, allowed_updates=["message", "my_chat_member"])
        if not updates:
            print("\n⚠️ No recent messages found.")
            print("👉 Please go to your Sourcing and Fulfillment groups and send a test message (e.g., 'hello') in each group.")
            print("👉 Then run this script again.")
            return

        groups_found = {}
        for update in updates:
            if update.message and update.message.chat:
                chat = update.message.chat
                if chat.type in ["group", "supergroup"]:
                    groups_found[chat.id] = chat.title
            
            # Check if bot was just added to a group
            if update.my_chat_member and update.my_chat_member.chat:
                chat = update.my_chat_member.chat
                if chat.type in ["group", "supergroup"]:
                    groups_found[chat.id] = chat.title

        if groups_found:
            print("\n🎉 Found the following groups:")
            for chat_id, title in groups_found.items():
                print(f"  📌 Group Name: '{title}'")
                print(f"  🔢 Group ID:   {chat_id}\n")
            print("Copy the relevant ID and update TELEGRAM_SOURCING_GROUP_ID or TELEGRAM_FULFILLMENT_GROUP_ID in your .env file.")
        else:
            print("\n⚠️ No group messages found.")
            print("👉 Please ensure your bot is added to both groups.")
            print("👉 Send a message like 'hello' in both groups to trigger an update.")
            print("👉 Then run this script again.")

    except Exception as e:
        print(f"❌ Error communicating with Telegram: {e}")

if __name__ == "__main__":
    asyncio.run(get_group_ids())
