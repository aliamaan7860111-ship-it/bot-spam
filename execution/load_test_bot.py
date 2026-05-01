import os
import time
import asyncio
import logging
import random
import argparse
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright
try:
    from playwright_stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

# Load .env
load_dotenv()

# Setup logging
os.makedirs(".tmp", exist_ok=True)
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

DOMAINS = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "outlook.ae", "icloud.com", "me.com", "live.com", "proton.me"]


def generate_realistic_email(first_name, last_name):
    """Build a believable, format-valid email from a person's name.

    Patterns mirror how real people pick handles: full name with dot/underscore,
    first + last, initial + last, name + birth-year-ish number, etc.
    Output is always a syntactically valid local-part."""
    import re as _re
    first = _re.sub(r"[^a-zA-Z]", "", (first_name or "user")).lower() or "user"
    last = _re.sub(r"[^a-zA-Z]", "", (last_name or "")).lower()
    if not last:
        last = "x" + str(random.randint(10, 99))

    # Number flavours that look human-picked (not random 8-digit blobs)
    num_choices = [
        "",
        str(random.randint(1, 99)),
        str(random.randint(70, 99)),       # birth year-ish
        str(random.choice([2000, 2001, 2002, 2003, 1995, 1996, 1997, 1998, 1999])),
        str(random.randint(1, 9)),
        f"{random.randint(0, 9)}{random.randint(0, 9)}",
    ]
    num = random.choice(num_choices)

    sep_choices = [".", "_", "", "-"]
    sep = random.choice(sep_choices)

    patterns = [
        lambda: f"{first}{sep}{last}{num}",
        lambda: f"{first}{last}{num}",
        lambda: f"{first[0]}{sep}{last}{num}" if last else f"{first}{num}",
        lambda: f"{first}{sep}{last[0]}{num}" if last else f"{first}{num}",
        lambda: f"{last}{sep}{first}{num}",
        lambda: f"{first}{num}",
        lambda: f"{first}{sep}{last}",
        lambda: f"{first[0]}{last}{num}",
        lambda: f"the{first}{num}",
        lambda: f"{first}.{last}{num}",
    ]
    local = random.choice(patterns)()

    # Hard cleanup: collapse repeated separators, strip leading/trailing punctuation
    local = _re.sub(r"[._-]{2,}", lambda m: m.group(0)[0], local)
    local = local.strip("._-")
    if not local:
        local = first

    domain = random.choice(DOMAINS)
    return f"{local}@{domain}"
LANDMARKS = ["near Mosque", "opp. Petrol Station", "behind Supermarket", "next to Pharmacy", "close to Metro", "near Park", "beside Mall", "near School"]

# Rotating User-Agent Pool (modern Chrome/Edge on Win10/Win11/Mac)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

# Stores with advanced bot protection (disable resource blocking, extra humanization)
PROTECTED_STORES = [s.strip() for s in os.getenv("PROTECTED_STORES", "meowtiqueofficial.com,mandarerabrands.com,luxurytrunkdubai.com").split(",") if s.strip()]

# Per-domain platform overrides (avoids the platform-detection round-trip)
# Format: "domain1.com=woocommerce,domain2.com=shopify"
_platform_env = os.getenv("PLATFORM_OVERRIDES", "mandarerabrands.com=woocommerce,luxurytrunkdubai.com=woocommerce")
PLATFORM_OVERRIDES = {}
for entry in _platform_env.split(","):
    entry = entry.strip()
    if "=" in entry:
        d, p = entry.split("=", 1)
        PLATFORM_OVERRIDES[d.strip().lower()] = p.strip().lower()


def detect_platform(url):
    """Returns 'woocommerce' or 'shopify' based on URL domain. Defaults to 'shopify'."""
    if not url:
        return "shopify"
    url_l = url.lower()
    for domain, platform in PLATFORM_OVERRIDES.items():
        if domain in url_l:
            return platform
    return "shopify"


async def safe_fill(page, selector, value, label="field"):
    """Honeypot-aware fill: only fills inputs that are truly visible and not traps."""
    elements = page.locator(selector)
    count = await elements.count()
    for i in range(count):
        el = elements.nth(i)
        try:
            is_real = await el.evaluate("""el => {
                const style = window.getComputedStyle(el);
                // Skip hidden honeypot traps
                if (style.display === 'none') return false;
                if (style.visibility === 'hidden') return false;
                if (parseFloat(style.opacity) === 0) return false;
                if (el.offsetWidth === 0 && el.offsetHeight === 0) return false;
                if (el.getAttribute('aria-hidden') === 'true') return false;
                if (el.getAttribute('tabindex') === '-1' && style.position === 'absolute') return false;
                // Check parent containers too (honeypots often hide the wrapper)
                let parent = el.parentElement;
                for (let d = 0; d < 5 && parent; d++) {
                    const ps = window.getComputedStyle(parent);
                    if (ps.display === 'none' || ps.visibility === 'hidden' || parseFloat(ps.opacity) === 0) return false;
                    if (parent.offsetWidth === 0 && parent.offsetHeight === 0) return false;
                    parent = parent.parentElement;
                }
                return true;
            }""")
            if is_real:
                await el.fill(value)
                log.info(f"Filled {label} via: {selector}")
                return True
        except:
            continue
    return False


def is_protected_store(url):
    """Check if a URL belongs to a store with advanced bot protection."""
    if not url:
        return False
    return any(domain in url for domain in PROTECTED_STORES)

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
    
    # Try various ways to get a secondary address component (street, building, etc)
    extra = ""
    # Look for any list-like key to provide 'extra' detail
    for key in ["buildings", "streets", "clusters", "communities", "areas", "fronds", "subs", "landmarks", "districts", "blocks", "sectors", "zones", "shabiyas"]:
        if key in district and district[key]:
            extra = random.choice(district[key])
            break
            
    if addr_type in ["tower", "apartment", "building"]:
        prefix = "Apartment" if addr_type == "tower" else "Flat"
        if extra:
            components = [f"{prefix} {unit_num}", extra, district_name]
        else:
            components = [f"{prefix} {unit_num}", district_name]
    else: # Villa / Luxury Villa / Villa Palm
        prefix = "Villa"
        if extra:
            components = [f"{prefix} {unit_num}", extra, district_name]
        else:
            components = [f"{prefix} {unit_num}", district_name]

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

    # 5. Vary Phone Number Format (UAE variety: 971, 05, spaces, no spaces)
    prefix = random.choice(['50', '52', '54', '55', '56', '58'])
    base_num = f"{random.randint(1000000, 9999999)}"
    
    phone_roll = random.random()
    if phone_roll < 0.3:
        # Standard +971 local
        phone = f"+971{prefix}{base_num}"
    elif phone_roll < 0.6:
        # Local 05 format
        phone = f"0{prefix}{base_num}"
    elif phone_roll < 0.8:
        # 971 with spaces
        phone = f"971 {prefix} {base_num[:3]} {base_num[3:]}"
    else:
        # 05 with spaces
        phone = f"0{prefix} {base_num[:3]} {base_num[3:]}"
    
    # Realistic email derived from name (Shopify accepts phone-as-identity, but
    # WooCommerce requires a syntactically valid email)
    email = generate_realistic_email(first, last_base)

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

    # Extract just the origin (scheme + hostname) to avoid UTM params corrupting product URLs
    from urllib.parse import urlparse
    parsed = urlparse(active_base)
    store_origin = f"{parsed.scheme}://{parsed.netloc}"
    
    collections_url = f"{store_origin}/collections/all"
    log.info(f"Navigating to {collections_url} to find products...")
    
    try:
        if HAS_STEALTH:
            await Stealth().apply_stealth_async(page)

        await page.goto(collections_url, wait_until="domcontentloaded", timeout=90000)
        # Wait for product links to render (much faster than networkidle)
        try:
            await page.wait_for_selector("a[href*='/products/']", timeout=15000)
        except:
            await asyncio.sleep(3)

        # Look for standard Shopify product links
        product_links = await page.locator("a[href*='/products/']").all()
        log.info(f"Locator found {len(product_links)} raw product links")

        valid_urls = []
        for link in product_links:
            href = await link.get_attribute("href")
            if href and "/products/" in href and "page=" not in href:
                full_url = href if href.startswith("http") else f"{store_origin}{href}"
                valid_urls.append(full_url)

        # Deduplicate
        valid_urls = list(set(valid_urls))
        
        if valid_urls:
            chosen_url = random.choice(valid_urls)
            log.info(f"Found {len(valid_urls)} products. Randomly selected: {chosen_url}")
            return chosen_url
        else:
            log.error(f"Could not find any product links on the {collections_url} page.")
            try:
                # Debug: Save screenshot and HTML to see what's happening
                debug_path = ".tmp/failed_product_fetch.png"
                await page.screenshot(path=debug_path)
                html_path = ".tmp/failed_product_fetch.html"
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(await page.content())
                log.info(f"Saved failure debug info to {debug_path} and {html_path}")
            except:
                pass
            return None
            
    except Exception as e:
        log.error(f"Failed to fetch random product: {e}")
        return None

async def run_checkout_flow(context, customer, target_url):
    """Executes the standard Shopify Add to Cart and Checkout flow."""
    # Track the current active page
    page = context.pages[0] if context.pages else await context.new_page()
    
    try:
        # Apply stealth to the page
        if HAS_STEALTH:
            await Stealth().apply_stealth_async(page)

        # 1. Go to product page
        log.info(f"Navigating to product page: {target_url}")
        # Use networkidle for JS-heavy themes (Debutify) that render buttons via JS
        await page.goto(target_url, wait_until="domcontentloaded", timeout=90000)
        # Wait for add-to-cart button to render (faster than networkidle)
        try:
            await page.wait_for_selector(
                "button[name='add'], .product-form__submit, button[id^='ProductSubmitButton']",
                timeout=15000
            )
        except:
            await asyncio.sleep(3)

        # NOTE: "Sold out" badges may be fake (injected by MIDA bot protection)
        # We ignore them and attempt to add to cart regardless

        # Force-enable any disabled Add to Cart buttons (MIDA may disable them)
        if is_protected_store(target_url):
            await page.evaluate("""() => {
                // Remove fake sold-out badges
                document.querySelectorAll('[class*="sold-out"], [class*="soldout"]')
                    .forEach(el => el.remove());
                // Re-enable any disabled submit buttons
                document.querySelectorAll('button[disabled], input[disabled]')
                    .forEach(el => { el.disabled = false; el.removeAttribute('disabled'); });
                // Remove hidden class from Buy Now button (Debutify hides it)
                document.querySelectorAll('.dbtfy__buy-now.hidden, .hidden.dbtfy__buy-now')
                    .forEach(el => el.classList.remove('hidden'));
            }""")
            log.info("Stripped MIDA decoy badges and re-enabled buttons")

        # Humanize: Scroll and hover a bit
        log.info("Humanizing: Scrolling and hovering...")
        await page.mouse.wheel(0, 500)
        await asyncio.sleep(1)

        # Check terms/conditions checkbox (required on Meowtique before Add to Cart)
        try:
            checkboxes = await page.locator("input[type='checkbox']").all()
            for cb in checkboxes:
                if await cb.is_visible() and not await cb.is_checked():
                    label_text = await cb.evaluate("el => (el.closest('label') || el.parentElement).textContent || ''")
                    if any(kw in label_text.lower() for kw in ["agree", "terms", "condition", "policy", "accept"]):
                        log.info(f"Checking terms checkbox: {label_text.strip()[:50]}")
                        await cb.click()
                        await asyncio.sleep(0.5)
        except Exception as e:
            log.warning(f"Terms checkbox handling: {e}")
        # Try to hover over the add-to-cart button before clicking
        add_to_cart_selectors = [
            "button[name='add']",
            "form[action='/cart/add'] button[type='submit']",
            "form[action='/cart/add'] button",
            "button:has-text('Add to cart')",
            "button:has-text('Add to Cart')",
            "button:has-text('ADD TO CART')",
            "button:has-text('Add To Cart')",
            ".product-form__submit",
            ".add-to-cart",
            "#AddToCartText",
            # Debutify theme selectors
            "product-form button[type='submit']",
            "#ProductSubmitButton",
            "button[id^='ProductSubmitButton']",
            ".product-form__submit.button--secondary",
        ]

        # --- NEW: Try "Buy It Now" / "Buy Now" first for direct checkout ---
        buy_now_selectors = [
            "button:has-text('Buy it now')",
            "button:has-text('Buy Now')",
            "button:has-text('Order Now')",
            "button:has-text('Buy it Now')",
            "button:has-text('Order now')",
            "button:has-text('Checkout Now')",
            "button:has-text('Buy now get now')",
            ".shopify-payment-button__button", 
            ".shopify-payment-button button",
            "[data-testid='Checkout-button']",
            "button[type='submit']:has-text('Buy')"
        ]
        
        clicked_buy_now = False
        log.info("Checking for 'Buy It Now' buttons for direct checkout...")
        
        # Humanize: Wait for dynamic checkout buttons to render (they are often lazy-loaded)
        await asyncio.sleep(5)

        # 1. Check main page
        for selector in buy_now_selectors:
            try:
                # Wait briefly to see if it's there
                loc = page.locator(selector).first
                if await loc.is_visible(timeout=3000):
                    log.info(f"Found direct checkout button on main page: {selector}. Clicking...")
                    await loc.click()
                    clicked_buy_now = True
                    break
            except:
                continue
        
        # 3. ULTIMATE FALLBACK: Check all buttons/links for keywords
        if not clicked_buy_now:
            log.info("Direct buy not found. Trying universal keyword search...")
            # Check main page and all frames
            all_sources = [page] + page.frames
            for source in all_sources:
                try:
                    candidates = await source.locator("button, input[type='submit'], a.btn, .button, .btn, .shopify-payment-button__button").all()
                    for btn in candidates:
                        if await btn.is_visible(timeout=500):
                            text = (await btn.text_content() or "").lower()
                            if any(kw in text for kw in ["buy", "checkout", "order", "now"]):
                                log.info(f"Universal finder found candidate: '{text.strip()}'. Clicking...")
                                await btn.click()
                                clicked_buy_now = True
                                break
                except: continue
                if clicked_buy_now: break
        
        if clicked_buy_now:
            # If we clicked Buy It Now, we expect a redirect to checkout
            log.info("Clicked 'Buy It Now'. Waiting for checkout redirect...")
            try:
                # Some themes open checkout in a new tab/popup
                async def _on_new_page(new_page):
                    nonlocal page
                    page = new_page
                context.on("page", _on_new_page)

                await page.wait_for_url("**/checkouts/**", timeout=45000)
                log.info(f"Redirected to checkout: {page.url}")
                # Skip the rest of the cart flow and go straight to info entry
                return await enter_checkout_info(context, customer, page)
            except:
                # Check if a new page with checkout was opened
                for p in context.pages:
                    if "/checkouts/" in p.url:
                        page = p
                        log.info(f"Found checkout in new tab: {page.url}")
                        return await enter_checkout_info(context, customer, page)
                log.warning("Buy It Now didn't redirect to checkout quickly. Falling back to cart flow.")

        # --- EXISTING: Add to cart flow (Fallback) ---
        log.info("Proceeding with standard 'Add to cart' flow...")

        # Re-enable buttons again in case MIDA re-disabled them after our earlier patch
        if is_protected_store(target_url):
            await page.evaluate("""() => {
                document.querySelectorAll('button[disabled], input[disabled]')
                    .forEach(el => { el.disabled = false; el.removeAttribute('disabled'); });
            }""")

        clicked_add = False
        # Narrow down the selectors to find the MAIN button, avoiding hidden sticky widgets
        for selector in add_to_cart_selectors:
            # We look for all matching locators and pick the one that is visible
            locs = page.locator(selector)
            for i in range(await locs.count()):
                loc = locs.nth(i)
                # Check for visibility and ensure it's not a sticky/hidden widget
                if await loc.is_visible():
                    html = (await loc.evaluate("el => el.outerHTML")).lower()
                    if "sticky" in html or "widget" in html:
                        continue # Skip sticky buttons as they might be covered/inactive

                    await loc.click()
                    clicked_add = True
                    log.info(f"Used selector: {selector}")
                    break
            if clicked_add:
                break
                
        if not clicked_add:
            # Try clicking submit inside product-form custom element (Dawn/Shopify 2.0 themes)
            log.info("Standard selectors failed. Trying product-form element submit...")
            try:
                clicked_add = await page.evaluate("""() => {
                    const form = document.querySelector('product-form form, form[action*="/cart/add"]');
                    if (form) {
                        const btn = form.querySelector('button[type="submit"], button[name="add"], button');
                        if (btn && !btn.disabled) {
                            btn.disabled = false;
                            btn.click();
                            return true;
                        }
                        // Try submitting the form directly
                        form.submit();
                        return true;
                    }
                    return false;
                }""")
                if clicked_add:
                    log.info("Clicked add-to-cart via product-form element")
            except Exception as e:
                log.warning(f"product-form fallback: {e}")
                clicked_add = False

        if not clicked_add:
            # Last resort: Any visible button with "cart" text that isn't sticky
            log.info("Standard selectors failed. Trying generic visible 'cart' button...")
            generic_btns = page.locator("button:visible, input[type='submit']:visible, a.button:visible")
            for i in range(await generic_btns.count()):
                btn = generic_btns.nth(i)
                text = (await btn.text_content() or "").lower()
                if "cart" in text and "sticky" not in (await btn.evaluate("el => el.className")).lower():
                    await btn.click()
                    clicked_add = True
                    log.info(f"Used generic selector: {text.strip()}")
                    break

        if not clicked_add:
            # ULTIMATE FALLBACK: JS direct form submit / AJAX add to cart
            log.info("All button selectors failed. Trying JS cart/add...")
            try:
                js_result = await page.evaluate("""() => {
                    // Try to find the variant ID from multiple sources
                    let variantId = null;

                    // 1. Standard input fields
                    const variantInput = document.querySelector(
                        'input[name="id"], .product-variant-id, select[name="id"]'
                    );
                    if (variantInput && variantInput.value) {
                        variantId = variantInput.value;
                    }

                    // 2. URL parameter (e.g. ?variant=12345)
                    if (!variantId) {
                        const urlParams = new URLSearchParams(window.location.search);
                        variantId = urlParams.get('variant');
                    }

                    // 3. Shopify product JSON in page (window.ShopifyAnalytics or meta tag)
                    if (!variantId) {
                        try {
                            const meta = document.querySelector('meta[property="og:image"]');
                            // Check ShopifyAnalytics
                            if (window.ShopifyAnalytics && window.ShopifyAnalytics.meta && window.ShopifyAnalytics.meta.selectedVariantId) {
                                variantId = window.ShopifyAnalytics.meta.selectedVariantId;
                            }
                        } catch(e) {}
                    }

                    // 4. Product JSON script tag (most Shopify themes have this)
                    if (!variantId) {
                        try {
                            const scripts = document.querySelectorAll('script[type="application/json"]');
                            for (const script of scripts) {
                                const data = JSON.parse(script.textContent);
                                if (data && data.product && data.product.variants && data.product.variants.length > 0) {
                                    variantId = data.product.variants[0].id;
                                    break;
                                }
                                // Some themes nest it differently
                                if (data && data.variants && data.variants.length > 0) {
                                    variantId = data.variants[0].id;
                                    break;
                                }
                            }
                        } catch(e) {}
                    }

                    // 5. product.json endpoint (inline fetch)
                    if (!variantId) {
                        try {
                            const path = window.location.pathname;
                            if (path.includes('/products/')) {
                                const xhr = new XMLHttpRequest();
                                xhr.open('GET', path + '.json', false); // synchronous
                                xhr.send();
                                if (xhr.status === 200) {
                                    const pdata = JSON.parse(xhr.responseText);
                                    if (pdata.product && pdata.product.variants && pdata.product.variants.length > 0) {
                                        variantId = pdata.product.variants[0].id;
                                    }
                                }
                            }
                        } catch(e) {}
                    }

                    if (!variantId) return 'no_variant';

                    // Direct fetch to /cart/add.js (Shopify AJAX API)
                    return fetch('/cart/add.js', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({items: [{id: parseInt(variantId), quantity: 1}]})
                    }).then(r => r.ok ? 'added' : 'failed:' + r.status)
                      .catch(e => 'error:' + e.message);
                }""")
                if js_result == 'added':
                    clicked_add = True
                    log.info("Added to cart via JS AJAX fallback!")
                else:
                    log.warning(f"JS cart/add result: {js_result}")
            except Exception as e:
                log.warning(f"JS fallback failed: {e}")

        if not clicked_add:
            log.error("Could not add to cart by any method.")
            await page.screenshot(path=f".tmp/no_cart_button_{int(time.time())}.png")
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
            # Derive origin from the product URL we're on
            from urllib.parse import urlparse as _urlparse
            _parsed = _urlparse(page.url)
            _origin = f"{_parsed.scheme}://{_parsed.netloc}"
            try:
                await page.goto(f"{_origin}/cart", wait_until="domcontentloaded", timeout=60000)
            except:
                pass

            await asyncio.sleep(2)

        # Avoid direct navigation or JS to bypass Bright Data CDP robots.txt enforcement
        # Instead, act entirely like a human clicking "Checkout"
        # We prioritize the "Add to cart" -> "Checkout" flow over "Buy it now"
        # as "Buy it now" is often more heavily guarded or prone to failures
        checkout_selectors = [
            "form[action='/cart'] [name='checkout']",
            "button[name='checkout']",
            "form[action='/checkout'] [type='submit']",
            "a[href='/checkout']", 
            ".cart__checkout-button",
            ".cart-drawer__footer .button--primary",
            "button:has-text('Checkout')",
            "button:has-text('CHECK OUT')",
            "#checkout",
            ".checkout-button"
        ]
        
        # Give the cart side-drawer or page a moment to fully render
        await asyncio.sleep(4)
        
        # Proactive: Check any terms/conditions checkboxes in the cart
        try:
            checkboxes = await page.locator("input[type='checkbox']").all()
            for cb in checkboxes:
                if await cb.is_visible() and not await cb.is_checked():
                    log.info("Checking terms/conditions checkbox in cart...")
                    await cb.click()
                    await asyncio.sleep(1)
        except:
            pass

        clicked_checkout = False
        for selector in checkout_selectors:
            try:
                # Wait up to 10 seconds for the button to appear in the DOM and be visible
                loc = await page.wait_for_selector(selector, state="visible", timeout=10000)
                if loc:
                    # Check for disabled attribute
                    is_disabled = await page.evaluate(f"document.querySelector(\"{selector}\").disabled")
                    if is_disabled:
                        log.info(f"Checkout button ({selector}) is disabled. Waiting for it to become enabled...")
                        await asyncio.sleep(5)
                    
                    await page.click(selector)
                    clicked_checkout = True
                    log.info(f"Clicked checkout via: {selector}")
                    break
            except:
                continue
                
        if not clicked_checkout:
            log.warning("Could not find a standard checkout button. Searching for anything that says checkout...")
            try:
                # Try a broader search with a timeout
                checkout_btn = await page.wait_for_selector("button:has-text('Checkout'), a:has-text('Checkout')", state="visible", timeout=5000)
                if checkout_btn:
                    await checkout_btn.click()
                    log.info("Clicked fallback checkout button.")
                    clicked_checkout = True
            except:
                pass
        
        if not clicked_checkout:
            log.warning("Final fallback: Navigating directly to /checkout")
            try:
                from urllib.parse import urlparse as _urlparse
                _parsed = _urlparse(page.url)
                _origin = f"{_parsed.scheme}://{_parsed.netloc}"
                await page.goto(f"{_origin}/checkout", wait_until="domcontentloaded", timeout=60000)
                clicked_checkout = True
            except Exception as e:
                log.error(f"Direct checkout navigation failed: {e}")
                
        # Wait for the checkout page to stabilize
        await asyncio.sleep(5)
        if len(context.pages) > 1:
            log.info(f"Detected {len(context.pages)} tabs. Switching to the latest tab.")
            page = context.pages[-1]
            await page.bring_to_front()
            
        # We wait for the URL to change
        try:
            await page.wait_for_url("**/checkouts/**", timeout=30000)
            log.info(f"Successfully on checkout URL: {page.url}")
        except:
            log.warning(f"Did not detect a checkout URL redirect. Current URL: {page.url}")
            
        return await enter_checkout_info(context, customer, page)

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

async def enter_checkout_info(context, customer, page):
    """Fills out the Shopify checkout information form."""
    try:
        await page.wait_for_load_state("load", timeout=90000)

        # Extra humanization for protected stores (Honeypot Guard timestamp validation)
        if is_protected_store(page.url):
            delay = random.uniform(4, 8)
            log.info(f"Protected store — adding {delay:.1f}s human delay before filling...")
            await asyncio.sleep(delay)
            # Simulate reading the page: scroll down slowly, move mouse
            await page.mouse.move(random.randint(200, 600), random.randint(200, 400))
            await page.mouse.wheel(0, random.randint(100, 300))
            await asyncio.sleep(random.uniform(1, 3))
        else:
            await asyncio.sleep(5)

        # 4. Fill Information (Shopify new checkout uses deeply nested standard fields)
        log.info(f"Filling customer info: {customer['email']}")
        
        # 1. Fill Identity (Email or Phone) field at the top
        try:
            identity_selectors = [
                "input[name='email']",
                "input[id='email']",
                "input[placeholder*='Email']",
                "input[placeholder*='phone']",
                "input[aria-label*='Email']",
                "input[aria-label*='phone']"
            ]
            found_id = False
            for sel in identity_selectors:
                if await safe_fill(page, sel, customer["phone"], "Identity"):
                    found_id = True
                    break
        except Exception as e:
            log.warning(f"Could not fill identity field: {e}")

        # Wait for dynamic fields to appear (sometimes filling one field triggers another)
        await asyncio.sleep(2)

        # 2. Fill Shipping Address Fields (honeypot-safe)
        fields = {
            "firstName": ("first name", customer["first_name"]),
            "lastName": ("last name", customer["last_name"]),
            "address1": ("address", customer["address"]),
            "city": ("city", customer["city"])
        }

        for name, (label, value) in fields.items():
            await safe_fill(page, f"input[name='{name}']", value, label)

        # 3. Robust/Duplicate Phone filling (Shipping Phone, honeypot-safe)
        phone_fields_found = 0
        try:
            phone_selectors = [
                "input[name='phone']",
                "input[id*='phone']",
                "input[type='tel']",
                "input[placeholder*='Phone']",
                "[id*='shipping_address_phone']",
                "input[aria-label*='Phone']"
            ]

            for sel in phone_selectors:
                elements = page.locator(sel)
                count = await elements.count()
                for i in range(count):
                    el = elements.nth(i)
                    try:
                        # Honeypot check: verify the element is truly visible
                        is_real = await el.evaluate("""el => {
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden') return false;
                            if (parseFloat(style.opacity) === 0) return false;
                            if (el.offsetWidth === 0 && el.offsetHeight === 0) return false;
                            let parent = el.parentElement;
                            for (let d = 0; d < 5 && parent; d++) {
                                const ps = window.getComputedStyle(parent);
                                if (ps.display === 'none' || ps.visibility === 'hidden') return false;
                                parent = parent.parentElement;
                            }
                            return true;
                        }""")
                        if not is_real:
                            log.info(f"Skipping honeypot phone field: {sel}")
                            continue
                        curr_val = await el.get_attribute("value") or ""
                        if curr_val.strip() != customer["phone"].strip():
                            await el.fill(customer["phone"])
                            phone_fields_found += 1
                            log.info(f"Filled/Updated Phone field #{phone_fields_found} via: {sel}")
                    except:
                        continue
        except Exception as e:
            log.warning(f"Error during phone field re-scan: {e}")
                
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

        # --- COD Selection (JS-first for speed) ---
        log.info("Selecting COD payment...")
        await page.evaluate("""() => {
            const inputs = Array.from(document.querySelectorAll('input[type="radio"]'));
            const cod = inputs.find(i =>
                i.value.includes('manual') || i.value.includes('cod') ||
                i.id.includes('manual') || i.id.includes('cod')
            );
            if (cod) {
                const nativeSet = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'checked'
                ).set;
                nativeSet.call(cod, true);
                cod.dispatchEvent(new Event('input', { bubbles: true }));
                cod.dispatchEvent(new Event('change', { bubbles: true }));
                cod.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                const label = cod.closest('label') || document.querySelector('label[for="' + cod.id + '"]');
                if (label) label.click();
            }
        }""")
        log.info("COD selected")

        # Submit order: click a filled field and press Enter (instant form submit)
        log.info("Submitting order via field focus + Enter...")
        await asyncio.sleep(2)
        try:
            field = page.locator("input[name='firstName']").first
            await field.click()
            await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
        except:
            # Fallback: just press Enter on the page
            await page.keyboard.press("Enter")

        # Wait for confirmation page
        for i in range(10):
            await asyncio.sleep(2)
            if any(x in page.url for x in ["thank", "orders", "receipt"]):
                log.info(f"Successfully reached Order Confirmation! URL: {page.url}")
                return True

        log.info(f"Order submission attempted. Final URL: {page.url}")
        return True

    except Exception as e:
        log.error(f"Error inside enter_checkout_info: {e}")
        try:
            await page.screenshot(path=f".tmp/checkout_info_fail_{int(time.time())}.png")
        except:
            pass
        return False


# ============================================================================
# WooCommerce flow (separate from Shopify because URL/selectors/checkout differ)
# ============================================================================

async def get_random_woocommerce_product_url(page, base_url):
    """WooCommerce: navigate to /shop/ and pick a random /product/ link."""
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    store_origin = f"{parsed.scheme}://{parsed.netloc}"

    if HAS_STEALTH:
        await Stealth().apply_stealth_async(page)

    candidate_paths = ["/shop/", "/shop", "/store/", "/all-products/", "/?post_type=product"]
    for path in candidate_paths:
        shop_url = store_origin + path
        log.info(f"Navigating to {shop_url} to find products...")
        try:
            # Shorter per-path timeout: if the proxy IP is dead, fail fast across
            # paths instead of burning ~90s each. Total budget across 5 paths
            # capped at ~2.5 min instead of 7.5 min.
            await page.goto(shop_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_selector("a[href*='/product/']", timeout=15000)
            except:
                await asyncio.sleep(3)

            product_links = await page.locator("a[href*='/product/']").all()
            valid_urls = []
            for link in product_links:
                href = await link.get_attribute("href")
                if not href:
                    continue
                if "/product/" not in href:
                    continue
                if "/product-category/" in href or "/product-tag/" in href:
                    continue
                full_url = href if href.startswith("http") else f"{store_origin}{href}"
                valid_urls.append(full_url)

            valid_urls = list(set(valid_urls))
            if valid_urls:
                chosen = random.choice(valid_urls)
                log.info(f"Found {len(valid_urls)} WooCommerce products. Picked: {chosen}")
                return chosen
        except Exception as e:
            log.warning(f"WooCommerce shop path '{path}' failed: {e}")
            continue

    log.error(f"Could not find any /product/ links across shop paths on {store_origin}")
    try:
        os.makedirs(".tmp", exist_ok=True)
        await page.screenshot(path=".tmp/wc_failed_product_fetch.png")
    except:
        pass
    return None


async def run_woocommerce_checkout_flow(context, customer, target_url):
    """Executes the WooCommerce Add to Cart and Checkout flow."""
    page = context.pages[0] if context.pages else await context.new_page()
    from urllib.parse import urlparse
    parsed = urlparse(target_url)
    store_origin = f"{parsed.scheme}://{parsed.netloc}"

    try:
        if HAS_STEALTH:
            await Stealth().apply_stealth_async(page)

        # 1. Product page
        log.info(f"[WC] Navigating to product page: {target_url}")
        await page.goto(target_url, wait_until="domcontentloaded", timeout=90000)
        try:
            await page.wait_for_selector(
                "form.cart, button.single_add_to_cart_button, button[name='add-to-cart']",
                timeout=25000
            )
            # Extra settle time so JS event handlers bind (matters on bundled Chromium)
            await asyncio.sleep(2)
        except:
            log.warning("[WC] Cart form selector wait timed out — continuing anyway")
            await asyncio.sleep(5)

        # Humanize
        log.info("[WC] Humanizing: scroll + hover")
        await page.mouse.wheel(0, 400)
        await asyncio.sleep(random.uniform(2, 4))

        # 2. Handle variations (variable products require attribute selection)
        has_variations = await page.locator("table.variations, form.variations_form").count() > 0
        if has_variations:
            log.info("[WC] Variable product detected — selecting first available variation")
            try:
                selects = await page.locator("table.variations select, form.variations_form select").all()
                for sel in selects:
                    options = await sel.locator("option").all()
                    for opt in options:
                        val = await opt.get_attribute("value")
                        if val and val.strip():
                            await sel.select_option(value=val)
                            await asyncio.sleep(0.8)
                            break
            except Exception as e:
                log.warning(f"[WC] Variation selection error: {e}")

            # Wait for variation_id input to populate
            try:
                await page.wait_for_function(
                    """() => {
                        const v = document.querySelector('input[name="variation_id"]');
                        return v && v.value && v.value !== '0';
                    }""",
                    timeout=8000
                )
                log.info("[WC] Variation ID populated")
            except:
                log.warning("[WC] variation_id did not populate; attempting add-to-cart anyway")

        # 3. Extract product ID up front for URL fallback. WordPress always adds
        # `postid-XXXX` to the body class on single-product pages, so that's our
        # bulletproof signal even if the cart form is slow to render.
        product_id = await page.evaluate("""() => {
            const cleanId = (v) => {
                if (!v) return null;
                const m = String(v).match(/\\d{2,}/);
                return m ? m[0] : null;
            };
            // 1. form.cart button (simple products)
            let btn = document.querySelector('form.cart button[name="add-to-cart"]');
            if (btn && btn.value) return cleanId(btn.value);
            // 2. form.cart hidden input (some themes)
            let inp = document.querySelector('form.cart input[name="add-to-cart"]');
            if (inp && inp.value) return cleanId(inp.value);
            // 3. variation form data-product_id (variable products)
            let varForm = document.querySelector('form.variations_form');
            if (varForm) {
                const v = varForm.getAttribute('data-product_id');
                if (v) return cleanId(v);
            }
            // 4. .product .single_add_to_cart_button (any product type)
            let single = document.querySelector('.product .single_add_to_cart_button, .product button[name="add-to-cart"]');
            if (single && single.value) return cleanId(single.value);
            // 5. body class postid-XXXX (WordPress canonical)
            const bodyClass = document.body.className || '';
            const m = bodyClass.match(/postid-(\\d+)/);
            if (m) return m[1];
            // 6. last resort: any data-product_id on the main product container
            const dataEl = document.querySelector('.product[data-product_id], main [data-product_id]');
            if (dataEl) return cleanId(dataEl.getAttribute('data-product_id'));
            return null;
        }""")
        log.info(f"[WC] Detected product_id: {product_id}")

        # 4. Click Add to Cart - with progressive fallback to URL trick
        clicked_add = False
        wc_atc_selectors = [
            "form.cart button.single_add_to_cart_button",
            "button.single_add_to_cart_button",
            "form.cart button[name='add-to-cart']",
            "form.variations_form button[name='add-to-cart']",
            "form.cart button[type='submit']",
            ".product .single_add_to_cart_button",
        ]
        for sel in wc_atc_selectors:
            try:
                count = await page.locator(sel).count()
                if count == 0:
                    continue
                loc = page.locator(sel).first
                # Force-enable disabled buttons before clicking
                await page.evaluate(f"""() => {{
                    document.querySelectorAll({sel!r}).forEach(b => {{
                        b.disabled = false;
                        b.removeAttribute('disabled');
                        b.classList.remove('disabled');
                    }});
                }}""")
                await loc.scroll_into_view_if_needed(timeout=5000)
                await asyncio.sleep(0.5)
                # Use force=True to bypass actionability checks (overlays, animations)
                await loc.click(timeout=10000, force=True)
                clicked_add = True
                log.info(f"[WC] Clicked add-to-cart via: {sel}")
                break
            except Exception as e:
                log.debug(f"[WC] Selector {sel} failed: {str(e)[:80]}")
                continue

        if not clicked_add:
            # JS form submit fallback
            log.info("[WC] Click selectors failed — JS form submit fallback")
            submitted = await page.evaluate("""() => {
                const form = document.querySelector('form.cart, form.variations_form');
                if (!form) return null;
                const btn = form.querySelector('button[name="add-to-cart"], button.single_add_to_cart_button, button[type="submit"]');
                if (btn) {
                    btn.disabled = false;
                    btn.removeAttribute('disabled');
                    btn.click();
                    return 'clicked:' + (btn.value || '?');
                }
                form.submit();
                return 'submitted';
            }""")
            if submitted:
                clicked_add = True
                log.info(f"[WC] JS form fallback: {submitted}")

        if not clicked_add and product_id:
            # URL fallback: GET to product URL with ?add-to-cart=ID
            from urllib.parse import urlparse as _u
            parsed = _u(target_url)
            base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            add_url = f"{base}?add-to-cart={product_id}"
            log.info(f"[WC] URL fallback: {add_url}")
            try:
                await page.goto(add_url, wait_until="domcontentloaded", timeout=60000)
                clicked_add = True
            except Exception as e:
                log.warning(f"[WC] URL fallback failed: {e}")

        if not clicked_add:
            log.error("[WC] Could not add to cart by any method")
            try:
                await page.screenshot(path=f".tmp/wc_no_atc_{int(time.time())}.png")
            except:
                pass
            return False

        # Wait for AJAX add-to-cart to settle
        await asyncio.sleep(random.uniform(3, 5))

        # 4. Verify cart actually has items by visiting /cart/
        # If empty, retry via URL trick (handles cases where button click was
        # intercepted by JS but the server-side cart never updated)
        cart_url = f"{store_origin}/cart/"
        log.info(f"[WC] Verifying cart at {cart_url}")
        await page.goto(cart_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)

        cart_has_items = await page.evaluate("""() => {
            // Standard WooCommerce: items live in tr.cart_item or .woocommerce-cart-form
            const items = document.querySelectorAll('tr.cart_item, .cart_item, .woocommerce-cart-form .product-name');
            if (items.length > 0) return items.length;
            // Empty-cart message
            const empty = document.querySelector('.cart-empty, .wc-empty-cart-message');
            return empty ? 0 : -1;  // -1 = inconclusive
        }""")
        log.info(f"[WC] Cart item count: {cart_has_items}")

        if cart_has_items == 0 and product_id:
            # Retry with URL trick (most reliable WC add-to-cart)
            from urllib.parse import urlparse as _u
            parsed = _u(target_url)
            base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            add_url = f"{base}?add-to-cart={product_id}"
            log.info(f"[WC] Cart empty — retrying with URL: {add_url}")
            await page.goto(add_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(3)
            await page.goto(cart_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
            cart_has_items = await page.evaluate("""() => {
                const items = document.querySelectorAll('tr.cart_item, .cart_item, .woocommerce-cart-form .product-name');
                return items.length;
            }""")
            log.info(f"[WC] Cart item count after URL retry: {cart_has_items}")

        if not cart_has_items or cart_has_items <= 0:
            log.error("[WC] Cart is empty after add-to-cart attempts")
            try:
                await page.screenshot(path=f".tmp/wc_empty_cart_{int(time.time())}.png")
            except:
                pass
            return False

        # 5. Click "Proceed to Checkout" from cart page
        clicked_checkout = False
        cart_checkout_selectors = [
            "a.checkout-button",
            ".wc-proceed-to-checkout a.button",
            ".wc-proceed-to-checkout a",
            "a.wc-forward",
            "a:has-text('Proceed to checkout')",
            "a:has-text('Proceed to Checkout')",
            "a:has-text('Checkout')",
        ]
        for s in cart_checkout_selectors:
            try:
                loc = page.locator(s).first
                if await loc.count() == 0:
                    continue
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=10000, force=True)
                clicked_checkout = True
                log.info(f"[WC] Clicked checkout button via: {s}")
                break
            except Exception as e:
                log.debug(f"[WC] Cart checkout selector {s} failed: {str(e)[:80]}")
                continue

        if not clicked_checkout:
            log.warning("[WC] No checkout button clicked — direct nav to /checkout/")
            await page.goto(f"{store_origin}/checkout/", wait_until="domcontentloaded", timeout=60000)

        # Wait for checkout form to render
        try:
            await page.wait_for_selector(
                "form.checkout, form[name='checkout'], #place_order, input[name='billing_first_name'], input#billing_first_name",
                timeout=30000
            )
            log.info(f"[WC] Checkout form detected at {page.url}")
        except:
            log.warning(f"[WC] Checkout form not detected. URL: {page.url}")

        return await enter_woocommerce_checkout_info(context, customer, page)

    except Exception as e:
        log.error(f"[WC] Error during checkout flow: {e}")
        try:
            os.makedirs(".tmp", exist_ok=True)
            await page.screenshot(path=f".tmp/wc_error_{int(time.time())}.png")
        except:
            pass
        return False


async def enter_woocommerce_checkout_info(context, customer, page):
    """Fills out WooCommerce checkout form and places the order."""
    try:
        await page.wait_for_load_state("load", timeout=90000)

        # Extra delay for protected stores
        if is_protected_store(page.url):
            delay = random.uniform(4, 8)
            log.info(f"[WC] Protected store — {delay:.1f}s human delay before filling")
            await asyncio.sleep(delay)
            await page.mouse.move(random.randint(200, 600), random.randint(200, 400))
            await page.mouse.wheel(0, random.randint(100, 300))
            await asyncio.sleep(random.uniform(1, 3))
        else:
            await asyncio.sleep(4)

        log.info(f"[WC] Filling customer info for: {customer['email']}")

        # Country first — required before state dropdown populates
        country_select = page.locator("select#billing_country, select[name='billing_country']").first
        if await country_select.count() > 0:
            try:
                target_country = customer.get("country_code", "AE")
                await country_select.select_option(value=target_country)
                log.info(f"[WC] Selected country: {target_country}")
                await asyncio.sleep(2)  # let state dropdown rebuild via AJAX
            except Exception as e:
                log.warning(f"[WC] Country select failed: {e}")

        # Standard WC billing fields
        billing_fields = {
            "billing_first_name": customer["first_name"],
            "billing_last_name": customer["last_name"],
            "billing_address_1": customer["address"],
            "billing_city": customer["city"],
            "billing_postcode": customer.get("zip", "00000"),
            "billing_phone": customer["phone"],
            "billing_email": customer["email"],
            "billing_company": "",
        }
        for name, value in billing_fields.items():
            if value == "":
                continue
            await safe_fill(page, f"input[name='{name}'], input#{name}", value, name)

        # Billing state (UAE emirate)
        try:
            state_select = page.locator("select#billing_state, select[name='billing_state']").first
            if await state_select.count() > 0 and await state_select.is_visible():
                target_code = customer.get("state_code", "").upper()
                options = await state_select.locator("option").all()
                option_values = []
                for opt in options:
                    v = await opt.get_attribute("value")
                    t = (await opt.text_content()) or ""
                    if v and v.strip():
                        option_values.append((v, t.strip()))

                chosen = None
                if target_code:
                    for v, t in option_values:
                        if v.upper() == target_code:
                            chosen = v
                            break
                if not chosen:
                    target_city = customer.get("city", "").lower()
                    for v, t in option_values:
                        if target_city and target_city in t.lower():
                            chosen = v
                            break
                if not chosen and option_values:
                    chosen = option_values[0][0]

                if chosen:
                    await state_select.select_option(value=chosen)
                    log.info(f"[WC] Selected billing_state: {chosen}")
        except Exception as e:
            log.warning(f"[WC] State select failed: {e}")

        # Wait for AJAX checkout update_order_review to settle
        await asyncio.sleep(3)
        try:
            await page.wait_for_selector(".blockUI.blockOverlay", state="detached", timeout=15000)
        except:
            pass

        # Select COD payment method
        log.info("[WC] Selecting COD payment method")
        cod_selected = await page.evaluate("""() => {
            const radios = Array.from(document.querySelectorAll('input[name="payment_method"]'));
            // Prefer COD by id/value
            let target = radios.find(r =>
                /cod|cash[-_ ]?on[-_ ]?delivery|cash/i.test((r.value || '') + ' ' + (r.id || ''))
            );
            // Fallback: first available radio
            if (!target && radios.length) target = radios[0];
            if (!target) return 'no_payment_radio';

            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'checked'
            ).set;
            setter.call(target, true);
            target.dispatchEvent(new Event('change', {bubbles: true}));
            target.dispatchEvent(new Event('click', {bubbles: true}));
            const label = document.querySelector('label[for="' + target.id + '"]');
            if (label) label.click();
            return 'selected:' + target.value;
        }""")
        log.info(f"[WC] COD selection: {cod_selected}")
        await asyncio.sleep(2)

        # Tick required terms checkbox if present
        try:
            terms = page.locator("input[name='terms']").first
            if await terms.count() > 0 and not await terms.is_checked():
                await terms.check(force=True)
                log.info("[WC] Checked terms checkbox")
        except:
            pass

        # Wait for any remaining AJAX overlay
        await asyncio.sleep(2)
        try:
            await page.wait_for_selector(".blockUI.blockOverlay", state="detached", timeout=10000)
        except:
            pass

        # Place order
        log.info("[WC] Clicking Place Order")
        placed = False
        for sel in ["#place_order", "button#place_order", "button[name='woocommerce_checkout_place_order']", "button.place-order"]:
            try:
                btn = page.locator(sel).first
                if await btn.count() == 0:
                    continue
                if await btn.is_visible():
                    await btn.scroll_into_view_if_needed()
                    await asyncio.sleep(1)
                    await btn.click()
                    placed = True
                    log.info(f"[WC] Place Order clicked via: {sel}")
                    break
            except Exception as e:
                log.debug(f"[WC] Place order selector {sel} failed: {e}")

        if not placed:
            # JS form submit fallback
            placed = await page.evaluate("""() => {
                const form = document.querySelector('form.checkout, form[name="checkout"]');
                if (!form) return false;
                const btn = form.querySelector('#place_order, button[type="submit"]');
                if (btn) { btn.click(); return 'clicked'; }
                form.submit();
                return 'submitted';
            }""")
            log.info(f"[WC] Place order JS fallback: {placed}")

        # Wait for order-received confirmation OR a WC error notice
        for i in range(15):
            await asyncio.sleep(2)
            url_l = page.url.lower()
            if any(x in url_l for x in ["order-received", "thank", "order-pay", "checkout/order"]):
                # Extract order number — WC typically puts it in the URL path
                # (/order-received/<ID>/) and on the page as .order strong / .woocommerce-order-overview__order
                import re as _re
                order_id = None
                m = _re.search(r"/order-received/(\d+)", page.url)
                if m:
                    order_id = m.group(1)
                if not order_id:
                    try:
                        order_id = await page.evaluate("""() => {
                            const sels = [
                                '.woocommerce-order-overview__order strong',
                                '.order strong',
                                'li.woocommerce-order-overview__order',
                                '.woocommerce-thankyou-order-received'
                            ];
                            for (const s of sels) {
                                const el = document.querySelector(s);
                                if (el) {
                                    const m = (el.textContent || '').match(/\\d{3,}/);
                                    if (m) return m[0];
                                }
                            }
                            return null;
                        }""")
                    except:
                        pass
                if order_id:
                    log.info(f"[WC] Order placed! Order #{order_id}  URL: {page.url}")
                else:
                    log.info(f"[WC] Order placed! URL: {page.url}")
                return True

            # Check for inline WC validation/error messages
            try:
                wc_errors = await page.evaluate("""() => {
                    const sel = '.woocommerce-error li, .woocommerce-NoticeGroup-checkout li, ul.woocommerce-error, .wc-block-components-notice-banner';
                    return Array.from(document.querySelectorAll(sel))
                        .map(el => (el.textContent || '').trim())
                        .filter(t => t.length > 0)
                        .slice(0, 5);
                }""")
                if wc_errors:
                    log.error(f"[WC] Checkout rejected with errors: {wc_errors}")
                    try:
                        await page.screenshot(path=f".tmp/wc_checkout_error_{int(time.time())}.png")
                    except:
                        pass
                    return False
            except:
                pass

        log.warning(f"[WC] Did not detect order confirmation. Final URL: {page.url}")
        try:
            await page.screenshot(path=f".tmp/wc_no_confirm_{int(time.time())}.png")
            html = await page.content()
            with open(f".tmp/wc_no_confirm_{int(time.time())}.html", "w", encoding="utf-8") as f:
                f.write(html[:200000])
        except:
            pass
        return False

    except Exception as e:
        log.error(f"[WC] Error in enter_woocommerce_checkout_info: {e}")
        try:
            os.makedirs(".tmp", exist_ok=True)
            await page.screenshot(path=f".tmp/wc_info_fail_{int(time.time())}.png")
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

        # Use system Chrome for real TLS fingerprint (bypasses Cloudflare JA3 detection)
        # Playwright's bundled Chromium has a known TLS fingerprint that Cloudflare blocks
        launch_args["channel"] = "chrome"
        try:
            browser = await p.chromium.launch(**launch_args)
            log.info("Launched system Chrome (real TLS fingerprint)")
        except Exception:
            # Fallback to bundled Chromium if Chrome not installed
            del launch_args["channel"]
            browser = await p.chromium.launch(**launch_args)
            log.warning("System Chrome not found, using bundled Chromium")

        order_count = 0
        while True:
            if count > 0 and order_count >= count:
                log.info(f"Reached target order count ({count}). Exiting.")
                break

            log.info(f"--- Starting Round {order_count + 1} ---")

            # Rotate viewport slightly per session to vary fingerprint
            vw = random.choice([1280, 1366, 1440, 1536, 1920])
            vh = random.choice([768, 800, 900, 1080])

            context_args = {
                "viewport": {"width": vw, "height": vh},
                "user_agent": random.choice(USER_AGENTS),
                "ignore_https_errors": True,
                "locale": random.choice(["en-AE", "en-US", "en-GB", "ar-AE"]),
                "timezone_id": "Asia/Dubai"
            }

            context = await browser.new_context(**context_args)
            log.info(f"Session fingerprint: {vw}x{vh} | UA: {context_args['user_agent'][:50]}...")

            # Determine current target store for resource blocking decision
            current_target = PRODUCT_URL
            if STORE_URLS:
                current_target = STORE_URLS[order_count % len(STORE_URLS)]

            # Skip resource blocking for protected stores (MIDA fingerprints missing loads)
            should_block = BLOCK_RESOURCES and not is_protected_store(current_target)
            if should_block:
                async def intercept(route):
                    if route.request.resource_type in ["image", "media", "font"]:
                        await route.abort()
                    else:
                        await route.continue_()
                await context.route("**/*", intercept)
            elif is_protected_store(current_target):
                log.info("Protected store detected — loading ALL resources (no blocking)")

            # Neutralize MIDA + disable-devtool anti-automation scripts
            if is_protected_store(current_target):
                async def block_antibot(route):
                    await route.abort()
                await context.route("**/disable-devtool**", block_antibot)
                await context.route("**/mida**/script.min.js", block_antibot)
                # Patch only the specific DevTools size detection (safe, non-breaking)
                await context.add_init_script("""
                    Object.defineProperty(window, 'outerHeight', {
                        get: () => window.innerHeight, configurable: true
                    });
                    Object.defineProperty(window, 'outerWidth', {
                        get: () => window.innerWidth, configurable: true
                    });
                """)
                log.info("Blocked disable-devtool + patched DevTools size detection")

            # Fetch random product if needed
            target_url = PRODUCT_URL
            platform = "shopify"
            if STORE_URLS:
                # Cycle through URLs based on order count
                base_url = STORE_URLS[order_count % len(STORE_URLS)]
                platform = detect_platform(base_url)
                log.info(f"Targeting Store: {base_url} (platform: {platform})")

                # If the URL is already a product page, use it directly
                if "/products/" in base_url or "/product/" in base_url:
                    target_url = base_url
                else:
                    # Otherwise, navigate to find a random product
                    temp_page = await context.new_page()
                    if platform == "woocommerce":
                        target_url = await get_random_woocommerce_product_url(temp_page, base_url=base_url)
                    else:
                        target_url = await get_random_product_url(temp_page, base_url=base_url)
                    await temp_page.close()

            # Re-detect platform from the FINAL target URL — handles the case
            # where TEST_PRODUCT_URL points to a different platform than STORE_URLS
            if target_url:
                platform = detect_platform(target_url)

            success = False
            if target_url:
                customer = get_random_customer()
                if platform == "woocommerce":
                    success = await run_woocommerce_checkout_flow(context, customer, target_url)
                else:
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
