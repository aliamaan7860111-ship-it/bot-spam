"""Facebook Ads Library gate — checks whether a store has any active ads in a
country before the load-test bot bothers running orders against it.

Usage from load_test_bot.py:

    from fb_ads_gate import filter_stores_with_active_ads

    active = await filter_stores_with_active_ads(
        # PREFERRED: map domain -> FB Page ID (numeric). The gate hits the
        # page's ad listing directly — no keyword collisions, fast load.
        store_query_map={
            "https://mandarerabrands.com/": "1023336610862074",
            "https://hypedxb.store":         "1153411037859666",
        },
        # OR: keyword strings (fallback when you don't know the page id)
        # store_query_map={"https://mandarerabrands.com/": "Mandarera"},
        country_code="AE",
        proxy_url=TEST_PROXY,
    )
    # `active` is a list of store URLs that have active ads. Loop over those
    # instead of the full STORE_URLS for the next round.

The gate is cheap to call repeatedly because results are cached in-process for
CACHE_TTL_SECONDS. FB Ads Library doesn't update in real time anyway.
"""

import asyncio
import logging
import time
from urllib.parse import quote_plus, urlparse

from playwright.async_api import async_playwright

log = logging.getLogger("fb_ads_gate")

CACHE_TTL_SECONDS = 60 * 60          # 60 min — FB updates lag ~5-15 min anyway
GATE_TIMEOUT_MS = 35_000             # per-page load
NAV_HUMANIZE_MS_RANGE = (4_000, 8_000)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Cache: {(query_lower, country): (timestamp, has_active_bool, count)}
_cache: dict[tuple[str, str], tuple[float, bool, int]] = {}


def _build_url(target: str, country_code: str) -> str:
    """Build FB Ads Library URL.

    `target` is either a numeric Page ID (preferred — direct page-ad listing,
    most reliable) or a free-text keyword query (fallback)."""
    if target.isdigit():
        # Direct Page ID lookup — shows ALL active ads from that specific page.
        # Far more reliable than keyword search because there's no name collision.
        return (
            "https://www.facebook.com/ads/library/?"
            f"active_status=active&ad_type=all&country={country_code}"
            f"&is_targeted_country=false&media_type=all&search_type=page"
            f"&sort_data[mode]=total_impressions&sort_data[direction]=desc"
            f"&view_all_page_id={target}"
        )
    # Keyword fallback (preserved for backwards compatibility)
    return (
        "https://www.facebook.com/ads/library/?"
        f"active_status=active&ad_type=all&country={country_code}"
        f"&q={quote_plus(target)}&search_type=keyword_unordered"
        "&media_type=all"
    )


async def _count_active_ads_on_page(page) -> int:
    """Count the visible ad cards on a loaded FB Ads Library results page.

    FB rotates class names so we use multiple structural fallbacks rather than
    relying on any single selector. Returns -1 if the page state is ambiguous
    (login wall, network error, captcha)."""
    try:
        # 1. Check for explicit "no results" copy
        no_results_text = await page.evaluate(
            """() => {
                const t = document.body ? document.body.innerText : '';
                return /no\\s+results|0\\s+results|we couldn.?t find/i.test(t);
            }"""
        )
        if no_results_text:
            return 0

        # 2. Check for login wall / consent screen — ambiguous, return -1
        login_wall = await page.evaluate(
            """() => {
                const t = document.body ? document.body.innerText : '';
                return /log in to facebook|create new account|you must log in/i.test(t)
                    || !!document.querySelector('input[name="email"], input[name="pass"]');
            }"""
        )
        if login_wall:
            return -1

        # 3. Count "X results" header if present (most reliable signal)
        match_data = await page.evaluate(
            """() => {
                const t = document.body ? document.body.innerText : '';
                // FB shows e.g. "~12 results" or "12 ads". Require word boundary
                // after the keyword so we don't match navigation entries like
                // "Ads" or "Ad Library".
                const m = t.match(/~?\\s*([0-9,]+)\\s+(results?|ads?)\\b/i);
                return {
                    bodyLen: t.length,
                    match: m ? m[0] : null,
                    matchedNumber: m ? m[1] : null,
                    // Snippets that should contain count text on a real page
                    snippet: t.match(/.{0,40}(results?|ads?)\\b.{0,40}/i)
                        ? t.match(/.{0,40}(results?|ads?)\\b.{0,40}/i)[0] : null,
                };
            }"""
        )
        log.info(f"[FB Gate] count parse: bodyLen={match_data['bodyLen']} match={match_data['match']!r} snippet={match_data['snippet']!r}")
        if match_data["matchedNumber"] is not None:
            return int(match_data["matchedNumber"].replace(",", ""))

        # 4. Structural count of ad cards as last resort
        card_count = await page.evaluate(
            """() => {
                // FB Ads Library cards have specific role / data attributes
                const sels = [
                    'div[role="article"]',
                    '[data-testid="ad-library-result-card"]',
                    'div._99s5',  // legacy class
                ];
                for (const s of sels) {
                    const n = document.querySelectorAll(s).length;
                    if (n > 0) return n;
                }
                return 0;
            }"""
        )
        return card_count
    except Exception as e:
        log.warning(f"[FB Gate] count error: {e}")
        return -1


async def check_query(
    query: str,
    country_code: str = "AE",
    proxy_url: str | None = None,
    use_cache: bool = True,
) -> tuple[bool, int]:
    """Returns (has_active_ads, ad_count). ad_count == -1 means ambiguous."""
    cache_key = (query.strip().lower(), country_code.upper())
    now = time.time()

    if use_cache and cache_key in _cache:
        ts, ok, cnt = _cache[cache_key]
        if now - ts < CACHE_TTL_SECONDS:
            log.info(f"[FB Gate] cache hit '{query}' -> active={ok} count={cnt}")
            return ok, cnt

    url = _build_url(query, country_code)
    log.info(f"[FB Gate] checking '{query}' in {country_code}: {url}")

    async with async_playwright() as p:
        launch_args = {"headless": True, "channel": "chrome"}
        if proxy_url:
            parsed = urlparse(proxy_url)
            cfg = {
                "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            }
            if parsed.username and parsed.password:
                cfg["username"] = str(parsed.username)
                cfg["password"] = str(parsed.password)
            launch_args["proxy"] = cfg

        try:
            browser = await p.chromium.launch(**launch_args)
        except Exception:
            launch_args.pop("channel", None)
            browser = await p.chromium.launch(**launch_args)

        try:
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
                locale="en-US",
                timezone_id="Asia/Dubai",
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=GATE_TIMEOUT_MS)
            except Exception as e:
                log.warning(f"[FB Gate] nav error for '{query}': {e}")
                # Soft-fail: assume active, surface error to caller via ambiguous count
                _cache[cache_key] = (now, True, -1)
                return True, -1

            # Wait for the results region to render (or fail quickly)
            try:
                # Wait for the actual result-count text to render. FB's nav
                # contains "Ad Library" / "Ads" so we must require a DIGIT
                # before the result/ad keyword to avoid false-positive early
                # exit (which was returning count=0 before content rendered).
                wait_ok = True
                try:
                    await page.wait_for_function(
                        """() => {
                            const t = document.body ? document.body.innerText : '';
                            return /\\d+\\s+results?\\b|\\d+\\s+ads?\\b|no\\s+results|we\\s+couldn/i.test(t);
                        }""",
                        timeout=30_000,
                    )
                except Exception:
                    wait_ok = False
            except Exception:
                wait_ok = False

            # Settle delay so the count text stabilises (FB sometimes shows
            # "1 result" then jumps to "~18 results" as paginated data arrives)
            await asyncio.sleep(3)

            count = await _count_active_ads_on_page(page)

            # If our wait timed out AND we found 0, the page probably hadn't
            # rendered the count yet — give it more time and recount once.
            if not wait_ok and count == 0:
                log.info(f"[FB Gate] wait timed out for '{query}' — extra 8s recount")
                await asyncio.sleep(8)
                count = await _count_active_ads_on_page(page)
                # Still 0 with no positive signal? Treat as ambiguous so we
                # don't false-negative a live campaign because the page was
                # slow to render.
                if count == 0:
                    log.warning(f"[FB Gate] '{query}' inconclusive — treating as ambiguous")
                    count = -1
            if count == -1:
                # Ambiguous — soft-fail to active so we don't accidentally block
                # the order bot just because FB threw a login wall on us
                log.warning(f"[FB Gate] ambiguous result for '{query}' (login wall or error)")
                _cache[cache_key] = (now, True, -1)
                return True, -1

            has_active = count > 0
            _cache[cache_key] = (now, has_active, count)
            log.info(f"[FB Gate] '{query}' -> active={has_active} count={count}")
            return has_active, count
        finally:
            await browser.close()


async def filter_stores_with_active_ads(
    store_query_map: dict[str, str],
    country_code: str = "AE",
    proxy_url: str | None = None,
) -> list[str]:
    """Given {store_url: search_query}, returns the subset of store URLs whose
    queries have active ads in `country_code`. Soft-fails to inclusion on
    ambiguous results so the order bot keeps testing rather than going dark
    over a transient FB error."""
    active_urls: list[str] = []
    for store_url, query in store_query_map.items():
        if not query:
            log.warning(f"[FB Gate] no query for {store_url} — including by default")
            active_urls.append(store_url)
            continue
        try:
            ok, count = await check_query(query, country_code, proxy_url)
            if ok:
                active_urls.append(store_url)
            else:
                log.info(f"[FB Gate] skipping {store_url} ('{query}') — no active ads")
        except Exception as e:
            log.warning(f"[FB Gate] error checking {store_url}: {e} — including by default")
            active_urls.append(store_url)
    return active_urls


# CLI helper for ad-hoc verification:
#   python -m execution.fb_ads_gate 1023336610862074 1153411037859666
#   python -m execution.fb_ads_gate Mandarera Hypedxb       # keyword mode
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    queries = sys.argv[1:] or ["Mandarera"]

    async def _main():
        for q in queries:
            ok, n = await check_query(q, country_code="AE", use_cache=False)
            print(f"  {q!r}: active={ok}  count={n}")

    asyncio.run(_main())
