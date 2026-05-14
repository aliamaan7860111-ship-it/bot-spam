"""
telegram_client.py
==================
Telegram Bot wrapper for sending order notifications and handling commands.

Uses python-telegram-bot library for bot interactions.
Uses httpx to download images from URLs before sending to Telegram.

Commands handled:
    #ready ORDERID       — Mark order as processed, forward to fulfillment
    #cost ORDERID 500    — Store sourcing cost in Notion
    #note ORDERID text   — Store sourcing note in Notion
"""

import os
import re
import io
import json
import asyncio
import logging
from pathlib import Path
from typing import TypedDict


class SendOrderResult(TypedDict):
    success: bool                # True if ALL planned albums in this call sent
    albums_sent_this_call: int   # Number of albums delivered in THIS invocation
    total_albums: int            # Total albums planned for the order
    caption_message_id: int | None   # First msg_id of the caption-bearing album (only set when last sent)
    image_count: int             # Images successfully delivered in this call
    expected_count: int          # Parsed from order's item_qty field

# Separator image for visual divider between orders
SEPARATOR_PATH = Path(__file__).parent / "assets" / "separator.png"

import httpx
from telegram import Bot, InputMediaPhoto, Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

log = logging.getLogger("order_bridge.telegram")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_SOURCING_GROUP_ID = os.getenv("TELEGRAM_SOURCING_GROUP_ID", "")
TELEGRAM_FULFILLMENT_GROUP_ID = os.getenv("TELEGRAM_FULFILLMENT_GROUP_ID", "")

# ---------------------------------------------------------------------------
# Command patterns
# ---------------------------------------------------------------------------

# #ready ORDER-123 or #ready ORDER 123
READY_PATTERN = re.compile(r"#ready\s+(.+)", re.IGNORECASE)

# #cost ORDER-123 500 or #cost ORDER-123 500.50
COST_PATTERN = re.compile(r"#cost\s+(.+?)\s+(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)

# #note ORDER-123 some note text here
NOTE_PATTERN = re.compile(r"#note\s+(.+?)\s+(.+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Image downloading
# ---------------------------------------------------------------------------

def download_image(url: str) -> bytes | None:
    """Download an image from URL, return bytes or None on failure."""
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "image" in content_type or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                return resp.content
            else:
                log.warning(f"URL does not appear to be an image: {url} (content-type: {content_type})")
                return resp.content  # Try anyway
    except Exception as e:
        log.warning(f"Failed to download image from {url}: {e}")
        return None


def fetch_order_images_graphql(checkout_url: str, order: dict | None = None) -> list[str]:
    """
    Fetch product images from a Shopify order status page using the
    Customer Account GraphQL API.

    Supports two URL formats:
        - https://account.DOMAIN/orders/TOKEN
        - https://shopify.com/SHOPID/account/orders/TOKEN

    Returns a list of full-resolution CDN image URLs.
    Also populates order["line_items"] with variant/size data if order dict provided.
    """
    if not checkout_url:
        return []

    # Extract GraphQL endpoint + order token from the URL
    # Pattern 1: https://account.DOMAIN/orders/TOKEN
    m = re.match(r'(https://account\.[^/]+)/orders/([a-f0-9]+)', checkout_url)
    if m:
        graphql_url = f"{m.group(1)}/customer/api/unstable/graphql"
        token = m.group(2)
    else:
        # Pattern 2: https://shopify.com/SHOPID/account/orders/TOKEN
        m = re.match(r'(https://shopify\.com/\d+/account)/orders/([a-f0-9]+)', checkout_url)
        if m:
            graphql_url = f"{m.group(1)}/customer/api/unstable/graphql"
            token = m.group(2)
        else:
            # Pattern 3: https://STORE.com/SHOPID/orders/TOKEN (e.g. amarasroom.store)
            m = re.match(r'https://[^/]+/(\d+)/orders/([a-f0-9]+)', checkout_url)
            if m:
                shop_id = m.group(1)
                token = m.group(2)
                graphql_url = f"https://shopify.com/{shop_id}/account/customer/api/unstable/graphql"
            else:
                log.warning(f"  Could not parse checkout URL for GraphQL: {checkout_url}")
                return scrape_checkout_images(checkout_url)  # Fallback

    query = """
    {
      order(id: "gid://shopify/Order/%s") {
        lineItems(first: 50) {
          edges {
            node {
              title
              variantTitle
              quantity
              image {
                url
              }
            }
          }
        }
      }
    }
    """ % token

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                graphql_url,
                headers={
                    "Content-Type": "application/json",
                    "x-shopify-order-access-token": token,
                },
                json={"query": query},
            )
            resp.raise_for_status()
            data = resp.json()

        gql_order = data.get("data", {}).get("order")
        if not gql_order:
            log.warning(f"  GraphQL: no order data returned, falling back to scraper")
            return scrape_checkout_images(checkout_url)

        image_urls = []
        line_items = []
        for edge in gql_order.get("lineItems", {}).get("edges", []):
            node = edge.get("node", {})
            item = {
                "title": node.get("title", ""),
                "variant": node.get("variantTitle", ""),
                "quantity": node.get("quantity", 1),
            }
            line_items.append(item)
            img = node.get("image")
            if img and img.get("url"):
                # Request a compressed 800px wide image from Shopify instead of the massive original file
                # First, strip any existing size constraint, then inject _800x before the extension
                base_url = re.sub(r'_\d+x\d*(?=\.)', '', img["url"])
                # Inject _800x before the last dot
                if '.' in base_url.split('/')[-1]:
                    parts = base_url.rsplit('.', 1)
                    full_url = f"{parts[0]}_800x.{parts[1]}"
                else:
                    full_url = base_url
                image_urls.append(full_url)

        # Store line items (with size/variant) on the order dict
        if order is not None and line_items:
            order["line_items"] = line_items

        if image_urls:
            log.info(f"  📸 GraphQL: fetched {len(image_urls)} product image(s)")
        else:
            log.warning(f"  GraphQL: order found but no images, falling back to scraper")
            return scrape_checkout_images(checkout_url)

        return image_urls

    except Exception as e:
        log.warning(f"GraphQL image fetch failed: {e}, falling back to scraper")
        return scrape_checkout_images(checkout_url)


def scrape_checkout_images(checkout_url: str) -> list[str]:
    """
    FALLBACK: Scrape product images from a Shopify checkout/order page.
    Searches the raw HTML for CDN image URLs. Only works if the page
    doesn't block non-browser requests.
    Returns a list of image URLs found on the page.
    """
    if not checkout_url:
        return []

    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers=browser_headers) as client:
            resp = client.get(checkout_url)
            resp.raise_for_status()
            html = resp.text

        # Search ENTIRE page source for Shopify CDN image URLs
        cdn_pattern = re.compile(
            r'https?://cdn\.shopify\.com/s/files/[^"\'\\\>\s,\)]+\.(?:png|jpg|jpeg|webp|avif|gif)',
            re.IGNORECASE,
        )
        all_urls = cdn_pattern.findall(html)

        # Filter out non-product images
        product_images = []
        for url in all_urls:
            lower = url.lower()
            if any(skip in lower for skip in [
                "logo", "favicon", "icon", "badge", "payment",
                "spinner", "loader", "placeholder", "blank",
            ]):
                continue
            # Request a compressed 800px wide image from Shopify
            base_url = re.sub(r'_\d+x\d*(?=\.)', '', url)
            if '.' in base_url.split('/')[-1]:
                parts = base_url.rsplit('.', 1)
                full_res = f"{parts[0]}_800x.{parts[1]}"
            else:
                full_res = base_url
            product_images.append(full_res)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for url in product_images:
            if url not in seen:
                seen.add(url)
                unique.append(url)

        if unique:
            log.info(f"  📸 Scraped {len(unique)} product image(s) from checkout page")
        else:
            log.warning(f"  No product images found on checkout page: {checkout_url}")

        return unique

    except Exception as e:
        log.warning(f"Failed to scrape checkout page {checkout_url}: {e}")
        return []


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def safe_markdown(val) -> str:
    """Helper to safely escape Markdown characters to prevent Telegram parse errors."""
    if val is None:
        return ""
    # Strip markdown special characters to prevent Telegram API BadRequest
    text = str(val)
    for char in ['*', '_', '`', '[', ']', '(', ')', '~', '>']:
        text = text.replace(char, '')
    return text.strip()

# ---------------------------------------------------------------------------
# Album / caption helpers
# ---------------------------------------------------------------------------

TELEGRAM_MEDIA_GROUP_MAX = 10
TELEGRAM_CAPTION_MAX = 1024


def chunk_for_albums(items: list, chunk_size: int = TELEGRAM_MEDIA_GROUP_MAX) -> list[list]:
    """Split a list into chunks of up to chunk_size for Telegram media groups."""
    if not items:
        return []
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def parse_item_count(item_qty_field: str) -> int:
    """Count distinct items in the Notion 'ITEM | QTY' field.

    Each item is on its own line; quantity is always 1 in this system,
    so the line count equals the item count. Blank lines are ignored.
    """
    if not item_qty_field:
        return 0
    return sum(1 for line in item_qty_field.splitlines() if line.strip())


def truncate_caption_with_overflow(caption: str, max_length: int = TELEGRAM_CAPTION_MAX) -> tuple[str, str]:
    """Split a caption at max_length. Returns (short_caption, overflow_text).

    If caption fits, overflow is empty string. The caller decides what to do
    with the overflow (typically: send as a reply to the album).
    """
    if len(caption) <= max_length:
        return caption, ""
    return caption[:max_length], caption[max_length:]


def resume_range(start_album_index: int, total_albums: int) -> list[tuple[int, bool]]:
    """Return the list of (album_index, is_last) tuples for albums still to send.

    Empty list when everything has already been sent or there is nothing to send.
    is_last is True only for the final album in the order (where the caption goes).
    """
    if total_albums <= 0 or start_album_index >= total_albums:
        return []
    return [(i, i == total_albums - 1) for i in range(start_album_index, total_albums)]


def sort_orders_for_label_replies(orders: list[dict]) -> list[dict]:
    """Sort orders for /print label dispatch.

    Orders with a stored fulfillment_message_id go first, ascending by that id
    (Telegram message_ids are monotonic per chat, so this is chronological).
    Legacy orders (no message_id) go last, alphabetical by order_id for determinism.
    """
    anchored = [o for o in orders if o.get("fulfillment_message_id")]
    legacy = [o for o in orders if not o.get("fulfillment_message_id")]
    anchored.sort(key=lambda o: o["fulfillment_message_id"])
    legacy.sort(key=lambda o: o.get("order_id", ""))
    return anchored + legacy


def legacy_label_caption(order_id: str) -> str:
    """Caption for label PDFs sent for legacy orders (no message_id anchor)."""
    return f"📄 {order_id.strip()} — legacy order, no thread anchor"


def format_sourcing_message(order: dict) -> str:
    """
    Minimal message for the sourcing group.
    Shows only Order ID + Size (if available).
    """
    lines = [
        f"*Order ID:* `{safe_markdown(order.get('order_id', 'N/A'))}`",
    ]

    # Add size/variant from line items (fetched via GraphQL)
    line_items = order.get("line_items", [])
    for item in line_items:
        variant = item.get("variant", "")
        if variant and variant.lower() != "default title":
            lines.append(f"*Size:* {safe_markdown(variant)}")

    return "\n".join(lines)


def format_fulfillment_message(order: dict) -> str:
    """
    Full message for the fulfillment group.
    Shows Order ID, Customer, Phone, Product details, Amount, Address.
    """
    lines = [
        f"*Order ID:* `{safe_markdown(order.get('order_id', 'N/A'))}`",
        "",
        "",
        f"*Customer:* {safe_markdown(order.get('customer_name', 'N/A'))}",
        "",
        "",
        f"*Phone:* {safe_markdown(order.get('phone', 'N/A'))}",
        "",
    ]

    # Product details from line items
    line_items = order.get("line_items", [])
    if line_items:
        lines.append("*Product:*")
        for item in line_items:
            variant = item.get("variant", "")
            qty = item.get("quantity", 1)
            if variant and variant.lower() != "default title":
                lines.append(f"• Size: {safe_markdown(variant)}")
            lines.append(f"• Quantity: {qty}")
    else:
        # Fallback to ITEM | QTY from Notion
        lines.append(f"*Product:* {safe_markdown(order.get('item_qty', 'N/A'))}")

    lines.append("")
    amount = str(order.get('total_aed', 'N/A')).strip()
    # Remove existing "AED" prefix to avoid "AED AED 150"
    if amount.upper().startswith("AED"):
        amount = amount[3:].strip()
    lines.append(f"*Amount:* AED {safe_markdown(amount)}")
    lines.append("")

    if order.get("full_address"):
        lines.append(f"*Delivery Address:*")
        lines.append(f"{safe_markdown(order['full_address'])}")

    return "\n".join(lines)


def format_order_message(order: dict, header: str = "📦 NEW ORDER FOR SOURCING") -> str:
    """
    Route to the appropriate formatter based on the header/context.
    Kept for backwards compatibility.
    """
    if "FULFIL" in header.upper() or "READY" in header.upper():
        return format_fulfillment_message(order)
    return format_sourcing_message(order)


# ---------------------------------------------------------------------------
# Send order to a Telegram group
# ---------------------------------------------------------------------------

async def send_order_to_group(
    bot: Bot,
    group_id: str | int,
    order: dict,
    header: str = "✅ READY FOR FULFILLMENT",
    start_album_index: int = 0,
    on_album_sent=None,  # async callable: (new_albums_sent_count, caption_message_id_or_None) -> None
) -> SendOrderResult:
    """
    Send order as a sequence of atomic media groups (Telegram caps each at 10 items).
    The caption goes on the FIRST image of the LAST album.

    Resume protocol: if start_album_index > 0, skip the first N albums (the caller has
    already confirmed they were sent before). After each album sends successfully,
    invokes on_album_sent(new_count, caption_message_id) so the caller can checkpoint.

    Returns SendOrderResult. success=True only when all planned albums sent.
    """
    result: SendOrderResult = {
        "success": False,
        "albums_sent_this_call": 0,
        "total_albums": 0,
        "caption_message_id": None,
        "image_count": 0,
        "expected_count": parse_item_count(order.get("item_qty", "")),
    }

    try:
        group_id = int(group_id)
    except (ValueError, TypeError):
        log.error(f"Invalid Telegram group ID: {group_id}")
        return result

    # Image priority: GraphQL primary, Notion files fallback.
    checkout_url = order.get("order_source_url", "")
    notion_image_urls = order.get("image_urls", [])
    if checkout_url and not order.get("line_items"):
        log.info("  Fetching line items (sizes) via GraphQL...")
        graphql_images = fetch_order_images_graphql(checkout_url, order)
        if graphql_images:
            order["image_urls"] = graphql_images
        elif notion_image_urls:
            log.info(f"  GraphQL returned no images; falling back to {len(notion_image_urls)} from Notion IMAGE URL")
            order["image_urls"] = notion_image_urls

    image_urls = order.get("image_urls", [])

    # Download all images upfront so we know actual delivered count.
    downloaded: list[bytes] = []
    for url in image_urls:
        img_bytes = download_image(url)
        if img_bytes:
            downloaded.append(img_bytes)
        else:
            log.warning(f"  Skipped image: {url}")

    caption_full = format_order_message(order, header)

    if not downloaded:
        # No images. Send caption as a plain message + separator so the order is visible.
        if start_album_index > 0:
            log.warning(f"  No images for {order.get('order_id', '?')} but start_album_index={start_album_index}; sending text-only fallback")
        try:
            text_msg = await bot.send_message(
                chat_id=group_id, text=caption_full, parse_mode="Markdown",
                read_timeout=60, write_timeout=60,
            )
            await _send_separator(bot, group_id)
            result["success"] = True
            result["total_albums"] = 0
            result["caption_message_id"] = text_msg.message_id
            if on_album_sent is not None:
                await on_album_sent(0, text_msg.message_id)
        except Exception as e:
            log.error(f"Failed to send text-only order: {e}")
        return result

    short_caption, overflow_text = truncate_caption_with_overflow(caption_full)

    # Chunk the downloaded bytes into albums of <=10.
    album_chunks = chunk_for_albums(downloaded)
    total_albums = len(album_chunks)
    result["total_albums"] = total_albums
    result["image_count"] = len(downloaded)

    # Compute which albums actually need sending.
    plan = resume_range(start_album_index, total_albums)
    if not plan:
        log.info(f"  Skipping send for {order.get('order_id', '?')}: counter {start_album_index} >= total {total_albums}")
        result["success"] = True
        return result

    last_message_id: int | None = None
    try:
        for album_index, is_last in plan:
            chunk = album_chunks[album_index]
            media: list[InputMediaPhoto] = []
            for j, img_bytes in enumerate(chunk):
                if is_last and j == 0:
                    media.append(InputMediaPhoto(
                        media=io.BytesIO(img_bytes),
                        caption=short_caption,
                        parse_mode="Markdown",
                    ))
                else:
                    media.append(InputMediaPhoto(media=io.BytesIO(img_bytes)))

            if len(media) == 1:
                first = media[0]
                photo_msg = await bot.send_photo(
                    chat_id=group_id,
                    photo=first.media,
                    caption=getattr(first, "caption", None),
                    parse_mode="Markdown" if getattr(first, "caption", None) else None,
                    read_timeout=60, write_timeout=60,
                )
                first_msg_id_of_album = photo_msg.message_id
                last_message_id = photo_msg.message_id
            else:
                msgs = await bot.send_media_group(
                    chat_id=group_id, media=media,
                    read_timeout=60, write_timeout=60,
                )
                first_msg_id_of_album = msgs[0].message_id
                last_message_id = msgs[-1].message_id

            result["albums_sent_this_call"] += 1
            cap_id_for_callback: int | None = first_msg_id_of_album if is_last else None
            if is_last:
                result["caption_message_id"] = first_msg_id_of_album

            # Caption overflow handling — fires once, on the LAST album only.
            if is_last and overflow_text and last_message_id is not None:
                try:
                    await bot.send_message(
                        chat_id=group_id,
                        text=overflow_text,
                        reply_to_message_id=last_message_id,
                        parse_mode="Markdown",
                        read_timeout=60, write_timeout=60,
                    )
                except Exception as e:
                    log.warning(f"Failed to send overflow reply: {e}")

            # Checkpoint after each successful album.
            new_count = album_index + 1
            if on_album_sent is not None:
                try:
                    await on_album_sent(new_count, cap_id_for_callback)
                except Exception as e:
                    log.error(f"  on_album_sent callback raised: {e}", exc_info=True)

        log.info(f"  ✓ Sent {result['albums_sent_this_call']} album(s) to group {group_id}")

    except Exception as e:
        log.error(f"Failed to send album to Telegram: {e}", exc_info=True)
        try:
            await bot.send_message(
                chat_id=group_id,
                text=f"⚠️ Could not send album. URLs:\n" + "\n".join(image_urls) + f"\n\n{caption_full}",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return result

    await _send_separator(bot, group_id)
    result["success"] = True
    return result


async def _send_separator(bot: Bot, group_id: int) -> None:
    """Send the black separator image. Best-effort; logs but does not raise."""
    try:
        if SEPARATOR_PATH.exists():
            with open(SEPARATOR_PATH, "rb") as f:
                await bot.send_photo(
                    chat_id=group_id, photo=f,
                    read_timeout=60, write_timeout=60,
                )
        else:
            await bot.send_message(
                chat_id=group_id,
                text="━━━━━━━━━━━━━━━━━━━━",
                read_timeout=60, write_timeout=60,
            )
    except Exception as e:
        log.warning(f"Could not send separator: {e}")


async def send_order_via_file_ids(
    bot: Bot,
    group_id: str | int,
    order: dict,
    header: str = "✅ READY FOR FULFILLMENT",
) -> SendOrderResult:
    """
    Send an order to a Telegram group by REUSING file_ids saved during Stage 1.
    No GraphQL fetch, no image download — Telegram clones from cache.

    Returns a SendOrderResult. If file_ids fail (extremely rare), success=False
    and the caller should fall back to send_order_to_group (GraphQL refetch path).
    """
    result: SendOrderResult = {
        "success": False,
        "file_ids": [],
        "image_count": 0,
        "expected_count": parse_item_count(order.get("item_qty", "")),
    }
    try:
        group_id = int(group_id)
    except (ValueError, TypeError):
        log.error(f"Invalid Telegram group ID: {group_id}")
        return result

    file_ids = order.get("telegram_file_ids", [])
    if not file_ids:
        log.warning(f"  send_order_via_file_ids: no file_ids on order {order.get('order_id', '?')}")
        return result

    caption_full = format_order_message(order, header)
    short_caption, overflow_text = truncate_caption_with_overflow(caption_full)

    album_chunks = chunk_for_albums(file_ids)
    last_idx = len(album_chunks) - 1

    last_message_id: int | None = None
    try:
        for i, chunk in enumerate(album_chunks):
            media: list[InputMediaPhoto] = []
            for j, fid in enumerate(chunk):
                if i == last_idx and j == 0:
                    media.append(InputMediaPhoto(media=fid, caption=short_caption,
                                                  parse_mode="Markdown"))
                else:
                    media.append(InputMediaPhoto(media=fid))

            if len(media) == 1:
                first = media[0]
                photo_msg = await bot.send_photo(
                    chat_id=group_id, photo=first.media,
                    caption=getattr(first, "caption", None),
                    parse_mode="Markdown" if getattr(first, "caption", None) else None,
                    read_timeout=60, write_timeout=60,
                )
                last_message_id = photo_msg.message_id
            else:
                msgs = await bot.send_media_group(
                    chat_id=group_id, media=media,
                    read_timeout=60, write_timeout=60,
                )
                last_message_id = msgs[-1].message_id

        if overflow_text and last_message_id is not None:
            try:
                await bot.send_message(
                    chat_id=group_id, text=overflow_text,
                    reply_to_message_id=last_message_id,
                    parse_mode="Markdown",
                    read_timeout=60, write_timeout=60,
                )
            except Exception as e:
                log.warning(f"Failed to send overflow reply: {e}")

        await _send_separator(bot, group_id)
        result["success"] = True
        result["image_count"] = len(file_ids)
        result["file_ids"] = file_ids

    except Exception as e:
        log.error(f"  Failed to send album via file_ids: {e}", exc_info=True)

    return result


# ---------------------------------------------------------------------------
# Command Handlers (called by the bot when messages arrive)
# ---------------------------------------------------------------------------

# These handlers are set up by the main bridge script (order_bridge.py)
# which provides the notion_client dependency via closure.


def create_command_handlers(notion_module):
    """
    Factory that creates Telegram message handlers with access to notion_client.
    Returns a list of handlers to add to the Application.

    Args:
        notion_module: The notion_client module (imported in order_bridge.py)
    """

    async def handle_ready(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle #ready command variants:
            #ready all        — forward ALL confirmed orders to fulfillment
            #ready ORDERID    — forward a specific order to fulfillment
            #ready (no args)  — show usage help
        """
        if not update.message or not update.message.text:
            return

        text = update.message.text.strip()
        parts = text.split(None, 1)  # ["#ready", "argument"] or ["#ready"]

        if len(parts) < 2:
            await update.message.reply_text(
                "ℹ️ *Ready Usage:*\n\n"
                "`#ready all` — Forward all sourced orders to fulfillment\n"
                "`#ready ORDER-123` — Forward a specific order to fulfillment",
                parse_mode="Markdown",
            )
            return

        arg = parts[1].strip()
        bot = context.bot
        fulfillment_group = TELEGRAM_FULFILLMENT_GROUP_ID

        # --- #ready all ---
        if arg.lower() == "all":
            log.info("Received #ready all command")
            await update.message.reply_text("🔄 Finding all SOURCING orders to forward to fulfillment...", parse_mode="Markdown")

            orders = notion_module.query_sourcing_orders()
            if not orders:
                await update.message.reply_text(
                    "ℹ️ No orders with *SOURCING* status found.\n\n"
                    "Orders need to go through sourcing first:\n"
                    "1️⃣ Set order to `CONFIRMED | PROCESSING` in Notion\n"
                    "2️⃣ Bot auto-sends to sourcing group (status → SOURCING)\n"
                    "3️⃣ Then use `#ready all` to forward to fulfillment",
                    parse_mode="Markdown",
                )
                return

            sent_count = 0
            fail_count = 0
            for i, order in enumerate(orders):
                oid = order.get("order_id", "?")
                try:
                    # Mark as Processed in Notion (prevents re-processing)
                    success = notion_module.mark_order_processed(order["page_id"])
                    if not success:
                        log.warning(f"  Failed to mark {oid} as Processed")
                        fail_count += 1
                        continue

                    # Choose fast path (saved file_ids) vs fallback (GraphQL refetch).
                    sent_ok = False
                    if fulfillment_group:
                        if order.get("file_ids_saved") and order.get("telegram_file_ids"):
                            res = await send_order_via_file_ids(
                                bot, fulfillment_group, order,
                                header="✅ READY FOR FULFILLMENT",
                            )
                            sent_ok = res["success"]
                            if not sent_ok:
                                log.warning(f"  fast path failed for {oid}, falling back to GraphQL")
                        if not sent_ok:
                            res = await send_order_to_group(
                                bot, fulfillment_group, order,
                                header="✅ READY FOR FULFILLMENT",
                            )
                            sent_ok = res["success"]
                            if sent_ok and not order.get("file_ids_saved"):
                                try:
                                    await bot.send_message(
                                        chat_id=fulfillment_group,
                                        text=f"⚠️ {oid}: no file IDs were saved, resorted to fallback fetch",
                                    )
                                except Exception:
                                    pass
                    if not sent_ok:
                        fail_count += 1
                        continue
                    sent_count += 1
                    # Track fulfillment send
                    notion_module.mark_fulfillment_notified(order["page_id"])
                    log.info(f"  ✓ Order {oid} forwarded to fulfillment")

                    # Rate limit: wait between orders to avoid Telegram API limits
                    if i < len(orders) - 1:
                        await asyncio.sleep(1.5)
                except Exception as e:
                    log.error(f"  Failed to process order {oid}: {e}")
                    fail_count += 1

            result_msg = f"✅ *Ready All Complete*\n\n📦 Sent to fulfillment: *{sent_count}*"
            if fail_count:
                result_msg += f"\n❌ Failed: *{fail_count}*"
            await update.message.reply_text(result_msg, parse_mode="Markdown")
            return

        # --- #ready ORDERID ---
        order_id = arg
        log.info(f"Received #ready command for order: {order_id}")

        order = notion_module.find_order_by_id(order_id)
        if not order:
            await update.message.reply_text(f"❌ Order `{order_id}` not found in Notion.", parse_mode="Markdown")
            return

        # Check if already processed — prevent duplicate fulfillment sends
        current_status = order.get("order_status", "")
        if current_status.upper() == "PROCESSED":
            await update.message.reply_text(
                f"ℹ️ Order `{order_id}` is already *Processed* and was previously sent to fulfillment.",
                parse_mode="Markdown",
            )
            return

        success = notion_module.mark_order_processed(order["page_id"])
        if not success:
            await update.message.reply_text(f"❌ Failed to update order `{order_id}` in Notion.", parse_mode="Markdown")
            return

        if fulfillment_group:
            sent_ok = False
            if order.get("file_ids_saved") and order.get("telegram_file_ids"):
                res = await send_order_via_file_ids(
                    bot, fulfillment_group, order,
                    header="✅ READY FOR FULFILLMENT",
                )
                sent_ok = res["success"]
                if not sent_ok:
                    log.warning(f"  fast path failed for {order_id}, falling back to GraphQL")
                    res = await send_order_to_group(
                        bot, fulfillment_group, order,
                        header="✅ READY FOR FULFILLMENT",
                    )
                    sent_ok = res["success"]
                    if sent_ok:
                        try:
                            await bot.send_message(
                                chat_id=fulfillment_group,
                                text=f"⚠️ {order_id}: no file IDs (fast path failed), resorted to fallback fetch",
                            )
                        except Exception:
                            pass
            else:
                res = await send_order_to_group(
                    bot, fulfillment_group, order,
                    header="✅ READY FOR FULFILLMENT",
                )
                sent_ok = res["success"]
                if sent_ok and not order.get("file_ids_saved"):
                    try:
                        await bot.send_message(
                            chat_id=fulfillment_group,
                            text=f"⚠️ {order_id}: no file IDs were saved, resorted to fallback fetch",
                        )
                    except Exception:
                        pass

            log.info(f"  ✓ Order {order_id} forwarded to fulfillment group")
            notion_module.mark_fulfillment_notified(order["page_id"])

        await update.message.reply_text(
            f"✅ Order `{order_id}` marked as *Processed* and forwarded to fulfillment.",
            parse_mode="Markdown",
        )

    async def handle_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle #cost ORDERID amount command."""
        if not update.message or not update.message.text:
            return

        match = COST_PATTERN.match(update.message.text.strip())
        if not match:
            return

        order_id = match.group(1).strip()
        cost = float(match.group(2))
        log.info(f"Received #cost command for order: {order_id}, cost: {cost}")

        # Look up the order
        order = notion_module.find_order_by_id(order_id)
        if not order:
            await update.message.reply_text(f"❌ Order `{order_id}` not found in Notion.", parse_mode="Markdown")
            return

        # Update cost in Notion
        success = notion_module.set_sourcing_cost(order["page_id"], cost)
        if success:
            await update.message.reply_text(
                f"✅ Cost for order `{order_id}` set to *AED {cost}*.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"❌ Failed to update cost for `{order_id}`.", parse_mode="Markdown")

    async def handle_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle #note ORDERID text command."""
        if not update.message or not update.message.text:
            return

        match = NOTE_PATTERN.match(update.message.text.strip())
        if not match:
            return

        order_id = match.group(1).strip()
        note_text = match.group(2).strip()
        log.info(f"Received #note command for order: {order_id}")

        # Look up the order
        order = notion_module.find_order_by_id(order_id)
        if not order:
            await update.message.reply_text(f"❌ Order `{order_id}` not found in Notion.", parse_mode="Markdown")
            return

        # Update note in Notion (append to INTERNAL NOTE)
        existing_note = order.get("internal_note", "")
        new_note = f"{existing_note}\n[Sourcing] {note_text}".strip() if existing_note else f"[Sourcing] {note_text}"

        success = notion_module._update_page(order["page_id"], {
            notion_module.FIELD_INTERNAL_NOTE: {
                "rich_text": [{"type": "text", "text": {"content": new_note}}]
            }
        })
        if success:
            await update.message.reply_text(
                f"✅ Note added to order `{order_id}`.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"❌ Failed to add note for `{order_id}`.", parse_mode="Markdown")

    async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle #reset command variants:
            #reset 24h          — reset all orders from the last 24 hours to NEW
            #reset ORDER-123    — reset a specific order to NEW
            #reset (no args)    — show usage help
        """
        if not update.message or not update.message.text:
            return

        text = update.message.text.strip()
        log.info(f"Received #reset command: {text}")

        # Parse the argument after "#reset"
        parts = text.split(None, 1)  # ["#reset", "argument"] or ["#reset"]
        log.info(f"  Reset parts: {parts}, count: {len(parts)}")
        if len(parts) < 2:
            # No argument — show help
            await update.message.reply_text(
                "ℹ️ *Reset Usage:*\n\n"
                "`#reset 24h` — Reset all orders from last 24 hours to NEW\n"
                "`#reset ORDER-123` — Reset a specific order to NEW",
                parse_mode="Markdown",
            )
            return

        arg = parts[1].strip()

        # --- #reset 24h ---
        if arg.lower() == "24h":
            await update.message.reply_text("🔄 Resetting orders from the last 24 hours...", parse_mode="Markdown")

            orders = notion_module.query_recent_orders(hours=24)
            if not orders:
                await update.message.reply_text("ℹ️ No orders found in the last 24 hours.", parse_mode="Markdown")
                return

            reset_count = 0
            failed = []

            for order in orders:
                oid = order.get("order_id", "")
                pid = order.get("page_id", "")
                if not oid or not pid:
                    continue

                # Reset status to NEW in Notion
                success = notion_module.mark_order_new(pid)
                if success:
                    reset_count += 1
                    # Remove from tracking so poller picks it up again
                    notion_module.unmark_notified(pid)
                else:
                    failed.append(oid)

            msg = f"✅ *Reset complete!*\n\n📦 Orders reset to NEW: *{reset_count}*"
            if failed:
                msg += f"\n❌ Failed: {', '.join(failed)}"
            await update.message.reply_text(msg, parse_mode="Markdown")
            log.info(f"  Reset 24h: {reset_count} orders reset, {len(failed)} failed")
            return

        # --- #reset ORDER-ID ---
        order_id = arg
        order = notion_module.find_order_by_id(order_id)
        if not order:
            await update.message.reply_text(
                f"❌ Order `{order_id}` not found in Notion.",
                parse_mode="Markdown",
            )
            return

        success = notion_module.mark_order_new(order["page_id"])
        if not success:
            await update.message.reply_text(
                f"❌ Failed to reset order `{order_id}` in Notion.",
                parse_mode="Markdown",
            )
            return

        # Remove from tracking
        notion_module.unmark_notified(order["page_id"])

        await update.message.reply_text(
            f"✅ Order `{order_id}` reset to *NEW* and removed from tracker.",
            parse_mode="Markdown",
        )

    async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle #status command — shows tracking info."""
        if not update.message or not update.message.text:
            return

        await update.message.reply_text(
            f"📊 *Bridge Status*\n\n"
            f"The bridge is online and active! 🚀\n"
            f"Order tracking has been upgraded to run natively via Notion checkboxes (`SOURCING NOTIFIED` and `FULFILLMENT NOTIFIED`) to prevent server resets.",
            parse_mode="Markdown",
        )

    # Build handlers — only trigger on messages starting with #
    handlers = [
        MessageHandler(filters.TEXT & filters.Regex(r"^#ready\b"), handle_ready),
        MessageHandler(filters.TEXT & filters.Regex(r"^#cost\b"), handle_cost),
        MessageHandler(filters.TEXT & filters.Regex(r"^#note\b"), handle_note),
        MessageHandler(filters.TEXT & filters.Regex(r"^#reset\b"), handle_reset),
        MessageHandler(filters.TEXT & filters.Regex(r"^#status\b"), handle_status),
    ]

    return handlers


# ---------------------------------------------------------------------------
# Connectivity Test
# ---------------------------------------------------------------------------

async def test_connection() -> bool:
    """Test that the Telegram bot token is valid."""
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is not set in .env")
        return False

    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        me = await bot.get_me()
        log.info(f"✓ Connected to Telegram bot: @{me.username} ({me.first_name})")
        return True
    except Exception as e:
        log.error(f"Telegram connection failed: {e}")
        return False
