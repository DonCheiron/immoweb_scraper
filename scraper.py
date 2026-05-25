"""
Immoweb Scraper — CSV export for local on-demand analysis.
Generates two CSVs: one for sales, one for rentals.
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
#  SEARCH FILTERS — edit these before running
#  Set a value to None to exclude that filter.
# ─────────────────────────────────────────────
min_bedroom   = 1
max_bedroom   = 1
min_surface   = 40
max_surface   = 70
postal_codes  = [1030]

# Sales-specific price range
min_price_sales = None
max_price_sales = 180000

# Rental-specific price range
min_price_rental = None
max_price_rental = None

# ─────────────────────────────────────────────
#  OUTPUT FILES
# ─────────────────────────────────────────────
CSV_SALES_FILE   = Path("immoweb_listings_for_sale.csv")
CSV_RENTAL_FILE  = Path("immoweb_listings_for_rent.csv")

CSV_FIELDS_SALES = [
    "id",
    "url",
    "price",
    "locality",
    "zip",
    "type",
    "bedrooms",
    "area",
]

CSV_FIELDS_RENTAL = [
    "id",
    "url",
    "price",
    "monthly_costs",
    "locality",
    "zip",
    "type",
    "bedrooms",
    "area",
]

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
#  URL BUILDER
# ─────────────────────────────────────────────
def build_url(mode: str) -> str:
    """Build the Immoweb search URL for 'for-sale' or 'for-rent'."""
    assert mode in ("for-sale", "for-rent")

    base = f"https://www.immoweb.be/en/search/apartment/{mode}?countries=BE"

    params = {}
    if min_bedroom   is not None: params["minBedroomCount"] = min_bedroom
    if max_bedroom   is not None: params["maxBedroomCount"] = max_bedroom
    if min_surface   is not None: params["minSurface"]      = min_surface
    if max_surface   is not None: params["maxSurface"]      = max_surface

    if postal_codes:
        codes = ",".join(f"BE-{c}" for c in postal_codes)
        params["postalCodes"] = codes

    if mode == "for-sale":
        if min_price_sales  is not None: params["minPrice"]   = min_price_sales
        if max_price_sales  is not None: params["maxPrice"]   = max_price_sales
        params["priceType"] = "SALE_PRICE"
    else:
        if min_price_rental is not None: params["minPrice"]   = min_price_rental
        if max_price_rental is not None: params["maxPrice"]   = max_price_rental
        params["priceType"] = "MONTHLY_RENTAL_PRICE"

    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}&{query}" if query else base


# ─────────────────────────────────────────────
#  CSV OUTPUT
# ─────────────────────────────────────────────
def write_csv(listings: list[dict], output_file: Path, fields: list[str]):
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fields)
        writer.writeheader()
        for listing in listings:
            writer.writerow({k: listing.get(k, "") for k in fields})
    log.info(f"Wrote {len(listings)} listings to {output_file}")


# ─────────────────────────────────────────────
#  IMMOWEB SCRAPING
# ─────────────────────────────────────────────
def fetch_search_results(search_url: str) -> list[dict]:
    log.info(f"Search URL: {search_url}")
    log.info("Fetching search results from Immoweb...")
    try:
        r = requests.get(search_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        log.info(f"Search page response: HTTP {r.status_code}")
    except Exception as e:
        log.error(f"Failed to fetch search page: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    listings = []

    cards = soup.select("article.card--result")
    log.info(f"Found {len(cards)} listing cards on the page.")
    for card in cards:
        a_tag = card.select_one("a[href*='/classified/']")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        url_match = re.search(r"/classified/([^/]+)/for-(?:rent|sale)/([^/]+)/(\d+)/(\d+)", href)
        if not url_match:
            continue
        prop_type, locality, zip_code, listing_id = url_match.groups()
        area = "?"
        prop_info = card.select_one(".card__information--property")
        if prop_info:
            area_match = re.search(r"(\d+)", prop_info.get_text())
            if area_match:
                area = area_match.group(1)
        log.info(f"  [{listing_id}] {prop_type} in {locality} {zip_code}, area={area}m²")
        listings.append({
            "id": listing_id,
            "url": href,
            "price": "N/A",
            "locality": locality.replace("-", " ").title(),
            "zip": zip_code,
            "type": prop_type,
            "bedrooms": "?",
            "area": area,
        })

    log.info(f"Extracted {len(listings)} listings.")
    return listings


def fetch_listing_detail(listing: dict, index: int, total: int) -> dict:
    log.info(f"[{index}/{total}] Fetching detail for listing {listing['id']} ({listing['locality']})...")
    time.sleep(random.uniform(2, 5))
    try:
        r = requests.get(listing["url"], headers=HEADERS, timeout=15)
        r.raise_for_status()
        log.info(f"[{index}/{total}] HTTP {r.status_code} OK")
    except Exception as e:
        log.warning(f"[{index}/{total}] Failed to fetch listing {listing['id']}: {e}")
        return listing

    soup = BeautifulSoup(r.text, "html.parser")

    for script in soup.find_all("script"):
        t = script.string or ""
        if "window.classified" in t:
            m = re.search(r"window\.classified\s*=\s*(\{.*?\});", t, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1))
                    price_data = data.get("price", {}) or {}
                    listing["price"] = price_data.get("mainValue", "N/A")
                    listing["monthly_costs"] = price_data.get("additionalValue", "")
                    prop = data.get("property", {}) or {}
                    listing["bedrooms"] = prop.get("bedroomCount", "?")
                    listing["area"] = prop.get("netHabitableSurface", listing.get("area", "?"))
                    log.info(f"[{index}/{total}] Price={listing['price']}, MonthlyCosts={listing['monthly_costs']}, Bedrooms={listing['bedrooms']}, Area={listing['area']}m²")
                except Exception as e:
                    log.warning(f"[{index}/{total}] Failed to parse window.classified: {e}")
            break

    return listing


# ─────────────────────────────────────────────
#  SCRAPER RUNNER
# ─────────────────────────────────────────────
def run_scraper(mode: str, output_file: Path):
    search_url = build_url(mode)
    log.info("=" * 50)
    log.info(f"Running scraper for {mode}...")
    log.info(f"URL: {search_url}")

    listings = fetch_search_results(search_url)
    if not listings:
        log.warning("No listings found. Immoweb may have changed its structure.")
        return

    total = len(listings)
    fields = CSV_FIELDS_RENTAL if mode == "for-rent" else CSV_FIELDS_SALES
    detailed = [fetch_listing_detail(l, i, total) for i, l in enumerate(listings, start=1)]
    write_csv(detailed, output_file, fields)
    log.info(f"Done. Processed {len(detailed)} listings → {output_file.resolve()}")


# ─────────────────────────────────────────────
#  SCRIPT ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Immoweb CSV scraper starting up...")
    run_scraper("for-sale", CSV_SALES_FILE)
    run_scraper("for-rent", CSV_RENTAL_FILE)
