import os
import logging
import threading
import time
import re
import json
import requests as req_lib
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize logging
logging.basicConfig(level=logging.INFO)

# Initialize the Slack app
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

from claude_engine import generate_ad_concepts
from slack_ui import build_loading_block, build_concepts_blocks
from extract_context import extract_urls

# Global In-Memory Store for Ad Concepts to bypass Slack's 2000-char button character limit
CONCEPT_STORE = {}

def safe_post(target_id, fallback_user, text=None, blocks=None, is_file=False, file_path=None, comment=None, title=None):
    """
    Global helper to post to a channel with a fallback to user's DM if it fails.
    Handles both chat_postMessage and files_upload_v2.
    """
    try:
        if is_file:
            try:
                # files_upload_v2: using the 'channel' parameter (SDK handles mapping to channel_id)
                # to avoid internal 'multiple values for channel_id' errors.
                app.client.files_upload_v2(channel=target_id, initial_comment=comment, file=file_path, title=title)
            except Exception as e:
                logging.warning(f"File upload to {target_id} failed: {e}. Trying fallback DM {fallback_user}...")
                app.client.files_upload_v2(channel=fallback_user, initial_comment=comment, file=file_path, title=title)
        else:
            try:
                # chat_postMessage uses 'channel'
                app.client.chat_postMessage(channel=target_id, text=text, blocks=blocks)
            except Exception as e:
                logging.warning(f"Message to {target_id} failed: {e}. Trying fallback DM {fallback_user}...")
                app.client.chat_postMessage(channel=fallback_user, text=text, blocks=blocks)
    except Exception as e:
        logging.error(f"FATAL: safe_post failed completely: {e}")

# --- Shared Logic for Ad Generation ---
def shared_ad_logic(user_input, user_id, channel_id, say, msg_files=None):
    session_id = f"session_{int(time.time())}"
    
    def background_task():
        try:
            # 0. Initial Loading Post (Inside thread to avoid blocking ACK)
            safe_post(
                channel_id, user_id, 
                blocks=build_loading_block(user_id, user_input),
                text=f"Ad pipeline starting for {user_input[:50]}..."
            )
            
            product_img = None
            style_gallery = []
            brand_name = "Unknown"
            
            token = os.environ.get("SLACK_BOT_TOKEN")
            headers = {"Authorization": f"Bearer {token}"}
            
            # --- Task Analysis: Extract URL first to define Strategy ---
            from extract_context import extract_urls
            found_urls = extract_urls(user_input)
            
            # 1. FILE COLLECTION (Simplified Single-Image Model)
            downloaded_files = []
            if msg_files is not None:
                logging.info(f"[IMAGE] Collecting attachments...")
                for i, f in enumerate(msg_files[:5]):
                    mimetype = f.get('mimetype', 'unknown')
                    if mimetype.startswith('image/'):
                        dl_url = f.get('url_private_download') or f.get('url_private')
                        if dl_url:
                            r = req_lib.get(dl_url, headers=headers, timeout=20)
                            if r.status_code == 200:
                                path_name = f"{session_id}_attachment_{i}.png"
                                with open(path_name, 'wb') as fh: fh.write(r.content)
                                downloaded_files.append(path_name)

            # 2. STRATEGY SELECTION
            if found_urls:
                # STRATEGY: Scrape Website for Product, use first attachment for Style
                try:
                    from extract_context import extract_shopify_original_image
                    scraped = extract_shopify_original_image(found_urls[0])
                    if scraped:
                        brand_name = scraped.get('brand_name', 'Unknown')
                        img_url = scraped.get('image_url') or (scraped.get('images') and scraped['images'][0].get('src'))
                        if img_url:
                            logging.info(f"[STRATEGY] Scrape-Product. URL: {img_url}")
                            r = req_lib.get(img_url, timeout=20)
                            if r.status_code == 200:
                                product_img = f"{session_id}_scraped_product.png"
                                with open(product_img, 'wb') as fh: fh.write(r.content)
                        # Reverted: Only use the first attachment as the style reference
                        style_gallery = [downloaded_files[0]] if downloaded_files else []
                except Exception as e:
                    logging.warning(f"Scraper Strategy failed: {e}")
            
            # Fallback: If no URL provided, we revert to Legacy (Attachment 0 = Product, Rest = Style)
            if not product_img and downloaded_files:
                product_img = downloaded_files[0]
                style_gallery = downloaded_files[1:]

            # 4. ERROR IF NO IMAGE
            if not product_img:
                err_msg = (
                    ":camera: *No product image found.*\n\n"
                    "Please upload a photo and write `/create-ad` (or just `create-ad`) in the **caption**, or provide a Shopify product URL."
                )
                safe_post(channel_id, user_id, text=err_msg)
                return

            # 5. UPSCALE + SURGICAL CLEAN
            from image_engine import upscale_image, remove_background, surgical_crop
            from claude_engine import get_product_bounding_box
            
            # Upscale (Quality Improvement)
            try:
                upscaled_path = f"{session_id}_up.png"
                res = upscale_image(product_img, upscaled_path)
                if res: product_img = res
            except Exception as e:
                logging.warning(f"Upscaling failed: {e}")

            # Surgical Clean (Remove Badges/Glasses/Clutter)
            try:
                logging.info(f"[CLEAN] Attempting surgical isolation for {product_img}")
                box = get_product_bounding_box(product_img)
                if box:
                    surgical_crop(product_img, box)
                    remove_background(product_img)
                    
                    # 2-SECOND SAFETY BUFFER FOR WINDOWS FILE LOCKS
                    logging.info("[AUDIT] Waiting 2s for file-system sync before Slack upload...")
                    time.sleep(2)
                    
                    # Audit: Show the user the isolated asset so they see what the AI is using
                    safe_post(channel_id, user_id, is_file=True, file_path=product_img, comment=":mag: *Cleaned & Isolated Asset (Purity Secured):*")
            except Exception as e:
                logging.warning(f"Surgical clean failed (falling back to original): {e}")

            # Generate Concepts via Moodboard Synthesis
            from claude_engine import generate_ad_concepts
            ad_concepts = generate_ad_concepts(user_input, style_img_path=style_gallery, brand_name=brand_name)
            if ad_concepts:
                CONCEPT_STORE[session_id] = {
                    "concepts": ad_concepts, 
                    "product": product_img, 
                    "style": style_gallery
                }
                # Pass the style_gallery to the UI builder for the Moodboard Preview
                blocks = build_concepts_blocks(ad_concepts, session_id, style_gallery=style_gallery)
                safe_post(channel_id, user_id, blocks=blocks, text="Ad concepts ready!")
            else:
                safe_post(channel_id, user_id, text="❌ Claude failed to generate concepts.")
        except Exception as e:
            logging.error(f"Pipeline Error: {e}")
            safe_post(channel_id, user_id, text=f"❌ Error: {e}")

    threading.Thread(target=background_task).start()

# --- Interaction Listeners ---

@app.command("/create-ad")
def handle_create_ad(ack, command, respond):
    """Handles the /create-ad slash command."""
    ack()
    user_input = command.get('text', '')
    user_id = command.get('user_id', '')
    channel_id = command.get('channel_id', '')
    
    # Map 'respond' to a simple 'say' like interface
    def say_respond(blocks=None, text="Ad processing..."):
        respond(blocks=blocks, text=text)
        
    shared_ad_logic(user_input, user_id, channel_id, say_respond)

@app.message(re.compile(r".*create-ad.*", re.IGNORECASE))
def handle_create_ad_message(message, say):
    """Handles cases where users upload a file and type create-ad in the caption."""
    user_input = message.get('text', '')
    user_id = message.get('user', '')
    channel_id = message.get('channel', '')
    msg_files = message.get('files', [])
    shared_ad_logic(user_input, user_id, channel_id, say, msg_files=msg_files)

from image_engine import generate_fal_flux_image
import json
import re

# --- Action Listeners for the Buttons ---

@app.action(re.compile("approve_concept_.*"))
def handle_approve_button(ack, body, respond, action):
    ack()
    user_id = body["user"]["id"]
    
    # Extract the store reference: "session_id|concept_index"
    val = action.get("value", "")
    if "|" not in val:
        respond(text="❌ Concept data expired. Please run /create-ad again.")
        return
        
    session_id, index_str = val.split("|")
    index = int(index_str)
    
    # Extract the actual Channel/DM ID
    channel_id = body["channel"]["id"]
    
    # Retrieve from the global store
    session_data = CONCEPT_STORE.get(session_id)
    if not session_data or "concepts" not in session_data or index >= len(session_data["concepts"]):
        respond(text="❌ Concept data expired. Please run /create-ad again.", replace_original=False)
        return
        
    concept_json = session_data["concepts"][index]
    headline = concept_json.get("headline", "Ad")
    product_img = session_data.get("product", "bottle.png")
    style_gallery = session_data.get("style", []) # Retrieve the multi-image gallery
    
    logging.info(f"User approved concept: {headline} in channel {channel_id}")
    
    # Update the UI instantly so they know it was clicked
    respond(
        text=f"✅ <@{user_id}> approved *{headline}*!\n\n_Sending Blueprint and Image References to Fal.ai Engineer..._",
        replace_original=False
    )
    
    # Fire the Image Engine sequentially so we don't drop the webhook context
    def image_task():
        import time
        try:
            logging.info("Calling Fal.ai Architecture Engine with Moodboard Context...")
            img_bytes = generate_fal_flux_image(concept_json, product_image_path=product_img, style_image_path=style_gallery)
            
            # 1. Save the literal Output bytes
            logging.info("Saving Fal.ai render to local payload...")
            final_upload_path = 'final_fal_ad.png'
            with open(final_upload_path, 'wb') as handler:
                handler.write(img_bytes)
                
            # CRITICAL WINDOWS FIX: Small pause to ensure OS file handles are fully released
            logging.info("Waiting 2s for file system sync...")
            time.sleep(2)
            
            if not os.path.exists(final_upload_path):
                raise FileNotFoundError(f"Composited ad file missing at {final_upload_path}")

            # 2. Upload the raw image file directly to Slack the 100% foolproof way
            logging.info(f"Uploading native PNG file to Slack channel: {channel_id}")
            
            safe_post(
                channel_id, user_id,
                is_file=True,
                file_path=final_upload_path,
                comment=f"🎉 *Here is your completely autonomous Static Ad for '{headline}'!* 👇",
                title=headline
            )
                    
        except Exception as e:
            logging.error(f"Image generation failed: {e}")
            safe_post(channel_id, user_id, text=f"❌ Image generation failed: {e}")
                
    threading.Thread(target=image_task).start()


@app.action(re.compile("refine_concept_.*"))
def handle_refine_button(ack, body, client, action):
    ack()
    val = action.get("value", "")
    if "|" not in val:
        return
        
    session_id, index_str = val.split("|")
    index = int(index_str)
    
    # We retrieve the specific concept to populate the modal with context
    session_data = CONCEPT_STORE.get(session_id)
    if not session_data or "concepts" not in session_data or index >= len(session_data["concepts"]):
        return
        
    concept_json = session_data["concepts"][index]
    headline = concept_json.get("headline", f"Concept #{index+1}")
    
    # Store the channel_id inside the private metadata so the view_submission knows where to reply
    channel_id = body["channel"]["id"]
    private_metadata = f"{session_id}|{index}|{channel_id}"
    
    # Open the Modal
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "refine_concept_modal",
            "private_metadata": private_metadata,
            "title": {
                "type": "plain_text",
                "text": "Refine AI Concept"
            },
            "submit": {
                "type": "plain_text",
                "text": "Send to Claude"
            },
            "close": {
                "type": "plain_text",
                "text": "Cancel"
            },
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"You are refining: *{headline}*\n_What would you like the AI to change about this concept? (e.g., 'Make the background darker', 'Change the CTA to blue')_"
                    }
                },
                {
                    "type": "input",
                    "block_id": "feedback_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "feedback_input",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Type your feedback here..."
                        }
                    },
                    "label": {
                        "type": "plain_text",
                        "text": "Instructions for Claude"
                    }
                }
            ]
        }
    )

@app.action(re.compile("reject_concept_.*"))
def handle_reject_button(ack, body, respond):
    ack()
    respond(text="❌ Concept rejected. It has been purged from the AI queue.", replace_original=False)

@app.view("refine_concept_modal")
def handle_refine_modal_submission(ack, body, client, view, logger):
    ack()
    
    # Extract the payload
    val_dict = view["state"]["values"]["feedback_block"]["feedback_input"]
    feedback_text = val_dict.get("value", "")
    private_metadata = view.get("private_metadata", "")
    
    try:
        session_id, index_str, channel_id = private_metadata.split("|")
        index = int(index_str)
    except:
        logger.error("Failed to parse private_metadata from modal.")
        return
        
    session_data = CONCEPT_STORE.get(session_id)
    if not session_data or "concepts" not in session_data or index >= len(session_data["concepts"]):
        return
        
    original_concept = session_data["concepts"][index]
    headline = original_concept.get("headline", "Ad")
    
    # Notify user that refinement is starting
    user_id = body["user"]["id"]
    safe_post(
        channel_id, user_id,
        text=f"🔄 *Refining Concept: {headline}*\n_Sending your feedback back to Claude {feedback_text[:30]}..._"
    )
    
    # Spin up background thread to call Claude
    def refine_task():
        try:
            from claude_engine import refine_ad_concept
            from slack_ui import build_concepts_blocks
            
            logger.info("Calling Claude Refinement Engine...")
            updated_concept = refine_ad_concept(original_concept, feedback_text)
            
            if updated_concept:
                # Update the store directly by appending the new concept
                new_index = len(session_data["concepts"])
                session_data["concepts"].append(updated_concept)
                
                # Send just this updated concept back to the channel
                updated_blocks = build_concepts_blocks([updated_concept], session_id)
                for block in updated_blocks:
                    if block.get("type") == "actions":
                        for element in block["elements"]:
                            if "session_" in element.get("value", ""):
                                element["value"] = f"{session_id}|{new_index}"
                
                safe_post(
                    channel_id, user_id,
                    blocks=updated_blocks,
                    text=f"✨ Your refined concept for '{headline}' is ready!"
                )
            else:
                safe_post(channel_id, user_id, text="❌ Claude failed to refine the concept.")
        except Exception as e:
            logger.error(f"Error during refinement: {e}")
            safe_post(channel_id, user_id, text=f"❌ Error during refinement: {str(e)}")
            
    threading.Thread(target=refine_task).start()

# Start the socket mode handler
if __name__ == "__main__":
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        logging.error("SLACK_APP_TOKEN is missing! Please check your .env file.")
    else:
        logging.info("Starting Antigravity Ad Pipeline Bot via Socket Mode...")
        SocketModeHandler(app, app_token).start()
