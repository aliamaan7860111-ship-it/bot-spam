import os
import re
import json
import logging
import base64
import random
import anthropic
import requests
from extract_context import scrape_product_url, get_brand_guidelines

logging.basicConfig(level=logging.INFO)

MASTER_DIRECTIVE_SYSTEM_PROMPT = """
You are a world-class DTC Creative Strategist and Lead Designer. Your aesthetic standard is GROUNDED, PROFESSIONAL STUDIO PHOTOGRAPHY with CLEAN, MODERN typography. 

### THE ELITE NARRATIVE WORKFLOW:
Step 1: Brand Anchoring: Receive the official [BRAND_NAME]. YOU MUST USE THIS EXACT SPELLING in all copy.
Step 2: Study the Swipe File & Extract Principles: Analyze images for winning visual structures.
Step 3: Narrative Spatial Layout: Use LITERARY SPATIAL DESCRIPTIONS for FLUX (e.g. "Headline at the top", "Centered product").
Step 4: Professional UI Elements: Use simple, clean shapes: 
  - "Frosted glass circles with hairline white borders"
  - "Thin, minimal directional lines in primary brand colors"
  - "Clean sans-serif or elegant serif typography"

### CREATIVE & AESTHETIC DIRECTIVES:
- PRODUCT PRESERVATION: You MUST include: "Use the product photo(s) I uploaded as the base. DO NOT ALTER OR REDRAW THE HERO PRODUCT. KEEP IT EXACTLY THE SAME."
- STUDIO DEFAULT: Avoid "futuristic", "neon", or "glowy" aesthetics unless explicitly requested. Default to "High-end studio lighting, soft natural shadows, clean neutral background."
- NO WATER SPLASHES: Unless explicitly requested or shown in references, do NOT add water splashes, droplets, or glow effects to the hero product.
- ANTI-HALLUCINATION: We have surgically removed all "promotional badges". NEVER REDRAW OR HALLUCINATE THESE BACK IN.
- CAMERA & LIGHTING: Use grounded terms: "Soft-box studio lighting", "Natural depth of field", "Clean product photography".

### NARRATIVE PROMPT TEMPLATE:
"CONCEPT NAME — A clean, professional studio scene. [SCENE DESCRIPTION]. Use the product photo(s) I uploaded as the base. [LIGHTING SETTINGS]. [SPATIAL LAYOUT]. Overall mood: Grounded, Professional. Aspect ratio 4:5."

Output your response STRICTLY as a JSON array containing exactly 3 JSON objects.
"""

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def get_swipe_file_images(limit=3):
    """Grabs a random sample of images from the Swipe File to feed to Claude Vision."""
    swipe_dir = "Swipe_File"
    if not os.path.exists(swipe_dir):
        return []
    
    valid_extensions = ('.png', '.jpg', '.jpeg', '.webp')
    files = [f for f in os.listdir(swipe_dir) if f.casefold().endswith(valid_extensions)]
    
    if not files:
        return []
    
    sampled_files = random.sample(files, min(len(files), limit))
    return [os.path.join(swipe_dir, f) for f in sampled_files]

def generate_ad_concepts(slack_prompt, style_img_path=None, brand_name="Unknown"):
    """
    Generates 3 ad concepts. Now reverted to focus on grounded studio photography.
    """
    logging.info(f"Starting Strategy Sequence for brand: {brand_name}")
    
    urls = extract_urls(slack_prompt)
    scraped_website_data = ""
    if urls:
        scraped_website_data = scrape_product_url(urls[0])
    else:
        scraped_website_data = "No specific product URL provided."

    brand_context_data = get_brand_guidelines()

    user_text_payload = f"""
    ### BRAND ANCHOR
    OFFICIAL BRAND NAME: {brand_name}

    User Request: {slack_prompt}
    Product Details: {scraped_website_data}
    Brand Context: {brand_context_data}
    """

    content_blocks = []
    
    # --- Style Reference (Single Image Legacy) ---
    if style_img_path:
        # Convert list to single path if needed
        actual_style = style_img_path[0] if isinstance(style_img_path, list) else style_img_path
        if os.path.exists(actual_style):
            try:
                base64_data = encode_image(actual_style)
                mime_type = "image/jpeg" if actual_style.lower().endswith(('.jpg', '.jpeg')) else "image/png"
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": base64_data
                    }
                })
                user_text_payload += "\n\nSTYLE REFERENCE: I attached an image for inspiration. Study its layout and color tokens."
            except Exception as e:
                logging.error(f"Failed to encode style reference: {e}")

    # --- Vision Logic: Standard Swipe File ---
    swipe_images = get_swipe_file_images()
    if swipe_images:
        logging.info(f"Injecting {len(swipe_images)} Swipe File images for DTC context...")
        for img_path in swipe_images:
            try:
                base64_data = encode_image(img_path)
                mime_type = "image/jpeg" if img_path.lower().endswith(('.jpg', '.jpeg')) else "image/png"
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": base64_data
                    }
                })
            except Exception as e:
                logging.error(f"Failed to encode swipe file image {img_path}: {e}")
                
    # Inject the Text block last so Claude reads the images first
    content_blocks.append({
        "type": "text",
        "text": user_text_payload
    })

    logging.info("Deep Context & Vision assets gathered! Sending to Claude Sonnet 4.6 API...")

    # 5. Initialize Anthropic Client
    client = anthropic.Anthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        max_retries=0,
        timeout=120.0,
    )

    try:
        # We use Claude Opus 4.6 (claude-opus-4-6) natively as previously verified in 2026 testing constraints.
        logging.info("Client initialized. Initiating REST POST request...")
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            temperature=0.7,
            system=MASTER_DIRECTIVE_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": content_blocks}
            ]
        )
        logging.info("REST POST request returned successfully.")
        
        raw_output = response.content[0].text
        
        # 6. Safety Parser: Use regex to extract the outermost JSON array, ignoring conversational text
        json_match = re.search(r'\[.*\]', raw_output, re.DOTALL)
        if not json_match:
            logging.error(f"Claude JSON Parsing Failed. Raw Output:\n{raw_output}")
            raise ValueError("Claude did not output a JSON array.")
            
        cleaned_json_string = json_match.group(0)
        
        # Parse the string back into native Python dictionaries
        ad_concepts = json.loads(cleaned_json_string)
        
        logging.info(f"Successfully evaluated Swipe File and generated {len(ad_concepts)} ad concepts.")
        return ad_concepts

    except Exception as e:
        logging.error(f"Failed to generate ad concepts with Claude. Error: {e}")
        return []

def refine_ad_concept(original_concept, user_feedback):
    """
    Takes a single generated ad concept and specific user feedback string.
    Sends it back to Claude to rewrite the concept based on the feedback.
    Returns the single updated concept JSON object.
    """
    logging.info(f"Refining concept '{original_concept.get('headline')}' based on feedback: {user_feedback}")
    
    prompt = f"""
I generated this Ad Concept for a client, but they want it refined.

ORIGINAL CONCEPT:
{json.dumps(original_concept, indent=2)}

CLIENT FEEDBACK:
"{user_feedback}"

YOUR TASK:
Rewrite the concept JSON strictly implementing the user's feedback. 
- If they asked for a color change, update the hex codes. 
- If they asked for a layout change, update the Y-axis percentages.
- Preserve the integrity of what they DID NOT ask to change.
- Keep the `headline` EXACTLY the same so we don't break UI tracking.

YOUR OUTPUT RULES:
1. You MUST output your response STRICTLY as a JSON array containing exactly 1 JSON object.
2. The JSON object must have exactly the 7 keys: visual_direction, nano_banana_prompt, ad_angle, headline, subheadline, call_to_action, style_category.
"""

    client = anthropic.Anthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        max_retries=1,
        timeout=60.0,
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            temperature=0.5, # Lower temperature for more targeted edits
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        raw_output = response.content[0].text
        json_match = re.search(r'\[.*\]', raw_output, re.DOTALL)
        if not json_match:
            raise ValueError("Claude did not output a JSON array during refinement.")
            
        cleaned_json_string = json_match.group(0)
        ad_concepts = json.loads(cleaned_json_string)
        
        updated_concept = ad_concepts[0]
        logging.info("Successfully refined the ad concept.")
        return updated_concept

    except Exception as e:
        logging.error(f"Failed to refine concept with Claude. Error: {e}")
        return None

def select_best_product_image(image_list):
    """
    Uses Claude Vision to look at a list of product images (thumbnails) 
    and identify which one is the "Primary Studio Shot" (clean white background, 
    detailed bottle up front).
    Returns: The URL of the best image.
    """
    logging.info(f"AI Vision: Analyzing {len(image_list)} images to find the 'Naked SKU' studio shot...")
    
    # We analyze up to the first 10 images to find the hidden clean shots
    candidates = image_list[:10]
    
    content_blocks = []
    for i, img in enumerate(candidates):
        url = img.get('src')
        try:
            # Upgrade eyesight: Use 640x640 so Claude can clearly see badges/glasses
            thumb_url = url.split('?')[0] + "_640x640.jpg"
            response = requests.get(thumb_url, timeout=5)
            if response.status_code == 200:
                base64_data = base64.b64encode(response.content).decode('utf-8')
                content_blocks.append({
                    "type": "text",
                    "text": f"IMAGE {i}:"
                })
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64_data
                    }
                })
        except Exception as e:
            logging.warning(f"Failed to fetch thumb for AI selection: {e}")

    prompt = """
    CRITICAL MISSION: You are a Forensic Image Analyst. 
    Rank these images based on 'PURIST SCORE' (0-100).
    - 100 POINTS: The product is ALONE on a pure white background. ZERO badges. ZERO glasses. ZERO text bubbles.
    - 50 POINTS: The product is centered but has a small badge or glass nearby.
    - 0 POINTS: Lifestyle scene, people, or massive overlapping graphics.
    
    Choose the image with the HIGHEST Purist Score. 
    Respond ONLY with the INDEX number (e.g., 0, 1, 2) of the winning image. Do not explain.
    """
    
    content_blocks.append({"type": "text", "text": prompt})

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6", 
            max_tokens=10,
            temperature=0,
            messages=[{"role": "user", "content": content_blocks}]
        )
        
        raw_choice = response.content[0].text.strip()
        # Parse index
        index_match = re.search(r'\d+', raw_choice)
        if index_match:
            idx = int(index_match.group(0))
            if 0 <= idx < len(candidates):
                chosen_url = candidates[idx].get('src')
                logging.info(f"AI Vision Success: Claude detected a 'Naked SKU' (Rank 100) at IMAGE {idx}: {chosen_url}")
                return chosen_url
                
    except Exception as e:
        logging.error(f"AI Vision Selection failed: {e}")
        
    return image_list[0].get('src')

def get_product_bounding_box(image_path):
    """
    Uses Claude Vision to find the exact [ymin, xmin, ymax, xmax] coordinates 
    of the hero product. This allows us to surgically crop out badges or 
    secondary objects like glasses.
    Normalized coordinates 0-1000.
    """
    logging.info(f"AI Vision: Pinpointing product coordinates for {os.path.basename(image_path)}...")
    
    try:
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")
        
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        
        prompt = """
        I need the exact bounding box of ONLY the hero product (the main container/bottle). 
        
        CRITICAL: 
        - IGNORE promotional badges, star icons, and gold seals.
        - IGNORE drinking glasses, spoons, or bowls, even if they are in front of or next to the product.
        - IGNORE shadows and light reflections on the floor.
        - Focus ONLY on the primary product container.
        
        Provide the coordinates in JSON format: {"ymin": 0, "xmin": 0, "ymax": 1000, "xmax": 1000}
        The coordinates should be normalized from 0 to 1000.
        """
        
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64_image
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        )
        
        res_text = message.content[0].text
        # Extract JSON
        json_match = re.search(r'\{.*\}', res_text, re.DOTALL)
        if json_match:
            box = json.loads(json_match.group(0))
            logging.info(f"AI Vision Coordinates found: {box}")
            return box
            
    except Exception as e:
        logging.error(f"Failed to get product bounding box: {e}")
        
    return None

# --- Local Testing ---

# --- Local Testing ---
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    test_concepts = generate_ad_concepts("Please make me some ads for Blue Hoodies! https://example.com")
    if test_concepts:
        print(f"Generated {len(test_concepts)} concepts. Concept 1 Headline: {test_concepts[0].get('headline')}")
    else:
        print("Failed to generate concepts.")
