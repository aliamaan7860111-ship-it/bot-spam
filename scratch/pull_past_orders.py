import os
import sys
import asyncio
import httpx
from pathlib import Path

# Resolve project root and load env
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

# Load local env (which contains Notion API keys)
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from execution.shopify_webhook_server import (
    parse_shopify_order,
    build_notion_properties,
    create_notion_order,
    find_order_by_id,
)

# Brand configuration mappings
BRANDS = {
    "AMARA": {"prefix": "AM", "env_suffix": "AMARA"},
    "AM": {"prefix": "AM", "env_suffix": "AMARA"},
    
    "PELVINI": {"prefix": "PV", "env_suffix": "PELVINI"},
    "PV": {"prefix": "PV", "env_suffix": "PELVINI"},
    
    "ELARA": {"prefix": "PT", "env_suffix": "ELARA"},
    "PT": {"prefix": "PT", "env_suffix": "ELARA"},
    
    "DIALO": {"prefix": "Di", "env_suffix": "DIALO"},
    "DI": {"prefix": "Di", "env_suffix": "DIALO"},
    
    "LUNE": {"prefix": "LU", "env_suffix": "LUNE"},
    "LU": {"prefix": "LU", "env_suffix": "LUNE"},
    
    "VIREX": {"prefix": "VX", "env_suffix": "VIREX"},
    "VX": {"prefix": "VX", "env_suffix": "VIREX"},
    
    "RIMAL": {"prefix": "R", "env_suffix": "RIMAL"},
    "R": {"prefix": "R", "env_suffix": "RIMAL"},
    
    "ORLENTO": {"prefix": "O", "env_suffix": "ORLENTO"},
    "O": {"prefix": "O", "env_suffix": "ORLENTO"},

    "VIRESTA": {"prefix": "VS", "env_suffix": "VIRESTA"},
    "VS": {"prefix": "VS", "env_suffix": "VIRESTA"},

    "DIWAN": {"prefix": "DX", "env_suffix": "DIWAN"},
    "DX": {"prefix": "DX", "env_suffix": "DIWAN"},
}

# Command line selection of brand (e.g. python pull_past_orders.py AM)
brand_arg = sys.argv[1].upper().strip() if len(sys.argv) > 1 else "ORLENTO"

if brand_arg not in BRANDS:
    print(f"Error: Brand '{brand_arg}' is not configured.")
    print(f"Supported brands: {', '.join(sorted(set(k for k in BRANDS.keys() if len(k) > 2)))}")
    sys.exit(1)

brand_info = BRANDS[brand_arg]
BRAND_PREFIX = brand_info["prefix"]
env_suffix = brand_info["env_suffix"]

SHOPIFY_DOMAIN = os.getenv(f"SHOPIFY_DOMAIN_{env_suffix}")
SHOPIFY_ACCESS_TOKEN = os.getenv(f"SHOPIFY_TOKEN_{env_suffix}")

if not SHOPIFY_DOMAIN or not SHOPIFY_ACCESS_TOKEN:
    print(f"Error: Shopify credentials for {env_suffix} not found in .env file.")
    sys.exit(1)

async def main():
    print(f"Connecting to Shopify store: {SHOPIFY_DOMAIN}...")
    shopify_url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-04/orders.json"
    shopify_headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    
    # Fetch limit from env if defined, otherwise use default
    limit_val = os.getenv(f"SHOPIFY_BACKFILL_LIMIT_{env_suffix}")
    if not limit_val:
        limit_val = os.getenv("SHOPIFY_BACKFILL_LIMIT")
    
    if limit_val:
        try:
            limit = int(limit_val.strip())
        except ValueError:
            limit = 10 if BRAND_PREFIX == "O" else 50
    else:
        limit = 10 if BRAND_PREFIX == "O" else 50
        
    params = {
        "limit": limit,
        "status": "any"
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(shopify_url, headers=shopify_headers, params=params)
            resp.raise_for_status()
            orders = resp.json().get("orders", [])
        except Exception as e:
            print(f"Error fetching orders from Shopify: {e}")
            if 'resp' in locals() and resp is not None:
                print(f"Details: {resp.text}")
            return
            
        print(f"Successfully retrieved {len(orders)} orders from Shopify.\n")
        
        for idx, order in enumerate(orders, 1):
            raw_name = order.get("name")
            order_id_raw = order.get("id")
            
            # Skip cancelled or voided orders
            if order.get("cancelled_at") or order.get("financial_status") == "voided":
                print(f"[{idx}] Shopify Order Name: {raw_name} (ID: {order_id_raw}) -> [SKIP] Cancelled or Voided.")
                continue
                
            print(f"[{idx}] Shopify Order Name: {raw_name} (ID: {order_id_raw})")
            
            # 1. Parse and format order data using the Orlento brand prefix rules
            try:
                parsed_data = parse_shopify_order(order, BRAND_PREFIX)
            except Exception as pe:
                print(f"  -> Failed to parse order data: {pe}")
                continue
                
            formatted_order_id = parsed_data["order_id"]
            customer_name = parsed_data["customer_name"]
            total = parsed_data["total"]
            
            print(f"  -> Formatted CRM Order ID: {formatted_order_id}")
            print(f"  -> Order Total: {total}")


            
            # 2. Check if the formatted Order ID already exists in the Notion CRM
            exists = await find_order_by_id(client, formatted_order_id)
            if exists:
                print(f"  -> [SKIP] Order {formatted_order_id} already exists in the CRM database.\n")
                continue
                
            # 3. Create properties and insert to Notion
            print(f"  -> [INSERT] Order {formatted_order_id} is new. Pushing to Notion...")
            properties = build_notion_properties(parsed_data)
            success = await create_notion_order(client, properties)
            if success:
                print(f"  -> [SUCCESS] Order {formatted_order_id} added successfully.\n")
            else:
                print(f"  -> [ERROR] Failed to write Order {formatted_order_id} to Notion.\n")

if __name__ == "__main__":
    asyncio.run(main())
