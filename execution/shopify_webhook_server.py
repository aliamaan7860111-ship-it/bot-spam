import os
import sys
import json
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import httpx
from dotenv import load_dotenv

# Resolve project root and load env
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / ".tmp" / "shopify_webhook.log", encoding="utf-8")
    ]
)
log = logging.getLogger("shopify_webhook")

# Centralized error reporting. MUST come AFTER basicConfig — installing a root
# handler first makes basicConfig a no-op and silences all stdout/file logging.
import error_reporter
import order_dedup
error_reporter.install("shopify-webhook", host="gcp-vm")

# Configurations
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "").strip()
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").strip()
# Default to the ALL ORDERS data source ID we queried
DATA_SOURCE_ID = os.getenv("NOTION_DATA_SOURCE_ID", "2ebc320e-ba59-80b9-b23e-000beb542ac8").strip()
PORT = int(os.getenv("SHOPIFY_WEBHOOK_PORT", "8085"))

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

# Bounded memory cache to prevent rapid concurrent duplicates of the exact same event
_recently_processed_order_ids = set()

# Ensure temp directory exists
(PROJECT_ROOT / ".tmp").mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Notion Database API Queries
# ---------------------------------------------------------------------------

async def find_order_by_id(client: httpx.AsyncClient, order_id: str) -> bool:
    """Query Notion to check if an order with the same ORDER ID already exists."""
    url = f"{NOTION_API_BASE}/data_sources/{DATA_SOURCE_ID}/query"
    payload = {
        "filter": {
            "property": "ORDER ID",
            "title": {"equals": order_id.strip()}
        },
        "page_size": 1
    }
    try:
        resp = await client.post(url, headers=HEADERS, json=payload, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return len(results) > 0
    except Exception as e:
        log.error(f"Error checking order ID {order_id} in Notion: {e}")
        # Return False to avoid blocking order creation on Notion query errors,
        # but in a critical production system we might want to fail-safe.
        return False

async def find_recent_duplicate(client: httpx.AsyncClient, customer_name: str, ip_address: str) -> bool:
    """
    Check if an order with the same customer name and IP address was created
    in the database within the last 5 minutes.
    """
    if not customer_name or not ip_address:
        return False
        
    # 5 minutes ago in ISO format
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    url = f"{NOTION_API_BASE}/data_sources/{DATA_SOURCE_ID}/query"
    payload = {
        "filter": {
            "and": [
                {
                    "property": "CUSTOMER NAME",
                    "rich_text": {"equals": customer_name.strip()}
                },
                {
                    "property": "IP ADDRESS",
                    "rich_text": {"equals": ip_address.strip()}
                },
                {
                    "timestamp": "created_time",
                    "created_time": {"on_or_after": cutoff_iso}
                }
            ]
        },
        "page_size": 1
    }
    try:
        resp = await client.post(url, headers=HEADERS, json=payload, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return len(results) > 0
    except Exception as e:
        log.error(f"Error checking rapid duplicates for {customer_name} / {ip_address} in Notion: {e}")
        return False

async def create_notion_order(client: httpx.AsyncClient, properties: dict) -> bool:
    """Insert a new page into the Notion data source."""
    url = f"{NOTION_API_BASE}/pages"
    payload = {
        "parent": {
            "type": "data_source_id",
            "data_source_id": DATA_SOURCE_ID
        },
        "properties": properties
    }
    try:
        resp = await client.post(url, headers=HEADERS, json=payload, timeout=20)
        resp.raise_for_status()
        log.info(f"Successfully created order page in Notion: {resp.json().get('id')}")
        return True
    except Exception as e:
        log.error(f"Failed to create order page in Notion: {e}")
        if hasattr(e, 'response') and e.response:
            log.error(f"Notion response error details: {e.response.text}")
        return False

# ---------------------------------------------------------------------------
# Request Parser and Mapper
# ---------------------------------------------------------------------------

# Store prefix mappings based on database formats
DOMAIN_TO_PREFIX = {
    "amara": "AM",
    "ghu1xv-x0": "AM",       # Amara
    "virex": "VX",
    "3wawhe-zf": "VX",       # Virex
    "chronova": "VX",        # Chronova (Virex)
    "pelvini": "PV",
    "rngttp-0k": "PV",       # Pelvini
    "elara": "PT",
    "vner5g-2p": "PT",       # Elara (old store, retired)
    "njya1e-uz": "E",        # Elara (new store, E-prefix)
    "rimal": "R",
    "1jrhmy-ep": "R",        # Rimal
    "orlento": "O",
    "wv0sxe-me": "O",        # Orlento
    "dialo": "Di",
    "tr00cx-kz": "Di",       # Dialo
    "lune": "LU",
    "xe6vwe-4q": "LU",       # Lune
    "viresta": "VS",
    "r0h0yn-ku": "VS",        # Viresta
    "diwan": "DX",
    "wq2usc-du": "DX",        # Diwan
    "velix": "VL",
    "g1ynuh-q0": "VL"         # Velix
}

def get_store_prefix(path: str, headers: dict) -> str:
    """Determine the store prefix from query params (store=XX) or shop domain headers."""
    # 1. Query Param check
    if "?" in path:
        query = path.split("?", 1)[1]
        for pair in query.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                if k.lower() == "store":
                    return v.upper().strip()
    
    # 2. Domain header check
    domain = headers.get("x-shopify-shop-domain", "").lower()
    if domain:
        for keyword, prefix in DOMAIN_TO_PREFIX.items():
            if keyword in domain:
                return prefix
                
    # Fallback default
    return "PT"

# Formatting rules for each store to strip specific prefix/suffix and map to target CRM format.
BRAND_FORMATTING = {
    "AM": {
        "strip_prefixes": ["AM"],
        "strip_suffixes": ["777"],
        "crm_prefix": "AM"
    },
    "O": {
        "strip_prefixes": ["O"],
        "strip_suffixes": [],
        "crm_prefix": "O"
    },
    "VX": {
        "strip_prefixes": ["V", "C"],
        "strip_suffixes": ["433"],
        "crm_prefix": "C"
    },
    "R": {
        "strip_prefixes": ["RM"],
        "strip_suffixes": ["6"],
        "crm_prefix": "R"
    },
    "PT": {
        "strip_prefixes": ["SHD"],
        "strip_suffixes": ["767"],
        "crm_prefix": "PT"
    },
    "PV": {
        "strip_prefixes": ["PV23"],
        "strip_suffixes": ["232"],
        "crm_prefix": "PV"
    },
    "Di": {
        "strip_prefixes": ["Di"],
        "strip_suffixes": [],
        "crm_prefix": "Di"
    },
    "LU": {
        "strip_prefixes": ["LN7"],
        "strip_suffixes": ["76"],
        "crm_prefix": "LU"
    },
    "VS": {
        "strip_prefixes": ["7"],
        "strip_suffixes": ["76"],
        "crm_prefix": "VS"
    },
    "DX": {
        "strip_prefixes": ["D"],
        "strip_suffixes": ["28"],
        "crm_prefix": "DX"
    },
    "VL": {
        "strip_prefixes": ["V1"],
        "strip_suffixes": [],
        "crm_prefix": "VL"
    },
    "E": {  # Elara new store: "#E12347" -> strip "E" + trailing "7" -> "E1234"
        "strip_prefixes": ["E"],
        "strip_suffixes": ["7"],
        "crm_prefix": "E"
    }
}

def parse_shopify_order(payload: dict, prefix: str) -> dict:
    """Extract and map relevant fields from Shopify order JSON payload and apply store prefix."""
    raw_name = str(payload.get("name") or payload.get("id") or "").strip()
    
    # Format order ID according to CRM requirement: prefix + order_number (e.g. AM1234)
    clean_name = raw_name.lstrip('#').strip()
    
    # Apply store-specific formatting rules (strip prefixes/suffixes and apply target CRM prefix)
    cfg = BRAND_FORMATTING.get(prefix)
    if cfg:
        crm_prefix = cfg["crm_prefix"]
        
        # 1. Strip prefix if present (case-insensitive)
        for pfx in cfg["strip_prefixes"]:
            if clean_name.upper().startswith(pfx.upper()):
                clean_name = clean_name[len(pfx):].strip()
                break
                
        # 2. Strip suffix if present (case-insensitive)
        for sfx in cfg["strip_suffixes"]:
            if clean_name.upper().endswith(sfx.upper()):
                clean_name = clean_name[:-len(sfx)].strip()
                break
                
        order_id = f"{crm_prefix}{clean_name}"
    else:
        # Fallback if prefix is not configured
        if clean_name.upper().startswith(prefix.upper()):
            order_id = clean_name
        else:
            order_id = f"{prefix}{clean_name}"


    
    # Customer Info
    customer = payload.get("customer") or {}
    shipping = payload.get("shipping_address") or {}
    billing = payload.get("billing_address") or {}
    
    customer_name = (
        shipping.get("name") or 
        f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip() or
        billing.get("name") or 
        "Unknown Customer"
    ).strip()
    
    phone = (
        shipping.get("phone") or 
        customer.get("phone") or 
        billing.get("phone") or 
        ""
    ).strip()
    
    email = (
        customer.get("email") or 
        payload.get("email") or 
        ""
    ).strip()
    
    # Address details
    addr_parts = [
        shipping.get("address1") or billing.get("address1") or "",
        shipping.get("address2") or billing.get("address2") or "",
        shipping.get("city") or billing.get("city") or "",
        shipping.get("province") or billing.get("province") or "",
        shipping.get("country") or billing.get("country") or "",
        shipping.get("zip") or billing.get("zip") or ""
    ]
    full_address = ", ".join(p.strip() for p in addr_parts if p.strip())
    
    # Line Items formatting: "Product A x 2, Product B x 1"
    items = payload.get("line_items") or []
    item_parts = []
    for item in items:
        title = item.get("title") or "Unknown Product"
        qty = item.get("quantity") or 1
        item_parts.append(f"{title} x {qty}")
    item_qty_str = ", ".join(item_parts)
    
    # Totals
    total_price = payload.get("total_price") or "0.00"
    currency = payload.get("currency") or "AED"
    total_str = f"{total_price} {currency}"
    
    # IP & Source
    ip_address = str(payload.get("browser_ip") or "").strip()
    source_url = str(payload.get("order_status_url") or "").strip()
    created_at = payload.get("created_at") or datetime.now(timezone.utc).isoformat()
    
    return {
        "order_id": order_id,
        "customer_name": customer_name,
        "phone": phone,
        "email": email,
        "full_address": full_address,
        "item_qty": item_qty_str,
        "total": total_str,
        "total_price": total_price,
        "ip_address": ip_address,
        "source_url": source_url,
        "created_at": created_at
    }

def build_notion_properties(data: dict) -> dict:
    """Build Notion API properties object from parsed order data."""
    now_iso = datetime.now(timezone.utc).isoformat()
    
    try:
        total_val = float(data.get("total_price") or "0.00")
    except ValueError:
        total_val = 0.0

    props = {
        "ORDER ID": {
            "title": [{"text": {"content": data["order_id"]}}]
        },
        "CUSTOMER NAME": {
            "rich_text": [{"text": {"content": data["customer_name"]}}]
        },
        "PHONE": {
            "rich_text": [{"text": {"content": data["phone"]}}]
        },
        "EMAIL": {
            "rich_text": [{"text": {"content": data["email"]}}]
        },
        "FULL ADDRESS": {
            "rich_text": [{"text": {"content": data["full_address"]}}]
        },
        "ITEM | QTY": {
            "rich_text": [{"text": {"content": data["item_qty"]}}]
        },
        "TOTAL ": {
            "number": total_val
        },
        "IP ADDRESS": {
            "rich_text": [{"text": {"content": data["ip_address"]}}]
        },
        "ORDER SOURCE URL": {
            "rich_text": [{"text": {"content": data["source_url"]}}]
        },
        "PLATFORM SOURCE": {
            "select": {"name": "Shopify"}
        },
        "ORDER STATUS": {
            "select": {"name": "NEW"}
        },
        "Last Update": {
            "date": {"start": now_iso}
        }
    }
    
    # Note: If the database CREATED property is a system created_time property,
    # we don't write it (Notion sets it automatically). If it's a date property, we could.
    # We will let Notion handle the created_time system CREATED property automatically.
    
    return props

# ---------------------------------------------------------------------------
# Server Request Handler
# ---------------------------------------------------------------------------

async def process_shopify_webhook(http_client: httpx.AsyncClient, payload: dict, prefix: str) -> tuple[int, str]:
    """Process Shopify webhook data: parse, deduplicate, and insert to Notion."""
    try:
        data = parse_shopify_order(payload, prefix)
    except Exception as e:
        log.error(f"Failed to parse Shopify order payload: {e}")
        return 400, "Bad Request: Failed to parse order data"

    order_id = data["order_id"]
    customer_name = data["customer_name"]
    ip_address = data["ip_address"]
    
    log.info(f"Processing webhook for Order ID: {order_id} (Customer: {customer_name}, IP: {ip_address})")

    # 1. Atomic claim — the durable guard. Only ONE caller (this webhook or any
    #    backfill loop) can claim a given order_id; concurrent claims lose and skip.
    if not order_dedup.claim(order_id):
        log.info(f"Order {order_id} already claimed — skipping (duplicate).")
        return 200, "Duplicate Order (already claimed)"

    # 2. Guard against re-adding orders that predate the dedup DB (already in Notion).
    try:
        id_exists = await find_order_by_id(http_client, order_id)
    except Exception:
        order_dedup.release(order_id)  # couldn't verify — release so it can retry
        raise
    if id_exists:
        log.info(f"Order {order_id} already in Notion (pre-existing). Skipping insert.")
        return 200, "Duplicate Order (Notion ID check)"

    # 3. Insert to Notion database
    properties = build_notion_properties(data)
    success = await create_notion_order(http_client, properties)

    if success:
        return 200, "Order processed and sent to Notion successfully"
    else:
        order_dedup.release(order_id)  # failed insert -> allow a later retry
        return 500, "Internal Server Error: Failed to write to Notion"

async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, http_client: httpx.AsyncClient):
    """Low-level socket connection handler for incoming HTTP requests."""
    try:
        header_bytes = bytearray()
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
            header_bytes.extend(chunk)
            if b"\r\n\r\n" in header_bytes or len(header_bytes) > 65536:
                break

        if b"\r\n\r\n" not in header_bytes:
            writer.close()
            return

        head, _, rest = header_bytes.partition(b"\r\n\r\n")
        head_text = head.decode("iso-8859-1", errors="replace")
        lines = head_text.split("\r\n")
        first_line = lines[0].split()
        if len(first_line) < 2:
            writer.close()
            return
            
        method = first_line[0].upper()
        path = first_line[1]

        # Parse headers
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        # Handle health check
        if method in ("GET", "HEAD") and path in ("/", "/health"):
            body = b"Shopify Webhook Receiver is UP"
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n"
            )
            writer.write(resp + (b"" if method == "HEAD" else body))
            await writer.drain()
            writer.close()
            return

        if method != "POST" or path.split("?", 1)[0] not in ("/shopify/orders", "/shopify/orders/create"):
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\nConnection: close\r\n\r\nNot Found")
            await writer.drain()
            writer.close()
            return

        # Read full request body
        content_length = int(headers.get("content-length", "0"))
        body_bytes = bytes(rest)
        while len(body_bytes) < content_length:
            chunk = await reader.read(min(65536, content_length - len(body_bytes)))
            if not chunk:
                break
            body_bytes += chunk

        # Parse JSON
        try:
            payload = json.loads(body_bytes[:content_length].decode("utf-8"))
        except Exception as e:
            log.warning(f"Invalid JSON request body: {e}")
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 12\r\nConnection: close\r\n\r\nInvalid JSON")
            await writer.drain()
            writer.close()
            return

        # Process order webhook
        prefix = get_store_prefix(path, headers)
        status_code, msg = await process_shopify_webhook(http_client, payload, prefix)
        
        status_line = f"HTTP/1.1 {status_code} "
        status_line += "OK\r\n" if status_code == 200 else "Error\r\n"
        
        body_resp = msg.encode("utf-8")
        resp = (
            status_line.encode() +
            b"Content-Type: text/plain\r\n" +
            b"Content-Length: " + str(len(body_resp)).encode() + b"\r\n" +
            b"Connection: close\r\n\r\n" +
            body_resp
        )
        writer.write(resp)
        await writer.drain()
        writer.close()

    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, asyncio.IncompleteReadError) as e:
        log.debug(f"Connection dropped by peer: {e}")
        try:
            writer.close()
        except Exception:
            pass
    except Exception as e:
        log.error(f"Error handling connection: {e}", exc_info=True)
        try:
            writer.close()
        except Exception:
            pass

async def run_backfill_loop(http_client: httpx.AsyncClient):
    """Periodically backfill orders for all stores in the background to prevent missed webhooks."""
    interval = int(os.getenv("SHOPIFY_BACKFILL_INTERVAL_SECONDS", "600"))
    log.info(f"⏰ Background backfill loop started (every {interval}s)")
    
    # Mapping of prefixes to their suffixes for env configuration
    brands_info = {
        "AM": "AMARA",
        "PV": "PELVINI",
        "E": "ELARA",
        "Di": "DIALO",
        "LU": "LUNE",
        "VX": "VIREX",
        "R": "RIMAL",
        "O": "ORLENTO",
        "VS": "VIRESTA",
        "DX": "DIWAN",
        "VL": "VELIX"
    }

    while True:
        try:
            await asyncio.sleep(interval)
            log.info("⏰ Starting background backfill cycle for all stores...")
            
            for prefix, env_suffix in brands_info.items():
                shopify_domain = os.getenv(f"SHOPIFY_DOMAIN_{env_suffix}", "").strip()
                shopify_token = os.getenv(f"SHOPIFY_TOKEN_{env_suffix}", "").strip()
                
                if not shopify_domain or not shopify_token:
                    continue
                    
                # Get limit for this store
                limit_val = os.getenv(f"SHOPIFY_BACKFILL_LIMIT_{env_suffix}")
                if not limit_val:
                    limit_val = os.getenv("SHOPIFY_BACKFILL_LIMIT", "5")
                try:
                    limit = int(limit_val.strip())
                except ValueError:
                    limit = 5
                    
                shopify_url = f"https://{shopify_domain}/admin/api/2024-04/orders.json"
                shopify_headers = {
                    "X-Shopify-Access-Token": shopify_token,
                    "Content-Type": "application/json",
                }
                params = {
                    "limit": limit,
                    "status": "any"
                }
                
                try:
                    resp = await http_client.get(shopify_url, headers=shopify_headers, params=params, timeout=20)
                    resp.raise_for_status()
                    orders = resp.json().get("orders", [])
                except Exception as e:
                    log.error(f"⏰ Error fetching backfill orders for {env_suffix}: {e}")
                    continue
                    
                for order in orders:
                    # Check if order is cancelled or voided
                    if order.get("cancelled_at") or order.get("financial_status") == "voided":
                        continue
                        
                    try:
                        data = parse_shopify_order(order, prefix)
                    except Exception as pe:
                        log.error(f"⏰ Error parsing backfill order for {env_suffix}: {pe}")
                        continue
                        
                    order_id = data["order_id"]

                    # Atomic claim first — stops two backfill cycles (or a webhook
                    # + backfill) from both inserting the same order.
                    if not order_dedup.claim(order_id):
                        continue

                    # Already in Notion (predates the dedup DB)? keep claim, skip.
                    try:
                        id_exists = await find_order_by_id(http_client, order_id)
                    except Exception:
                        order_dedup.release(order_id)
                        continue
                    if id_exists:
                        continue

                    log.info(f"⏰ Found missing order {order_id} in backfill for {env_suffix}. Pushing to Notion...")
                    properties = build_notion_properties(data)
                    ok = await create_notion_order(http_client, properties)
                    if not ok:
                        order_dedup.release(order_id)
                    
            log.info("⏰ Background backfill cycle complete.")
        except Exception as loop_err:
            log.error(f"⏰ Error in background backfill loop: {loop_err}", exc_info=True)

async def main():
    log.info("=" * 60)
    log.info("  Shopify Webhook Receiver to Notion CRM")
    log.info(f"  Port: {PORT}")
    log.info(f"  Target Data Source ID: {DATA_SOURCE_ID}")
    log.info("=" * 60)

    if not NOTION_API_KEY:
        log.error("NOTION_API_KEY is not set. Exiting.")
        return

    http_client = httpx.AsyncClient(timeout=30.0)

    # Start the periodic background backfill loop
    asyncio.create_task(run_backfill_loop(http_client))

    async def on_conn(reader, writer):
        await handle_connection(reader, writer, http_client)

    server = await asyncio.start_server(on_conn, "0.0.0.0", PORT)
    log.info(f"🌐 listening on 0.0.0.0:{PORT}")

    async with server:
        await server.serve_forever()

    await http_client.aclose()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted. Shutting down.")
