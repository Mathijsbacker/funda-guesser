import json
import random
import time
import re
import requests
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── CONFIGURATIE ──────────────────────────────────────────────────────────────
TARGET_URL = (
    "https://www.funda.nl/zoeken/koop?"
    "selected_area=%5B%22nederland%22%5D"
    "&sort=%22date_down%22"
)
NUM_HOUSES = 15
BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "daily_houses.json"
IMAGE_DIR = BASE_DIR / "images"
IMAGE_DIR.mkdir(exist_ok=True)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ── HELPERS ───────────────────────────────────────────────────────────────────
def random_delay(lo: float = 1.5, hi: float = 4.0):
    time.sleep(random.uniform(lo, hi))

def download_image(url: str, filename: str) -> str:
    """Downloadt de afbeelding en geeft het lokale pad terug."""
    if not url: return ""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": random.choice(USER_AGENTS)})
        if resp.status_code == 200:
            path = IMAGE_DIR / filename
            path.write_bytes(resp.content)
            return f"images/{filename}"
    except Exception as e:
        print(f"Fout bij download image: {e}")
    return ""

def parse_price(txt: str) -> int:
    nums = re.sub(r'[^\d]', '', txt)
    return int(nums) if nums else 0

def parse_m2(txt: str) -> int:
    match = re.search(r'(\d+)\s*m²', txt)
    return int(match.group(1)) if match else 0

def parse_int(txt: str) -> int:
    match = re.search(r'(\d+)', txt)
    return int(match.group(1)) if match else 0

def parse_energy_label(txt: str) -> str:
    # Zoekt naar labels A++++ t/m G in de tekst
    match = re.search(r'[A-G][\+]*', txt, re.IGNORECASE)
    return match.group(0).upper() if match else "N/A"

# ── SCRAPER ───────────────────────────────────────────────────────────────────
def scrape() -> list[dict]:
    houses: list[dict] = []
    
    with sync_playwright() as p:
        # Browser opstarten
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        print(f"[{datetime.now():%H:%M:%S}] Starten met scrapen van {TARGET_URL}...")
        
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
            random_delay(2, 4)

            # Sluit cookie banner indien aanwezig
            try:
                page.locator('button:has-text("Accepteren")').first.click(timeout=3000)
            except:
                pass

            # Scroll naar beneden om lazy-loading afbeeldingen te triggeren
            for _ in range(3):
                page.mouse.wheel(0, 1500)
                random_delay(0.5, 1.0)

            # De listing containers uit index (7).html: div.@container.border-b
            cards = page.locator('div.@container.border-b.pb-3')
            count = min(NUM_HOUSES, cards.count())
            print(f"Gevonden listings: {cards.count()}. We verwerken de eerste {count}.")

            for i in range(count):
                card = cards.nth(i)
                try:
                    # 1. Stad (uit de adresregel: "6711 AP Ede" -> "Ede")
                    addr_el = card.locator('[data-testid="listingDetailsAddress"]').first
                    city = "Onbekend"
                    if addr_el.count() > 0:
                        addr_text = addr_el.inner_text().strip()
                        # Neem het laatste woord van de adresregel als stad
                        city = addr_text.split(' ')[-1]

                    # 2. Prijs (optioneel, maar vaak handig)
                    price_el = card.locator('[data-testid="listing-price"]').first
                    price = parse_price(price_el.inner_text()) if price_el.count() > 0 else 0

                    # 3. Kenmerken: m2 en Slaapkamers
                    # Deze staan in <li> elementen binnen de kaart
                    m2 = 0
                    bedrooms = 0
                    specs = card.locator('li')
                    for j in range(specs.count()):
                        spec_text = specs.nth(j).inner_text().lower()
                        if "m²" in spec_text:
                            m2 = parse_m2(spec_text)
                        elif "kamer" in spec_text:
                            bedrooms = parse_int(spec_text)

                    # 4. Energielabel
                    energy_label = "N/A"
                    # Zoek naar een span die de tekst 'Label' bevat
                    label_el = card.locator('span:has-text("Label")').first
                    if label_el.count() > 0:
                        energy_label = parse_energy_label(label_el.inner_text())

                    # 5. Afbeelding downloaden
                    img_el = card.locator('img').first
                    img_url = ""
                    if img_el.count() > 0:
                        # Probeer srcset voor hogere resolutie, anders src
                        img_url = img_el.get_attribute("srcset") or img_el.get_attribute("src") or ""
                        if "," in img_url: # Pak de eerste url uit srcset
                            img_url = img_url.split(",")[0].split(" ")[0].strip()
                    
                    local_img_path = ""
                    if img_url:
                        img_filename = f"house_{i+1}.jpg"
                        local_img_path = download_image(img_url, img_filename)

                    houses.append({
                        "id": i + 1,
                        "city": city,
                        "m2": m2,
                        "bedrooms": bedrooms,
                        "energy_label": energy_label,
                        "price": price,
                        "image": local_img_path
                    })
                    print(f"  ✓ #{i+1} in {city}: {m2}m², {bedrooms} slp., Label {energy_label}")

                except Exception as card_err:
                    print(f"  ✗ Fout bij listing {i+1}: {card_err}")

        except Exception as e:
            print(f"KRITIEKE FOUT: {e}")
        finally:
            browser.close()
            
    return houses

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("--- START FUNDA SCRAPER ---")
    results = scrape()
    
    if results:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n✅ Succes! {len(results)} huizen opgeslagen in {OUTPUT_FILE.name}")
    else:
        print("\n⚠️ Geen resultaten gevonden.")

if __name__ == "__main__":
    main()
