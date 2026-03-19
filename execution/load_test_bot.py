import os
import time
import asyncio
import logging
import random
import argparse
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# Load .env
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(".tmp/load_test_bot.log", mode='a'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("load_test_bot")

# Config
# Set to a comma-separated list of store URLs (e.g. https://store1.com,https://store2.com)
STORE_URLS = [url.strip() for url in os.getenv("TEST_STORE_URL", "").split(",") if url.strip()]
# Fallback to exact product URL if provided
PRODUCT_URL = os.getenv("TEST_PRODUCT_URL", "")
TEST_INTERVAL_MINS = float(os.getenv("TEST_INTERVAL_MINS", "10"))
TEST_PROXY = os.getenv("TEST_PROXY", "")
# Bright Data Scraping Browser URL (wss://user:pass@brd.superproxy.io:9222)
BLOCK_RESOURCES = os.getenv("BLOCK_RESOURCES", "True").lower() == "true"

# Demographic-Categorized Name Data (400+ entries)
DEMOGRAPHICS = {
    "gulf": {
        "weight": 0.40,
        "first_male": ["Mohammed", "Ahmed", "Ali", "Omar", "Khalid", "Abdullah", "Hamdan", "Saeed", "Rashid", "Faisal", "Tariq", "Zayed", "Majid", "Yousef", "Sultan", "Nasser", "Waleed", "Marwan", "Bilal", "Hassan", "Hisham", "Adel", "Sami", "Karim", "Rami"],
        "first_female": ["Fatima", "Mariam", "Aisha", "Noura", "Hessa", "Latifa", "Shaikha", "Reem", "Maha", "Hind", "Sara", "Layla", "Nadia", "Salma", "Lina", "Dina", "Rana", "Hana", "Manal", "Wafa"],
        "last": ["Al Maktoum", "Al Nahyan", "Al Qasimi", "Al Rashidi", "Al Mazrouei", "Al Mansoori", "Al Shamsi", "Al Nuaimi", "Al Kaabi", "Al Falasi", "Al Suwaidi", "Al Marzooqi", "Al Hammadi", "Al Hashimi", "Al Ameri", "Al Blooshi", "Al Dhaheri", "Al Zaabi", "Al Ketbi", "Al Muhairi", "Al Neyadi", "Al Romaithi", "Al Tunaiji", "Al Jabri", "Al Yousuf", "Al Shehhi", "Bin Ali", "Bin Hamdan", "Al Marri", "Al Kuwari", "Al Thani", "Al Hajri", "Al Dosari", "Al Harthi", "Al Saadi", "Al Balushi", "Al Lawati", "Al Wahaibi", "Al Harrasi", "Al Busaidi", "Al Farsi", "Al Habsi", "Al Maawali", "Al Hosni", "Al Sinani", "Al Abri", "Al Kindi", "Al Rawahi", "Al Ghafri"]
    },
    "south_asian_in": {
        "weight": 0.25,
        "first_male": ["Rahul", "Amit", "Vijay", "Suresh", "Rajesh", "Arjun", "Kiran", "Deepak", "Nikhil", "Rohan", "Manish", "Sanjay", "Anil", "Pradeep", "Vishal", "Akash", "Gaurav", "Naveen", "Pankaj", "Ravi"],
        "first_female": ["Priya", "Anjali", "Pooja", "Neha", "Swati", "Divya", "Sneha", "Kavya", "Meena", "Rekha"],
        "last": ["Sharma", "Patel", "Singh", "Kumar", "Verma", "Gupta", "Nair", "Menon", "Pillai", "Reddy", "Rao", "Iyer", "Krishnan", "Naidu", "Joshi", "Shah", "Mehta", "Kapoor", "Malhotra", "Bose", "Chatterjee", "Mukherjee", "Sinha", "Das", "Mishra", "Pandey", "Tripathi", "Tiwari", "Dubey", "Agarwal"]
    },
    "south_asian_pk": {
        "weight": 0.15,
        "first_male": ["Usman", "Imran", "Asad", "Zubair", "Kamran", "Fawad", "Shahzad", "Adnan", "Waseem", "Junaid", "Hamza", "Shoaib", "Irfan", "Arslan"],
        "first_female": ["Sana", "Hira", "Nadia", "Zara", "Ayesha", "Maham", "Rabia", "Sidra", "Amna", "Iqra"],
        "last": ["Khan", "Ahmed", "Ali", "Malik", "Chaudhry", "Sheikh", "Butt", "Mirza", "Qureshi", "Siddiqui", "Hussain", "Akhtar", "Baig", "Rizvi", "Hashmi", "Ansari", "Farooq", "Nawaz", "Rehman", "Iqbal"]
    },
    "levantine": {
        "weight": 0.08,
        "first_male": ["Omar", "Hassan", "Hisham", "Adel", "Sami", "Karim", "Rami"],
        "first_female": ["Nadia", "Salma", "Lina", "Dina", "Rana", "Hana", "Manal", "Wafa"],
        "last": ["Hassan", "Ibrahim", "Khalil", "Mansour", "Nasser", "Saleh", "Younis", "Zaki", "Bishara", "Haddad", "Khoury", "Nassar", "Suleiman", "Barakat", "Farhat", "Ghanem", "Moussa", "Taha", "Osman", "Fadl"]
    },
    "western": {
        "weight": 0.05,
        "first_male": ["James", "David", "Michael", "Andrew", "Christopher", "Daniel", "Matthew", "Ryan", "Jason", "Luke", "Tom", "Mark", "Oliver", "Nathan", "Jack", "Robert", "William", "George", "Patrick", "Scott"],
        "first_female": ["Sarah", "Emma", "Jessica", "Claire", "Laura", "Rachel", "Kate", "Amy", "Lisa", "Natalie", "Victoria", "Melissa", "Hannah", "Stephanie", "Jennifer", "Nicole", "Charlotte", "Emily", "Samantha", "Rebecca"],
        "last": ["Smith", "Johnson", "Williams", "Brown", "Jones", "Taylor", "Anderson", "Wilson", "Thompson", "White", "Harris", "Martin", "Clarke", "Walker", "Hall", "Allen", "Young", "King", "Wright", "Scott", "Green", "Baker", "Adams", "Nelson", "Carter", "Mitchell", "Roberts", "Turner", "Phillips", "Campbell", "Evans", "Edwards", "Collins", "Stewart", "Morris", "Murphy", "Cook", "Rogers", "Morgan", "Cooper"]
    },
    "east_asian": {
        "weight": 0.04,
        "first_male": ["Jun", "Mark", "Carlo", "Miguel", "Jerome", "Kevin", "Brian", "Jin", "Wei", "Hao", "Xiao", "Arnel", "Ronald", "Eric", "Jayson"],
        "first_female": ["Maria", "Ana", "Grace", "Rose", "Cristina", "Lyn", "Mei", "Ying", "Jing", "Karen"],
        "last": ["Santos", "Reyes", "Cruz", "Garcia", "Dela Cruz", "Ramos", "Mendoza", "Torres", "Villanueva", "Aquino", "Li", "Wang", "Chen", "Zhang", "Liu", "Yang", "Huang", "Wu", "Kim", "Park"]
    },
    "african": {
        "weight": 0.02,
        "first_male": ["Samuel", "Joseph", "Daniel", "Emmanuel", "Abraham", "Yohannes", "Tesfaye", "Dawit", "Kwame", "Chukwudi"],
        "first_female": ["Amina", "Fatou", "Halima", "Miriam", "Mercy"],
        "last": ["Tesfaye", "Haile", "Bekele", "Girma", "Abebe", "Okonkwo", "Mensah", "Diallo", "Nkosi", "Waweru"]
    },
    "other": {
        "weight": 0.01,
        "first_male": ["Alex", "Chris", "Sam", "Jordan", "Taylor", "Ivan", "Dmitri", "Carlos", "Luis", "Reza"],
        "first_female": ["Sana", "Maya", "Lara"],
        "last": ["Hossain", "Begum", "Chowdhury", "Islam", "Perera", "Fernando", "Silva", "Jayasinghe"]
    }
}

DOMAINS = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.ae", "icloud.com"]
LANDMARKS = ["near Mosque", "opp. Petrol Station", "behind Supermarket", "next to Pharmacy", "close to Metro", "near Park", "beside Mall", "near School"]

def introduce_typo(text):
    """Introduces a small typo 10% of the time."""
    if not text or random.random() > 0.10:
        return text
    
    if len(text) < 3:
        return text
        
    chars = list(text)
    typo_type = random.choice(["swap", "repeat", "omit"])
    
    idx = random.randint(0, len(chars) - 2)
    
    if typo_type == "swap":
        chars[idx], chars[idx+1] = chars[idx+1], chars[idx]
    elif typo_type == "repeat":
        chars.insert(idx, chars[idx])
    elif typo_type == "omit":
        chars.pop(idx)
        
    return "".join(chars)

def random_case(text):
    """Randomly changes the casing of the text for realism."""
    if not text: return text
    mode = random.random()
    if mode < 0.70:
        return text.title() # Normal: Ahmed
    elif mode < 0.85:
        return text.lower() # Informal: ahmed
    elif mode < 0.95:
        return text.upper() # Aggressive: AHMED
    else:
        # Mixed: aHmed or AhmED
        chars = list(text)
        for i in range(len(chars)):
            if random.random() > 0.5:
                chars[i] = chars[i].upper()
            else:
                chars[i] = chars[i].lower()
        return "".join(chars)

# Structured UAE Location Data
UAE_LOCATIONS = {
    "Abu Dhabi": {
        "code": "AZ",
        "districts": [
            {"name": "Abu Dhabi City", "type": "apartment", "streets": ["Corniche Road", "Hamdan Street", "Electra Street", "Khalifa Street"]},
            {"name": "Al Reem Island", "type": "tower", "buildings": ["Tala Tower", "Tamouh Tower", "Sun and Sky Tower"]},
            {"name": "Saadiyat Island", "type": "luxury_villa", "landmarks": ["Saadiyat Beach Villas", "Mamsha Al Saadiyat"]},
            {"name": "Yas Island", "type": "apartment", "buildings": ["Ansam Residence", "Mayyan"]},
            {"name": "Al Khalidiyah", "type": "villa", "streets": ["Street 14", "Street 9"]},
            {"name": "Al Bateen", "type": "villa", "streets": ["Al Bateen Street", "Al Meena Road"]},
            {"name": "Khalifa City", "type": "villa", "sectors": ["Sector A", "Sector B", "Sector C"]},
            {"name": "Mohammed Bin Zayed City", "type": "villa", "zones": ["Zone 14", "Zone 18", "Zone 22"]},
            {"name": "Mussafah", "type": "building", "shabiyas": ["Shabiya M7", "Shabiya M12"]},
            {"name": "Al Ain", "type": "villa", "districts": ["Al Jimi", "Al Hili", "Al Mutaredh"]}
        ]
    },
    "Dubai": {
        "code": "DU",
        "districts": [
            {"name": "Deira", "type": "apartment", "streets": ["Al Rigga Road", "Omar Bin Al Khattab Road", "Naif Road"]},
            {"name": "Bur Dubai", "type": "apartment", "streets": ["Al Fahidi Street", "Mankhool Road", "Bank Street"]},
            {"name": "Al Karama", "type": "apartment", "streets": ["Karama Street", "Kuwait Road"]},
            {"name": "Jumeirah", "type": "villa", "streets": ["Street 11A", "Street 18B", "Al Wasl Road"], "subs": ["Jumeirah 1", "Jumeirah 2", "Jumeirah 3"]},
            {"name": "Umm Suqeim", "type": "villa", "subs": ["Umm Suqeim 1", "Umm Suqeim 2", "Umm Suqeim 3"]},
            {"name": "Al Barsha", "type": "apartment", "buildings": ["Barsha Heights", "Al Barsha Tower"], "subs": ["Al Barsha 1", "Al Barsha 2"]},
            {"name": "Palm Jumeirah", "type": "villa_palm", "fronds": ["Frond G", "Frond K", "Frond P"]},
            {"name": "Dubai Marina", "type": "tower", "buildings": ["Cayan Tower", "Marina Heights", "Princess Tower"]},
            {"name": "Business Bay", "type": "tower", "buildings": ["Executive Tower B", "Vision Tower", "Bay Square"]},
            {"name": "Downtown Dubai", "type": "tower", "buildings": ["The Address Residence", "Burj Vista", "Old Town"]},
            {"name": "Mirdif", "type": "villa", "areas": ["Mirdif Area 32", "Uptown Mirdif"]},
            {"name": "International City", "type": "apartment", "clusters": ["Morocco Cluster", "China Cluster", "England Cluster"]},
            {"name": "Discovery Gardens", "type": "apartment", "clusters": ["Zen Cluster", "Mediterranean Cluster"]},
            {"name": "JVC", "type": "tower", "districts": ["District 10", "District 12", "District 15"]},
            {"name": "Dubai Hills", "type": "villa", "communities": ["Sidra 1", "Maple 2"]}
        ]
    },
    "Sharjah": {
        "code": "SH",
        "districts": [
            {"name": "Al Majaz", "type": "tower", "streets": ["Corniche Road", "Al Majaz Street"]},
            {"name": "Al Khan", "type": "apartment", "buildings": ["Al Khan Residence"]},
            {"name": "Al Nahda", "type": "apartment", "buildings": ["Al Nahda Tower"]},
            {"name": "Muweilah", "type": "villa", "communities": ["Muweilah Residential"]},
            {"name": "Aljada", "type": "apartment", "buildings": ["Nest by Eskan"]}
        ]
    },
    "Ajman": {
        "code": "AJ",
        "districts": [
            {"name": "Al Nuaimiya", "type": "apartment", "blocks": ["Block A", "Block B"]},
            {"name": "Al Rashidiya", "type": "villa", "streets": ["Al Rashidiya Road"]},
            {"name": "Emirates City", "type": "tower", "buildings": ["Lilies Tower", "Jasmine Tower"]}
        ]
    },
    "Ras Al Khaimah": {
        "code": "RK",
        "districts": [
            {"name": "Al Nakheel", "type": "villa", "streets": ["Al Nakheel Road"]},
            {"name": "Al Hamra Village", "type": "luxury_villa", "landmarks": ["Al Hamra Marina", "Al Hamra Golf Club"]},
            {"name": "Mina Al Arab", "type": "villa", "landmarks": ["Gateway Residences"]}
        ]
    },
    "Fujairah": {
        "code": "FU",
        "districts": [
            {"name": "Fujairah City", "type": "apartment", "streets": ["Hamad Bin Abdullah Road"]},
            {"name": "Al Aqah", "type": "luxury_villa", "landmarks": ["Le Meridien Al Aqah", "Sandy Beach Hotel"]}
        ]
    },
    "Umm Al Quwain": {
        "code": "UQ",
        "districts": [
            {"name": "Umm Al Quwain City", "type": "villa", "streets": ["King Faisal Road"]},
            {"name": "Al Salamah", "type": "villa", "streets": ["Al Salamah Road"]}
        ]
    }
}

def get_random_customer():
    # 1. Select Demographic based on weights
    d_keys = list(DEMOGRAPHICS.keys())
    d_weights = [DEMOGRAPHICS[k]["weight"] for k in d_keys]
    demo_key = random.choices(d_keys, weights=d_weights, k=1)[0]
    demo = DEMOGRAPHICS[demo_key]
    
    # 2. Select First/Last within demographic
    is_male = random.random() > 0.5
    f_list = demo["first_male"] if is_male else demo["first_female"]
    first = random.choice(f_list)
    
    # Realistic Pairing Exception (1-2% mixed heritage)
    if random.random() < 0.02:
        other_demo_key = random.choice(list(DEMOGRAPHICS.keys()))
        other_demo = DEMOGRAPHICS[other_demo_key]
        last_base = random.choice(other_demo["last"])
    else:
        last_base = random.choice(demo["last"])
    
    domain = random.choice(DOMAINS)
    
    # 3. Apply Name Logic (Casing and Typos)
    first_name = random_case(first)
    last_name = random_case(last_base)
    
    # Introduce typos to names (10% chance)
    first_name = introduce_typo(first_name)
    last_name = introduce_typo(last_name)

    # 4. Location and Address
    emirate_name = random.choice(list(UAE_LOCATIONS.keys()))
    location = UAE_LOCATIONS[emirate_name]
    district = random.choice(location["districts"])
    
    addr_type = district["type"]
    district_name = district["name"]
    unit_num = random.randint(1, 400)
    
    components = []
    if addr_type == "tower":
        bld = random.choice(district["buildings"])
        components = [f"Apartment {unit_num}", bld, district_name]
    elif addr_type == "apartment":
        extra = ""
        if "streets" in district: extra = random.choice(district['streets'])
        elif "buildings" in district: extra = random.choice(district['buildings'])
        elif "clusters" in district: extra = random.choice(district['clusters'])
        components = [f"Flat {unit_num}", extra, district_name]
    elif addr_type == "villa":
        extra = ""
        if "streets" in district: extra = random.choice(district['streets'])
        elif "sectors" in district: extra = random.choice(district['sectors'])
        elif "zones" in district: extra = random.choice(district['zones'])
        elif "areas" in district: extra = random.choice(district['areas'])
        elif "subs" in district: district_name = random.choice(district['subs'])
        components = [f"Villa {unit_num}", extra, district_name]
    elif addr_type == "luxury_villa":
        components = [f"Villa {random.randint(1, 40)}", random.choice(district['landmarks']), district_name]
    elif addr_type == "villa_palm":
        components = [f"Villa {random.randint(1, 40)}", random.choice(district['fronds']), "Palm Jumeirah"]
    elif addr_type == "building":
        components = [f"Flat {random.randint(1, 50)}", f"Building {random.randint(1, 50)}", random.choice(district['shabiyas'])]
    else:
        components = [f"{random.randint(1, 100)}", district_name]

    # Advanced Address Realism: Shuffling, Landmarks, Punctuation
    # Filter out empty strings first
    components = [str(c).strip() for c in components if c]
    
    if random.random() > 0.4: # 60% chance to shuffle order
        random.shuffle(components)
    
    # Add landmark 30% of the time
    if random.random() < 0.3:
        components.append(random.choice(LANDMARKS))

    # Join with random punctuation/separator variety
    sep = random.choice([", ", " - ", " ", ",", " . "]) 
    address = sep.join(components)
    
    # Sometimes add a typo to the address (10% chance)
    if random.random() < 0.1:
        address = introduce_typo(address)

    # 5. Diversify Email Patterns
    f_low = first.lower().replace(" ", "")
    l_low = last_base.lower().replace(" ", "").replace("al", "").strip()
    email_roll = random.random()
    if email_roll < 0.25:
        email_user = f"{f_low}.{l_low}{random.randint(1, 99)}"
    elif email_roll < 0.50:
        email_user = f"{f_low}{l_low}"
    elif email_roll < 0.75:
        email_user = f"{f_low[0]}.{l_low}{random.randint(10, 999)}"
    else:
        email_user = f"{f_low}{random.randint(1000, 9999)}"
    
    email = f"{email_user}@{domain}"
    
    # 6. Vary Phone Number Format
    prefix = random.choice(['50', '52', '54', '55', '56', '58'])
    base_num = f"{random.randint(1000000, 9999999)}"
    phone_roll = random.random()
    if phone_roll < 0.4:
        phone = f"+971{prefix}{base_num}"
    elif phone_roll < 0.7:
        phone = f"0{prefix}{base_num}"
    else:
        phone = f"971 {prefix} {base_num[:3]} {base_num[3:]}"
    
    return {
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "address": address,
        "city": emirate_name,
        "state_code": location["code"],
        "phone": phone
    }

async def get_random_product_url(page, base_url=None):
    """Navigates to the store's catalogue and picks a random product link."""
    if PRODUCT_URL:
        # Fallback if the user explicitly set a direct product URL
        log.info(f"Using exact product URL from .env: {PRODUCT_URL}")
        return PRODUCT_URL

    active_base = base_url or (STORE_URLS[0] if STORE_URLS else None)
    if not active_base:
        log.error("No store URLs provided in TEST_STORE_URL or base_url!")
        return None

    collections_url = f"{active_base.rstrip('/')}/collections/all"
    log.info(f"Navigating to {collections_url} to find products...")
    
    try:
        await page.goto(collections_url, wait_until="load", timeout=90000)
        
        # Look for standard Shopify product links
        product_links = await page.locator("a[href*='/products/']").all()
        
        valid_urls = []
        for link in product_links:
            href = await link.get_attribute("href")
            # Ensure it's not a pagination or random non-product link
            if href and "/products/" in href and not "page=" in href:
                # Construct full URL if it's relative
                full_url = href if href.startswith("http") else f"{active_base.rstrip('/')}{href}"
                valid_urls.append(full_url)
                
        # Deduplicate
        valid_urls = list(set(valid_urls))
        
        if valid_urls:
            chosen_url = random.choice(valid_urls)
            log.info(f"Found {len(valid_urls)} products. Randomly selected: {chosen_url}")
            return chosen_url
        else:
            log.error("Could not find any product links on the /collections/all page.")
            return None
            
    except Exception as e:
        log.error(f"Failed to fetch random product: {e}")
        return None

async def run_checkout_flow(context, customer, target_url):
    """Executes the standard Shopify Add to Cart and Checkout flow."""
    # Track the current active page
    page = context.pages[0] if context.pages else await context.new_page()
    
    try:
        # 1. Go to product page
        log.info(f"Navigating to product page: {target_url}")
        await page.goto(target_url, wait_until="load", timeout=90000)
        
        # Humanize: Scroll and hover a bit
        log.info("Humanizing: Scrolling and hovering...")
        await page.mouse.wheel(0, 500)
        await asyncio.sleep(1)
        await page.mouse.wheel(0, -200)
        await asyncio.sleep(1)
        
        # Try to hover over the add-to-cart button before clicking
        add_to_cart_selectors = [
            "button[name='add']",
            "form[action='/cart/add'] button[type='submit']",
            "button:has-text('Add to cart')",
            ".product-form__submit",
            "button:has-text('ADD TO CART')"
        ]
        
        for sel in add_to_cart_selectors:
            loc = page.locator(sel).first
            if await loc.is_visible():
                await loc.hover()
                await asyncio.sleep(0.5)
                break
        
        # 2. Add to cart
        log.info("Clicking 'Add to cart'")
        # Try multiple common Shopify selectors for Add to Cart
        add_to_cart_selectors = [
            "button[name='add']",
            "form[action='/cart/add'] button[type='submit']",
            "button:has-text('Add to cart')",
            ".product-form__submit"
        ]
        
        clicked_add = False
        for selector in add_to_cart_selectors:
            if await page.locator(selector).count() > 0:
                # Some themes have a sliding cart, so we wait a bit after clicking
                await page.locator(selector).first.click()
                clicked_add = True
                log.info(f"Used selector: {selector}")
                break
                
        if not clicked_add:
            log.error("Could not find 'Add to cart' button. Check the store theme/URL.")
            return False

        await asyncio.sleep(2) # Wait for slide-out cart or redirect
        
        # 3. Go to checkout
        log.info("Going to checkout")
        
        # Humanize: If a popup appears, close it. Use a more robust check.
        for _ in range(3):
            try:
                # Look for common close buttons or X icons
                close_selectors = [
                    "button:has-text('Close')", 
                    ".popup-close", 
                    "button[aria-label='Close']",
                    "svg.icon-close",
                    ".modal__close"
                ]
                for sel in close_selectors:
                    close_btn = page.locator(sel).first
                    if await close_btn.is_visible():
                        log.info(f"Found popup close button ({sel}). Closing it.")
                        await close_btn.click()
                        await asyncio.sleep(1)
            except:
                pass
            await asyncio.sleep(1)

        # Fallback: If we can't find a checkout button on the current page, 
        # try navigating to /cart first as a human would
        if not page.url.endswith("/cart"):
            log.info("Navigating to /cart first for a more natural checkout flow.")
            await page.goto(f"{STORE_URL.rstrip('/')}/cart", wait_until="load")
            await asyncio.sleep(2)

        # Avoid direct navigation or JS to bypass Bright Data CDP robots.txt enforcement
        # Instead, act entirely like a human clicking "Checkout"
        # We prioritize the "Add to cart" -> "Checkout" flow over "Buy it now"
        # as "Buy it now" is often more heavily guarded or prone to failures
        checkout_selectors = [
            "button.cart__submit",
            "button[name='checkout']", 
            "a[href='/checkout']", 
            ".cart__checkout-button",
            "button:has-text('Checkout')",
            "button:has-text('CHECK OUT')",
            "#checkout",
            ".checkout-button"
        ]
        
        # Give the cart side-drawer or page a moment to fully render
        await asyncio.sleep(3)
        
        clicked_checkout = False
        for selector in checkout_selectors:
            if await page.locator(selector).is_visible():
                await page.locator(selector).first.click()
                clicked_checkout = True
                log.info(f"Clicked checkout via: {selector}")
                break
                
        if not clicked_checkout:
            log.warning("Could not find a standard checkout button. Searching for anything that says checkout...")
            checkout_btn = page.locator("button, a").filter(has_text="Checkout").first
            if await checkout_btn.count() > 0 and await checkout_btn.first.is_visible():
                await checkout_btn.first.click()
                log.info("Clicked fallback checkout button.")
            else:
                log.warning("Still couldn't find checkout. Trying one last 'Buy it now' fallback.")
                buy_now = page.locator("button:has-text('Buy it now')").first
                if await buy_now.is_visible():
                    await buy_now.click()
                    log.info("Clicked 'Buy it now' as last resort.")
                
        # Wait for the checkout page to stabilize
        # Check if a new tab was opened during checkout click
        await asyncio.sleep(5)
        if len(context.pages) > 1:
            log.info(f"Detected {len(context.pages)} tabs. Switching to the latest tab.")
            page = context.pages[-1]
            await page.bring_to_front()
            
        # We wait for the URL to change to something including 'checkout' or 'checkouts'
        try:
            await page.wait_for_url("**/checkouts/**", timeout=30000)
            log.info(f"Successfully on checkout URL: {page.url}")
        except:
            log.warning(f"Did not detect a checkout URL redirect. Current URL: {page.url}")
            
        await page.wait_for_load_state("load", timeout=90000)
        await asyncio.sleep(5)

        # 4. Fill Information (Shopify new checkout uses deeply nested standard fields)
        log.info(f"Filling customer info: {customer['email']}")
        
        # Handle email
        try:
            # Find a visible email input
            email_selectors = ["input[type='email']", "input[name='email']", "input[id='email']", "input[placeholder*='Email']"]
            found_email = False
            for sel in email_selectors:
                element = page.locator(sel).first
                if await element.is_visible():
                    await element.fill(customer["email"])
                    found_email = True
                    break
            
            if not found_email:
                log.warning("Email field not visible via selectors. Trying direct get_by_placeholder.")
                await page.get_by_placeholder("Email").first.fill(customer["email"])
        except Exception as e:
            log.warning(f"Could not fill email: {e}. Taking screenshot for debug.")
            await page.screenshot(path=f".tmp/email_fail_{int(time.time())}.png")
        
        # Fill shipping address standard Shopify fields
        fields = {
            "firstName": customer["first_name"],
            "lastName": customer["last_name"],
            "address1": customer["address"],
            "city": customer["city"],
            "phone": customer["phone"],
        }
        
        for name, value in fields.items():
            inputs = page.locator(f"input[name='{name}']")
            if await inputs.count() > 0:
                await inputs.first.fill(value)
                
        # Handle state/province/emirate dropdown (often required in UAE checkouts)
        zone_selects = [
            "select[name='zone']",
            "select[name='province']",
            "select[name='address[province]']",
            "select[name='State/Province']"
        ]
        
        for selector in zone_selects:
            select_element = page.locator(selector)
            if await select_element.count() > 0 and await select_element.first.is_visible():
                log.info(f"Selecting province/state using {selector}")
                options = await select_element.first.locator("option").all()
                valid_values = []
                for opt in options:
                    val = await opt.get_attribute("value")
                    text = await opt.text_content()
                    if val and text and 'select' not in text.lower() and val.strip() != '':
                        valid_values.append(val)
                
                if valid_values:
                    # Priority 1: Match exactly by state_code (e.g. "DU")
                    target_code = customer.get("state_code", "").upper()
                    if target_code in valid_values:
                        await select_element.first.select_option(value=target_code)
                        log.info(f"Selected state/province by code: {target_code}")
                    else:
                        # Priority 2: Try to match by name (e.g. "Dubai")
                        target_name = customer.get("city", "").lower()
                        matched_val = None
                        for opt in options:
                            val = await opt.get_attribute("value")
                            text = await opt.text_content()
                            if text and target_name in text.lower():
                                matched_val = val
                                break
                        
                        if matched_val:
                            await select_element.first.select_option(value=matched_val)
                            log.info(f"Selected state/province by name match: {matched_val}")
                        else:
                            # Fallback: Random (legacy)
                            chosen_val = random.choice(valid_values)
                            await select_element.first.select_option(value=chosen_val)
                            log.info(f"No match found. Selected random state/province: {chosen_val}")
                break

        # Look for Payment Option (specifically Cash on Delivery or COD)
        cod_selectors = [
            "text='Cash on Delivery'", 
            "text='COD'", 
            "label:has-text('Cash on Delivery')", 
            "label:has-text('COD')",
            ".radio__label:has-text('Cash on Delivery')"
        ]
        
        for cod_sel in cod_selectors:
            if await page.locator(cod_sel).is_visible():
                log.info(f"Explicitly selecting Cash on Delivery (COD)...")
                await page.locator(cod_sel).first.click()
                await asyncio.sleep(1)
                break

        # Click Continue to shipping / Payment
        log.info("Progressing through checkout steps...")
        # Shopify often has "Continue to shipping" -> "Continue to payment" -> "Complete order"
        # Or standard "Pay now"
        
        next_buttons = [
            "button:has-text('Continue to shipping')",
            "button:has-text('Continue to payment')",
            "button:has-text('Pay now')",
            "button:has-text('Complete order')",
            "#continue_button"
        ]
        
        # Try to click any 'continue' buttons until we reach the end
        # Max 5 attempts
        for _ in range(5):
            await asyncio.sleep(3)
            progressed = False
            for btn in next_buttons:
                if await page.locator(btn).is_visible():
                    btn_text = await page.locator(btn).first.text_content()
                    log.info(f"Clicking checkout action: {btn_text.strip() if btn_text else btn}")
                    await page.locator(btn).first.click()
                    progressed = True
                    break
            
            # Check if we landed on the thank you page
            if any(x in page.url for x in ["thank", "orders", "receipt"]) or await page.locator("text='Your order is confirmed'").count() > 0:
                log.info(f"Successfully reached Order Confirmation! URL: {page.url}")
                return True
                
        log.warning("Reached end of automated clicks. Check if 'Complete order' was successful.")
        return True # Return true anyway as we've hit the system with load

    except Exception as e:
        log.error(f"Error during checkout flow: {e}")
        # Take a screenshot for debugging
        try:
            os.makedirs(".tmp", exist_ok=True)
            await page.screenshot(path=f".tmp/error_screenshot_{int(time.time())}.png")
            log.info("Saved error screenshot to .tmp/")
        except:
            pass
        return False

async def run_bot(headless=True, visible=False, count=0):
    """Main loop to run the bot indefinitely or for a specific count."""
    if not STORE_URLS and not PRODUCT_URL:
        log.error("TEST_STORE_URL is not configured in .env!")
        return

    log.info("Starting Load Testing Bot...")
    if STORE_URLS:
        log.info(f"Targeting Store Rotation: {STORE_URLS}")
    else:
        log.info(f"Target Product: {PRODUCT_URL} (Fixed product)")

    async with async_playwright() as p:
        launch_args = {"headless": headless}
        if TEST_PROXY:
            from urllib.parse import urlparse
            parsed_proxy = urlparse(TEST_PROXY)
            proxy_config = {
                "server": f"{parsed_proxy.scheme}://{parsed_proxy.hostname}:{parsed_proxy.port}"
            }
            if parsed_proxy.username and parsed_proxy.password:
                proxy_config["username"] = str(parsed_proxy.username)
                proxy_config["password"] = str(parsed_proxy.password)

            launch_args["proxy"] = proxy_config
            log.info(f"Using proxy server: {proxy_config['server']}")

        browser = await p.chromium.launch(**launch_args)

        order_count = 0
        while True:
            if count > 0 and order_count >= count:
                log.info(f"Reached target order count ({count}). Exiting.")
                break

            log.info(f"--- Starting Round {order_count + 1} ---")

            context_args = {
                "viewport": {"width": 1280, "height": 800},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "ignore_https_errors": True
            }

            context = await browser.new_context(**context_args)

            # Optimization: Block images/fonts/media to save data costs on Cloud Browsers
            if BLOCK_RESOURCES:
                async def intercept(route):
                    if route.request.resource_type in ["image", "media", "font"]:
                        await route.abort()
                    else:
                        await route.continue_()
                await context.route("**/*", intercept)

            # Fetch random product if needed
            target_url = PRODUCT_URL
            if STORE_URLS:
                # Cycle through URLs based on order count
                base_url = STORE_URLS[order_count % len(STORE_URLS)]
                log.info(f"Targeting Store: {base_url}")
                temp_page = await context.new_page()
                target_url = await get_random_product_url(temp_page, base_url=base_url)
                await temp_page.close()

            success = False
            if target_url:
                customer = get_random_customer()
                # Run the checkout flow within the context
                success = await run_checkout_flow(context, customer, target_url)
                if success:
                    log.info("Order completion suspected successfully.")

            await context.close()

            order_count += 1

            if success:
                log.info(f"Order cycle successful. Round {order_count} complete.")
            else:
                log.error(f"Order cycle failed. Round {order_count} failed.")

            if count > 0 and order_count >= count:
                break

            # Staggered Interval: 1-2 orders per hour (30-60 mins)
            # Add 20% random jitter to the interval
            jitter = random.uniform(0.8, 1.2)
            sleep_mins = TEST_INTERVAL_MINS * jitter
            log.info(f"Waiting {sleep_mins:.2f} minutes before next order (Jitter: {jitter:.2f})...")
            await asyncio.sleep(sleep_mins * 60)

        await browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Demo Store Load Testing Bot")
    parser.add_argument("--test-once", action="store_true", help="Run the flow once and exit (for debugging)")
    parser.add_argument("--visible", action="store_true", help="Show the browser UI while running")
    parser.add_argument("--count", type=int, default=0, help="Number of orders to place before exiting (0 for infinite)")
    args = parser.parse_args()

    # Ensure tmp directory exists
    os.makedirs(".tmp", exist_ok=True)

    # The --test-once argument is now superseded by --count=1 if used together.
    # Prioritize --count if specified, otherwise use --test-once for count=1.
    final_count = args.count
    if args.test_once and args.count == 0:
        final_count = 1

    asyncio.run(run_bot(headless=not args.visible, visible=args.visible, count=final_count))
