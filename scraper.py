"""
Immoweb Scraper — Ixelles / Phone Number Filter → Telegram
Runs every 30 minutes automatically using APScheduler.
"""

import json
import logging
import os
import re
import time
import random
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.blocking import BlockingScheduler

# ─────────────────────────────────────────────
#  CONFIG — edit these before running
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]    # set in Railway dashboard
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]  # set in Railway dashboard

SEEN_FILE = Path("seen_listings.json")            # tracks already-sent listing IDs
INTERVAL_MINUTES = 30                             # how often to check

# Immoweb search URL for Ixelles rentals — tweak the URL params as needed
# To customize: go to immoweb.be, set your filters, copy the URL
SEARCH_URL = (
    "https://www.immoweb.be/en/search/house-and-apartment/for-rent"
    "?countries=BE"
    "&localities=Ixelles"
    "&orderBy=newest"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-BE,fr;q=0.9,en;q=0.8",
}

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scraper.log"),
    ],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  PHONE NUMBER DETECTION (Belgian formats)
# ─────────────────────────────────────────────
PHONE_PATTERNS = [
    # Mobile: 04xx xx xx xx / +32 4xx xx xx xx
    r"(?:\+32\s?|0032\s?)?4[5-9]\d[\s.\-]?\d{2}[\s.\-]?\d{2}[\s.\-]?\d{2}",
    # Landline Brussels: 02 xxx xx xx
    r"(?:\+32\s?|0032\s?)?2[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}",
    # Other landlines: 0x xxx xx xx
    r"0[1-9][\s.\-]?\d{2,3}[\s.\-]?\d{2}[\s.\-]?\d{2}",
]
PHONE_REGEX = re.compile("|".join(PHONE_PATTERNS))


def extract_phone_numbers(text: str) -> list[str]:
    """Find all Belgian phone numbers in a block of text."""
    matches = PHONE_REGEX.findall(text)
    # Clean up whitespace/separators for display
    cleaned = []
    for m in matches:
        m = m.strip()
        if m and len(re.sub(r"\D", "", m)) >= 8:  # at least 8 digits
            cleaned.append(m)
    return list(dict.fromkeys(cleaned))  # deduplicate, preserve order


# ─────────────────────────────────────────────
#  SEEN LISTINGS (JSON persistence)
# ─────────────────────────────────────────────
def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen), indent=2))


# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        log.info("Telegram message sent.")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


# ─────────────────────────────────────────────
#  IMMOWEB SCRAPING
# ─────────────────────────────────────────────
def fetch_search_results() -> list[dict]:
    """
    Fetch the Immoweb search page and return a list of listings.
    Immoweb embeds listing data as JSON in a <script> tag — we extract that.
    Falls back to HTML parsing if JSON extraction fails.
    """
    log.info(f"Fetching search results from Immoweb...")
    try:
        r = requests.get(SEARCH_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Failed to fetch search page: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    listings = []

    # Strategy 1: extract from embedded JSON (window.dataLayer or classified JSON blobs)
    for script in soup.find_all("script", type="text/javascript"):
        text = script.string or ""
        if '"id"' in text and '"price"' in text and "classified" in text.lower():
            try:
                # Try to find the JSON array of results
                match = re.search(r'"results"\s*:\s*(\[.*?\])\s*[,}]', text, re.DOTALL)
                if match:
                    items = json.loads(match.group(1))
                    for item in items:
                        listing_id = str(item.get("id", ""))
                        if listing_id:
                            listings.append({
                                "id": listing_id,
                                "url": f"https://www.immoweb.be/en/classified/{listing_id}",
                                "price": item.get("price", {}).get("mainValue", "N/A"),
                                "locality": item.get("property", {}).get("location", {}).get("locality", ""),
                                "zip": item.get("property", {}).get("location", {}).get("postalCode", ""),
                                "type": item.get("property", {}).get("type", ""),
                                "bedrooms": item.get("property", {}).get("bedroomCount", "?"),
                                "area": item.get("property", {}).get("netHabitableSurface", "?"),
                            })
                    if listings:
                        log.info(f"Extracted {len(listings)} listings from embedded JSON.")
                        return listings
            except Exception:
                pass  # fall through to HTML parsing

    # Strategy 2: HTML parsing fallback
    cards = soup.select("article.card--result, li.search-results__item")
    for card in cards:
        a_tag = card.select_one("a[href*='/classified/']")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        id_match = re.search(r"/classified/(\d+)", href)
        if not id_match:
            continue
        listing_id = id_match.group(1)
        price_el = card.select_one("[class*='price']")
        price = price_el.get_text(strip=True) if price_el else "N/A"
        listings.append({
            "id": listing_id,
            "url": f"https://www.immoweb.be/en/classified/{listing_id}",
            "price": price,
            "locality": "Ixelles",
            "zip": "",
            "type": "",
            "bedrooms": "?",
            "area": "?",
        })

    log.info(f"Extracted {len(listings)} listings via HTML fallback.")
    return listings


def fetch_listing_detail(listing: dict) -> dict:
    """
    Fetch the individual listing page and extract:
    - Full description text
    - Phone numbers in the description
    - Agency or private owner
    """
    time.sleep(random.uniform(2, 5))  # polite delay
    try:
        r = requests.get(listing["url"], headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning(f"Failed to fetch listing {listing['id']}: {e}")
        return listing

    soup = BeautifulSoup(r.text, "html.parser")

    # Extract description
    desc_el = soup.select_one(
        "[class*='description'], [class*='classified__description'], "
        "section.classified__description, div.classified__description--content"
    )
    description = desc_el.get_text(separator=" ", strip=True) if desc_el else ""

    # Also grab the full page text for phone number hunting
    full_text = soup.get_text(separator=" ")

    # Search for phone numbers in description + full page
    phones = extract_phone_numbers(description + " " + full_text)

    # Try to detect if it's a private owner
    is_private = False
    agent_section = soup.select_one("[class*='agency'], [class*='agent'], [class*='advertiser']")
    if agent_section:
        agent_text = agent_section.get_text().lower()
        is_private = "private" in agent_text or "particulier" in agent_text or "eigenaar" in agent_text

    listing["description"] = description[:500] if description else ""
    listing["phones"] = phones
    listing["is_private"] = is_private
    return listing


# ─────────────────────────────────────────────
#  FORMAT TELEGRAM MESSAGE
# ─────────────────────────────────────────────
def format_message(listing: dict) -> str:
    phones_str = " | ".join(listing.get("phones", []))
    price = listing.get("price", "N/A")
    locality = listing.get("locality", "")
    zip_code = listing.get("zip", "")
    bedrooms = listing.get("bedrooms", "?")
    area = listing.get("area", "?")
    prop_type = listing.get("type", "").replace("_", " ").title()
    url = listing.get("url", "")
    is_private = listing.get("is_private", False)
    owner_tag = "👤 Private owner" if is_private else "🏢 Agency"

    lines = [
        f"🏠 <b>New listing — {locality} {zip_code}</b>",
        f"💶 <b>Price:</b> {price} €/month",
        f"🛏 <b>Bedrooms:</b> {bedrooms} | 📐 <b>Area:</b> {area} m²",
        f"📋 <b>Type:</b> {prop_type}",
        f"{owner_tag}",
        f"📞 <b>Phone:</b> <code>{phones_str}</code>",
        f"🔗 <a href=\"{url}\">View listing</a>",
        f"<i>🕐 Found at {datetime.now().strftime('%d/%m/%Y %H:%M')}</i>",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  MAIN JOB
# ─────────────────────────────────────────────
def run_scraper():
    log.info("=" * 50)
    log.info("Running scraper job...")

    seen = load_seen()
    listings = fetch_search_results()

    if not listings:
        log.warning("No listings found. Immoweb may have changed its structure.")
        return

    new_count = 0
    sent_count = 0

    for listing in listings:
        lid = listing["id"]
        if lid in seen:
            continue

        new_count += 1
        log.info(f"New listing found: {lid} — fetching details...")
        listing = fetch_listing_detail(listing)
        phones = listing.get("phones", [])

        if phones:
            log.info(f"  ✅ Phone number found: {phones} — sending to Telegram")
            message = format_message(listing)
            send_telegram(message)
            sent_count += 1
        else:
            log.info(f"  ⏭  No phone number in listing {lid} — skipping")

        seen.add(lid)

    save_seen(seen)
    log.info(f"Done. {new_count} new listings checked, {sent_count} sent to Telegram.")


# ─────────────────────────────────────────────
#  SCHEDULER ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log.info("🚀 Immoweb scraper starting up...")
    log.info(f"Will check every {INTERVAL_MINUTES} minutes.")

    # Run once immediately on startup
    run_scraper()

    # Then schedule
    scheduler = BlockingScheduler()
    scheduler.add_job(run_scraper, "interval", minutes=INTERVAL_MINUTES)
    log.info("Scheduler started. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Scraper stopped.")
