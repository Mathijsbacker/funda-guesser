"""
funda_scraper.py
────────────────
Scrapes 15 fresh "Koop woningen" listings from funda.nl every night.
Outputs: daily_houses.json  (overwrites previous file)

Requirements:
    pip install playwright python-dotenv
    playwright install chromium
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

# Stealth headers — rotate between a handful of realistic UAs
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

EXTRA_HEADERS = {
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-CH-UA-Platform": '"Windows"',
}
# ─────────────────────────────────────────────────────────────────────────────


def random_delay(lo: float = 1.5, hi: float = 4.0) -> None:
    """Sleep a human-like random duration."""
    time.sleep(random.uniform(lo, hi))


def parse_price(text: str) -> int | None:
    """'€ 425.000 k.k.' → 425000"""
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def parse_m2(text: str) -> int | None:
    """'112 m²' → 112"""
    m = re.search(r"(\d+)\s*m", text)
    return int(m.group(1)) if m else None


def scrape() -> list[dict]:
    houses: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        ctx = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 768},
            locale="nl-NL",
            timezone_id="Europe/Amsterdam",
            extra_http_headers=EXTRA_HEADERS,
        )

        # Remove the `navigator.webdriver` flag
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = ctx.new_page()

        print(f"[{datetime.now():%H:%M:%S}] Navigating to Funda …")
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30_000)
        except PWTimeout:
            print("Timeout on initial load — retrying once …")
            random_delay(3, 6)
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45_000)

        random_delay(2, 4)

        # Accept cookie banner if present
        for selector in [
            'button[id*="accept"]',
            'button[data-testid*="accept"]',
            'button:has-text("Accepteren")',
            'button:has-text("Akkoord")',
        ]:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    random_delay(1, 2)
                    break
            except Exception:
                pass

        # ── Scrape listing cards ───────────────────────────────────────────
        # Funda renders cards as <div data-test-id="search-result-item"> or similar
        card_selectors = [
            '[data-test-id="search-result-item"]',
            'div[class*="search-result"]',
            'li[class*="search-result"]',
        ]

        cards = None
        for sel in card_selectors:
            found = page.locator(sel)
            if found.count() > 0:
                cards = found
                print(f"Found {found.count()} cards with selector: {sel}")
                break

        if not cards or cards.count() == 0:
            # Fallback: dump page source for debugging
            Path("debug_page.html").write_text(page.content(), encoding="utf-8")
            raise RuntimeError(
                "No listing cards found — page structure may have changed. "
                "See debug_page.html for the raw HTML."
            )

        for i in range(min(NUM_HOUSES, cards.count())):
            card = cards.nth(i)
            random_delay(0.3, 0.8)   # polite micro-delay between each card

            try:
                # Image
                img_el = card.locator("img").first
                image_url = img_el.get_attribute("src") or img_el.get_attribute("data-src") or ""

                # Price
                price_el = card.locator('[class*="price"], [data-test-id*="price"]').first
                price_raw = price_el.inner_text() if price_el.count() > 0 else ""
                price = parse_price(price_raw)

                # Area (m²)
                m2_el = card.locator('li:has-text("m²"), span:has-text("m²")').first
                m2_raw = m2_el.inner_text() if m2_el.count() > 0 else ""
                m2 = parse_m2(m2_raw)

                # Bedrooms
                bed_el = card.locator('[class*="bedroom"], li:has([data-icon*="bed"])').first
                bed_raw = bed_el.inner_text() if bed_el.count() > 0 else ""
                bedrooms_m = re.search(r"(\d+)", bed_raw)
                bedrooms = int(bedrooms_m.group(1)) if bedrooms_m else None

                # Energy label
                label_el = card.locator('[class*="energy"], [data-test-id*="energy"]').first
                energy_label = label_el.inner_text().strip() if label_el.count() > 0 else "?"

                # City
                addr_el = card.locator('[class*="address"], [data-test-id*="address"]').first
                addr_raw = addr_el.inner_text() if addr_el.count() > 0 else ""
                # Typically "1234 AB  Amsterdam" — grab the city part after the postcode
                city_m = re.search(r"\d{4}\s*[A-Z]{2}\s+(.+)", addr_raw)
                city = city_m.group(1).strip() if city_m else addr_raw.strip()

                if price is None:
                    print(f"  ⚠ House #{i+1}: no price found, skipping")
                    continue

                house = {
                    "id": i + 1,
                    "image": image_url,
                    "price": price,
                    "m2": m2,
                    "bedrooms": bedrooms,
                    "energy_label": energy_label,
                    "city": city,
                }
                houses.append(house)
                print(f"  ✓ House #{i+1}: {city} — €{price:,} ({m2} m²)")

            except Exception as e:
                print(f"  ✗ Error on house #{i+1}: {e}")

        browser.close()

    return houses


def main() -> None:
    print("=" * 50)
    print(f"Funda Scraper — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 50)

    houses = scrape()

    if len(houses) < 5:
        raise RuntimeError(
            f"Only scraped {len(houses)} houses — "
            "aborting to avoid overwriting good data."
        )

    # Pad or trim to exactly NUM_HOUSES
    houses = houses[:NUM_HOUSES]

    OUTPUT_FILE.write_text(
        json.dumps(houses, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✅ Saved {len(houses)} houses → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
