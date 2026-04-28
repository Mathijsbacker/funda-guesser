"""
funda_scraper.py
────────────────
Scrapes 15 fresh "Koop woningen" listings from funda.nl every night.
Outputs: daily_houses.json  (overwrites previous file)
"""

import json
import random
import time
import re
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── CONFIG ────────────────────────────────────────────────────────────────────
TARGET_URL = (
    "https://www.funda.nl/zoeken/koop?"
    "selected_area=%5B%22nederland%22%5D"
    "&sort=%22date_down%22"          # newest first
)
NUM_HOUSES = 15
OUTPUT_FILE = Path(__file__).parent / "daily_houses.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

EXTRA_HEADERS = {
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

# ── HELPER FUNCTIONS ──────────────────────────────────────────────────────────
def random_delay(lo: float = 1.5, hi: float = 4.0) -> None:
    time.sleep(random.uniform(lo, hi))

def parse_price(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None

def parse_m2(text: str) -> int | None:
    m = re.search(r"(\d+)\s*m", text)
    return int(m.group(1)) if m else None

# ── MAIN SCRAPE FUNCTION ──────────────────────────────────────────────────────
def scrape() -> list[dict]:
    houses: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 768},
            extra_http_headers=EXTRA_HEADERS,
        )

        page = ctx.new_page()

        print(f"[{datetime.now():%H:%M:%S}] Navigating to Funda …")
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45_000)

        random_delay(2, 4)

        # 1. Accept cookie banner
        for selector in ['button[id*="accept"]', 'button:has-text("Accepteren")']:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=3000):
                    btn.click()
                    random_delay(1, 2)
                    break
            except:
                pass

        # 2. TRIGGER LAZY LOADING (Cruciaal voor de afbeeldingen!)
        print("Scrolling to load images...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        random_delay(1, 2)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        random_delay(2, 3)
        page.evaluate("window.scrollTo(0, 0)") # Terug naar boven voor de zekerheid

        # 3. Scrape listing cards
        card_selectors = ['[data-test-id="search-result-item"]', 'div[class*="search-result"]']
        cards = None
        for sel in card_selectors:
            found = page.locator(sel)
            if found.count() > 0:
                cards = found
                break

        if not cards or cards.count() == 0:
            raise RuntimeError("Geen huizen gevonden op de pagina.")

        for i in range(min(NUM_HOUSES, cards.count())):
            card = cards.nth(i)
            try:
                # Image URL verbetering (pakt srcset als src leeg is/404 geeft)
                img_el = card.locator("img").first
                raw_img = (
                    img_el.get_attribute("srcset") or 
                    img_el.get_attribute("data-src") or 
                    img_el.get_attribute("src") or ""
                )
                # Pak de eerste schone URL uit een mogelijke srcset
                image_url = raw_img.split(",")[0].split(" ")[0].strip()

                # Price
                price_el = card.locator('[class*="price"]').first
                price = parse_price(price_el.inner_text()) if price_el.count() > 0 else None

                # Area (m²)
                m2_el = card.locator('li:has-text("m²"), span:has-text("m²")').first
                m2 = parse_m2(m2_el.inner_text()) if m2_el.count() > 0 else None

                # City
                addr_el = card.locator('[class*="address"]').first
                addr_raw = addr_el.inner_text() if addr_el.count() > 0 else ""
                city_m = re.search(r"\d{4}\s*[A-Z]{2}\s+(.+)", addr_raw)
                city = city_m.group(1).strip() if city_m else addr_raw.strip()

                if price:
                    houses.append({
                        "id": i + 1,
                        "image": image_url,
                        "price": price,
                        "m2": m2,
                        "city": city,
                        "date_scraped": datetime.now().strftime("%Y-%m-%d")
                    })
                    print(f"  ✓ {city} toegevoegd.")

            except Exception as e:
                print(f"  ✗ Fout bij huis #{i+1}: {e}")

        browser.close()
    return houses

def main() -> None:
    houses = scrape()
    if len(houses) >= 5:
        OUTPUT_FILE.write_text(json.dumps(houses, indent=2), encoding="utf-8")
        print(f"\n✅ {len(houses)} huizen opgeslagen in {OUTPUT_FILE}")
    else:
        print("❌ Te weinig huizen gevonden, bestand niet overschreven.")

if __name__ == "__main__":
    main()
