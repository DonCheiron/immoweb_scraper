"""
Immoweb Scraper — CSV export for local on-demand analysis.
"""

import csv
import json
import logging
import re
import time
import random
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
#  CONFIG — edit these before running
# ─────────────────────────────────────────────
CSV_OUTPUT_FILE = Path("immoweb_listings.csv")
CSV_FIELDS = [
    "id",
    "url",
    "price",
    "locality",
    "zip",
    "type",
    "bedrooms",
    "area",
    "description",
    "is_private",
]

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
#  CSV OUTPUT
# ─────────────────────────────────────────────
def write_csv(listings: list[dict]):
    CSV_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUTPUT_FILE.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for listing in listings:
            row = {k: listing.get(k, "") for k in CSV_FIELDS}
            row["is_private"] = "yes" if listing.get("is_private") else "no"
            writer.writerow(row)
    log.info(f"Wrote {len(listings)} listings to {CSV_OUTPUT_FILE}")


# ─────────────────────────────────────────────
#  IMMOWEB SCRAPING
# ─────────────────────────────────────────────
def fetch_search_results() -> list[dict]:
    log.info("Fetching search results from Immoweb...")
    try:
        r = requests.get(SEARCH_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Failed to fetch search page: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    listings = []

    # URL format: /en/classified/{type}/for-(rent|sale)/{locality}/{zip}/{id}
    cards = soup.select("article.card--result")
    for card in cards:
        a_tag = card.select_one("a[href*='/classified/']")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        url_match = re.search(r"/classified/([^/]+)/for-(?:rent|sale)/([^/]+)/(\d+)/(\d+)", href)
        if not url_match:
            continue
        prop_type, locality, zip_code, listing_id = url_match.groups()
        # Surface area sits in the property-info cell
        area = "?"
        prop_info = card.select_one(".card__information--property")
        if prop_info:
            area_match = re.search(r"(\d+)", prop_info.get_text())
            if area_match:
                area = area_match.group(1)
        listings.append({
            "id": listing_id,
            "url": href,
            "price": "N/A",  # price is JS-rendered on search page; filled in by fetch_listing_detail
            "locality": locality.replace("-", " ").title(),
            "zip": zip_code,
            "type": prop_type,
            "bedrooms": "?",
            "area": area,
        })

    log.info(f"Extracted {len(listings)} listings.")
    return listings


def fetch_listing_detail(listing: dict) -> dict:
    time.sleep(random.uniform(2, 5))  # polite delay
    try:
        r = requests.get(listing["url"], headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning(f"Failed to fetch listing {listing['id']}: {e}")
        return listing

    soup = BeautifulSoup(r.text, "html.parser")

    # Price, bedrooms, area from window.classified JSON
    for script in soup.find_all("script"):
        t = script.string or ""
        if "window.classified" in t:
            m = re.search(r"window\.classified\s*=\s*(\{.*?\});", t, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1))
                    price_data = data.get("price", {}) or {}
                    listing["price"] = price_data.get("mainValue", "N/A")
                    prop = data.get("property", {}) or {}
                    listing["bedrooms"] = prop.get("bedroomCount", "?")
                    listing["area"] = prop.get("netHabitableSurface", listing.get("area", "?"))
                except Exception:
                    pass
            break

    # Description from HTML
    desc_el = soup.select_one("p.classified__description, .classified__description")
    listing["description"] = desc_el.get_text(separator=" ", strip=True)[:500] if desc_el else ""

    # Private owner detection
    is_private = False
    agent_section = soup.select_one("[class*='agency'], [class*='agent'], [class*='advertiser']")
    if agent_section:
        agent_text = agent_section.get_text().lower()
        is_private = "private" in agent_text or "particulier" in agent_text or "eigenaar" in agent_text
    listing["is_private"] = is_private

    return listing


# ─────────────────────────────────────────────
#  MAIN JOB
# ─────────────────────────────────────────────
def run_scraper():
    log.info("=" * 50)
    log.info("Running scraper job...")

    listings = fetch_search_results()

    if not listings:
        log.warning("No listings found. Immoweb may have changed its structure.")
        return

    detailed_listings = []
    for listing in listings:
        log.info(f"Fetching details for listing {listing['id']}...")
        detailed_listings.append(fetch_listing_detail(listing))

    write_csv(detailed_listings)
    log.info(f"Done. Processed {len(detailed_listings)} listings.")


# ─────────────────────────────────────────────
#  SCRIPT ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Immoweb CSV scraper starting up...")
    run_scraper()
