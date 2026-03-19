import requests
from bs4 import BeautifulSoup
import logging
import re

logging.basicConfig(level=logging.INFO)

def extract_urls(text):
    """
    Finds and cleans URLs in the user's Slack message.
    Strips Slack brackets < > and trailing punctuation like underscores or periods.
    """
    # Slack often sends URLs in this format: <https://example.com>
    url_pattern = re.compile(r'https?://[^\s>]+')
    found_urls = url_pattern.findall(text)
    
    # Strip common trailing characters that break URLs
    clean_urls = [url.rstrip('._,>!?') for url in found_urls]
    return clean_urls

def scrape_product_url(url):
    """
    Visits a product URL and uses BeautifulSoup to extract all meaningful text strings 
    (product descriptions, prices, materials, reviews) while stripping out HTML/CSS.
    """
    try:
        logging.info(f"Attempting to scrape URL: {url}")
        
        # Add headers to mimic a real browser so Shopify/React sites don't block the request
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Adding verify=False prevents SSL failures on local Windows environments when scraping
        response = requests.get(url, headers=headers, timeout=15, verify=False)
        response.raise_for_status()

        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')

        # Strip all <script> and <style> tags, they are useless to Claude
        for script in soup(["script", "style", "nav", "footer"]):
            script.extract()
            
        # Get all text
        text = soup.get_text(separator=' ', strip=True)
        
        # Clean up excessive newlines or weird spacing
        clean_text = re.sub(r'\s+', ' ', text).strip()
        
        logging.info("Successfully extracted clean text from product URL.")
        
        # Truncate to save tokens (e.g. max 15,000 chars for Claude)
        return clean_text[:15000]

    except Exception as e:
        logging.error(f"Failed to scrape URL {url}. Error: {e}")
        return "ERROR: Could not scrape website data."


def extract_shopify_original_image(url):
    """
    Detects if a URL is a Shopify product and attempts to extract the 
    highest-resolution source image from the public .json endpoint.
    """
    try:
        # 1. Clean the URL and append .json
        clean_url = url.split('?')[0].rstrip('/')
        json_url = f"{clean_url}.json"
        
        logging.info(f"Shopify Detect: Attempting to pull master image from {json_url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(json_url, headers=headers, timeout=10, verify=False)
        if response.status_code != 200:
            return None
            
        data = response.json()
        
        # Shopify JSON structure: {"product": {"images": [{"src": "..."}]}}
        product = data.get('product', {})
        images = product.get('images', [])
        
        if images:
            brand_name = product.get('vendor', 'Unknown Brand')
            logging.info(f"Shopify Scraper: Found {len(images)} images for {brand_name}. Passing the full gallery for AI selection.")
            return {"images": images, "brand_name": brand_name}
            
        return None
    except Exception as e:
        logging.warning(f"Shopify JSON extraction failed for {url}: {e}. Trying Meta-tag fallback...")
        try:
            # Fallback 2: Direct HTML Scraping for og:image
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10, verify=False)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Try to find brand name in site_name or title
                site_name = soup.find("meta", property="og:site_name")
                brand_name = site_name.get("content") if site_name else "Unknown Brand"
                if brand_name == "Unknown Brand":
                    title = soup.find("title")
                    if title:
                        brand_name = title.text.split('-')[0].split('|')[0].strip()

                # Try OpenGraph image first
                og_image = soup.find("meta", property="og:image")
                if og_image:
                    master_url = og_image.get("content")
                    logging.info(f"Shopify Meta-Found (OG): {master_url} | Brand: {brand_name}")
                    return {"image_url": master_url, "brand_name": brand_name}
                
                # Try raw img tags as last resort
                img_tag = soup.find("img", {"src": re.compile(r'products')})
                if img_tag:
                    master_url = img_tag.get("src")
                    if master_url.startswith('//'):
                        master_url = 'https:' + master_url
                    logging.info(f"Shopify Meta-Found (Raw): {master_url} | Brand: {brand_name}")
                    return {"image_url": master_url, "brand_name": brand_name}
                    
        except Exception as e2:
            logging.error(f"Total scraper failure for {url}: {e2}")
        return None



BRAND_DOC_IDS = [
    "1c1oF4JUqDu4EQ-MzGqO63L7PQHk_f084", # Doc 1
    "1KnbiurBd4Pra5VbHoUo_3iEC0sEtF9hz", # Doc 2
    "1L9PUzOTrtD3jy9OE26J4hzO6XnYkifkQ"  # Doc 3
]

def get_brand_guidelines():
    """
    Downloads the pure text content of the 3 specified public Google Docs using the /export URL.
    This bypasses the need for a complex Google Cloud Service Account.
    """
    combined_context = ""
    logging.info("Fetching Brand Guidelines from the 3 Google Docs...")
    
    for doc_id in BRAND_DOC_IDS:
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
        try:
            response = requests.get(export_url, timeout=10)
            response.raise_for_status()
            combined_context += f"\n\n--- DOCUMENT END ---\n\n{response.text}"
            logging.info(f"Successfully downloaded Google Doc ID: {doc_id}")
        except Exception as e:
            logging.error(f"Failed to download Google Doc ID {doc_id}. Error: {e}")
            combined_context += f"\n\n[Failed to load document: {doc_id}]"
            
    # Truncate to maximum 20,000 characters to save Claude tokens, but keep enough for deep context.
    return combined_context[:20000]


# --- Local Testing ---
if __name__ == "__main__":
    test_url = "https://example.com"
    print("Scraping testing URL...")
    result = scrape_product_url(test_url)
    print(f"Scrape result preview (first 200 chars): {result[:200]}")
