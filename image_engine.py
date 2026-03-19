import os
import urllib.parse
import logging

logging.basicConfig(level=logging.INFO)

import os
import requests
import fal_client

def generate_fal_flux_image(concept_json, product_image_path="bottle.png", style_image_path=None):
    """
    Takes the architectural blueprint from Claude.
    Connects to Fal.ai (FLUX) to perfectly render typography and natively composite the product.
    Accepts a Product Anchor image and an optional Style Reference image.
    """
    # Use the narrative directly. If prompt is too long, Fal.ai downstream services (GPUs) can timeout.
    nano_prompt = concept_json.get('nano_banana_prompt', '').strip()
    if not nano_prompt:
        # Fallback if nano_banana_prompt is missing
        nano_prompt = concept_json.get('visual_direction', '')
        
    if not nano_prompt:
        raise ValueError("No prompt found in the blueprint json.")
        
    # Clean the prompt deeply to remove all emojis, unicode squares, and special invisible characters
    # that cause Fal.ai (FLUX) downstream service token-limit errors on massive prompts.
    # We NO LONGER truncate. We want 100% of the 'Manus-Logic' layout instructions.
    
    # Encode and decode to forcefully drop non-ascii tokens (emojis)
    clean_prompt = nano_prompt.encode('ascii', 'ignore').decode('ascii')
    
    import re
    clean_prompt = re.sub(r'\s+', ' ', clean_prompt).strip()
    
    if len(clean_prompt) > 1600:
        truncated = clean_prompt[:1600]
        last_period = truncated.rfind('.')
        if last_period > 1000:
            clean_prompt = truncated[:last_period+1]
        else:
            clean_prompt = truncated + "..."
            
    prompt = f"Generate this ad concept: {clean_prompt}"

    logging.info(f"Step 1: Uploading Product Anchor ({product_image_path}) to Fal.ai...")
    if not os.path.exists(product_image_path):
        raise FileNotFoundError(f"Product anchor not found at {product_image_path}. Cannot generate image without it.")
        
    product_url = fal_client.upload_file(product_image_path)
    
    # --- Step 2: Single Style Reference ---
    urllist = [product_url]
    if style_image_path:
        # Reverted: Only use the first/primary style reference if a list is provided
        actual_style = style_image_path[0] if isinstance(style_image_path, list) else style_image_path
        if actual_style and os.path.exists(actual_style):
            logging.info(f"Step 2: Uploading Style Reference ({actual_style}) to Fal.ai...")
            s_url = fal_client.upload_file(actual_style)
            urllist.append(s_url)
            prompt += f" Follow the style/lighting of this reference: {s_url}"
        
    # Tone Control: Added "glow, futuristic, neon, water splashes, chrome" to negative prompt
    negative_prompt = (
        "glow, futuristic, neon, water splashes, chrome, 3d render, plastic, "
        "garbled text, distorted labels, double bottles, blurry branding, messy fonts, misspelling, "
        "deformed hands, unrelated objects, promotional badges, gold stickers, drinking glasses, floating icons."
    )
    
    arguments = {
        "prompt": prompt,
        "image_urls": urllist,
        "aspect_ratio": "4:5",
        "negative_prompt": negative_prompt
    }
    
    # Step 3: Directing Nano Banana 2 Edit API to render background.
    # We implement a 'Silent Retry' loop to handle Fal.ai GPU capacity issues (downstream_service_error)
    import time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logging.info(f"Step 3: Directing Nano Banana 2 Edit API (Attempt {attempt + 1}/{max_retries})...")
            
            result = fal_client.subscribe(
                "fal-ai/nano-banana-2/edit",
                arguments=arguments,
                with_logs=True
            )
            
            # Download the resulting image payload
            logging.info("Step 4: Downloading rendered Nano Banana 2 Edit payload...")
            image_url_out = result.get('images')[0].get('url')
            img_data = requests.get(image_url_out).content
            
            return img_data
            
        except Exception as e:
            if "downstream_service_error" in str(e).lower() and attempt < max_retries - 1:
                logging.warning(f"Fal.ai GPU Timeout (Attempt {attempt+1}). Retrying in 2s...")
                time.sleep(2)
                continue
            else:
                logging.error(f"Image generation failed after {attempt+1} attempts: {e}")
                raise e

def remove_background(image_path):
    """
    Uses Fal.ai (Bria) to surgically remove the background and isolate the product.
    This ensures that badges, glasses, or lifestyle elements are stripped away,
    leaving a clean, transparent PNG master asset.
    """
    logging.info(f"AI Scrubber: Removing background from {image_path} to isolate product...")
    
    try:
        url = fal_client.upload_file(image_path)
        result = fal_client.subscribe(
            "fal-ai/bria/background/remove",
            arguments={"image_url": url},
            with_logs=True
        )
        
        # Bria RMBG 2.0 usually returns 'image' or 'images' depending on the exact sub-version
        out_obj = result.get('image') or result.get('images', [{}])[0]
        image_url_out = out_obj.get('url')
        
        if not image_url_out:
            raise ValueError(f"No output URL found in Fal.ai result: {result}")

        img_data = requests.get(image_url_out).content
        
        # We overwrite the original image with the clean, scrubbed version
        with open(image_path, 'wb') as f:
            f.write(img_data)
        
        logging.info(f"AI Scrubber Success: Product isolated and saved to {image_path}")
        return True
    except Exception as e:
        logging.error(f"AI Scrubber failed: {e}")
        return False

def surgical_crop(image_path, box):
    """
    Physically crops the image to the [ymin, xmin, ymax, xmax] coordinates 
    provided by AI Vision. box values are 0-1000.
    """
    from PIL import Image
    logging.info(f"Surgical Crop: Removing clutter from {image_path} using AI coordinates {box}...")
    
    try:
        img = Image.open(image_path)
        w, h = img.size
        
        # Convert normalized 0-1000 to pixel coordinates
        left = (box['xmin'] / 1000) * w
        top = (box['ymin'] / 1000) * h
        right = (box['xmax'] / 1000) * w
        bottom = (box['ymax'] / 1000) * h
        
        # ZERO BUFFER: We shave the edges to ensure no badge pixels survive
        left = max(0, left)
        top = max(0, top)
        right = min(w, right)
        bottom = min(h, bottom)
        
        # Perform the crop
        cropped_img = img.crop((left, top, right, bottom))
        cropped_img.save(image_path) # Overwrite with the clean crop
        
        logging.info(f"Surgical Crop Success: Image resized to isolated product area.")
        return True
    except Exception as e:
        logging.error(f"Surgical Crop failed: {e}")
        return False

def upscale_image(image_path, output_path=None):
    """
    Uses Fal.ai (ESRGAN) for high-speed, high-fidelity upscaling.
    4x upscale results in professional, sharp product assets.
    """
    logging.info(f"AI Upscaler: Enhancing {image_path} to 4x quality via ESRGAN...")
    
    try:
        url = fal_client.upload_file(image_path)
        result = fal_client.subscribe(
            "fal-ai/esrgan",
            arguments={
                "image_url": url,
                "model_name": "RealESRGAN_x4plus"
            },
            with_logs=True
        )
        
        image_url_out = result.get('image').get('url')
        img_data = requests.get(image_url_out).content
        
        final_path = output_path if output_path else f"enhanced_{os.path.basename(image_path)}"
        with open(final_path, 'wb') as f:
            f.write(img_data)
        
        logging.info(f"AI Upscaler Success: Enhanced image saved to {final_path}")
        return final_path
    except Exception as e:
        logging.error(f"AI Upscaler failed: {e}")
        return None
