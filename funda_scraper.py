import json
import random
import time
import re
import requests
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── CONFIG ────────────────────────────────────────────────────────────────────
TARGET_URL = (
    "https://www.funda.nl/zoeken/koop?"
    "selected_area=%5B%22nederland%22%5D"
    "&sort=%22date_down%22"
)
NUM_HOUSES = 15
BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "daily_houses.json"
IMAGE_DIR = BASE_DIR / "images"
IMAGE_DIR.mkdir(exist_ok=True) # Maak de map aan als die niet bestaat

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ── HELPERS ───────────────────────────────────────────────────────────────────
def random_delay(lo: float = 1.5, hi: float = 4.0):
    time.sleep(random.uniform(lo, hi))

def download_image(url: str, filename: str) -> str:
    """Downloadt de afbeelding en geeft het lokale pad terug."""
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS), "Referer": "https://www.funda.nl/"}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            path = IMAGE_DIR / filename
            path.write_bytes(response.content)
            return f"images/{filename}" # Relatief pad voor in de JSON
    except Exception as e:
        print(f"      ! Download mislukt: {e}")
    return url # Fallback naar originele URL bij fout

# ... (parse_price en parse_m2 functies blijven hetzelfde als voorheen) ...
def parse_price(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None

def parse_m2(text: str) -> int | None:
    m = re.search(r"(\d+)\s*m", text)
    return int(m.group(1)) if m else None

def scrape() -> list[dict]:
    houses: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=random.choice(USER_AGENTS), viewport={"width": 1366, "height": 768})
        page = ctx.new_page()

        print(f"[{datetime.now():%H:%M:%S}] Navigating to Funda …")
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30_000)
        random_delay(2, 4)

        # Cookies accepteren
        try:
            page.locator('button:has-text("Accepteren")').first.click(timeout=3000)
        except: pass

        # Scrollen voor lazy loading
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        random_delay(1, 2)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        random_delay(2, 3)

        cards = page.locator('[data-test-id="search-result-item"]')
        count = min(NUM_HOUSES, cards.count())

        for i in range(count):
            card = cards.nth(i)
            try:
                # 1. Haal de URL van de afbeelding op
                img_el = card.locator("img").first
                raw_img = img_el.get_attribute("srcset") or img_el.get_attribute("src") or ""
                remote_url = raw_img.split(",")[0].split(" ")[0].strip()

                # 2. DOWNLOAD de afbeelding
                local_img_path = ""
                if remote_url:
                    img_name = f"house_{i+1}.jpg"
                    print(f"  → Downloading image for house #{i+1}...")
                    local_img_path = download_image(remote_url, img_name)

                # 3. Rest van de data
                price_el = card.locator('[class*="price"]').first
                price = parse_price(price_el.inner_text()) if price_el.count() > 0 else 0
                
                addr_el = card.locator('[class*="address"]').first
                city = addr_el.inner_text().split("\n")[-1].strip() if addr_el.count() > 0 else "Onbekend"

                houses.append({
                    "id": i + 1,
                    "image": local_img_path, # Nu een lokaal pad!
                    "price": price,
                    "city": city
                })
                print(f"  ✓ {city} opgeslagen.")
            except Exception as e:
                print(f"  ✗ Fout bij huis #{i+1}: {e}")

        browser.close()
    return houses

def main():
    houses = scrape()
    if len(houses) >= 5:
        OUTPUT_FILE.write_text(json.dumps(houses, indent=2), encoding="utf-8")
        print(f"✅ Klaar! {len(houses)} huizen en beelden verwerkt.")

if __name__ == "__main__":
    main()
