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
    # Zoekt naar labels A t/m G (inclusief +jes)
    match = re.search(r'[A-G][\+]*', txt, re.IGNORECASE)
    return match.group(0).upper() if match else "N/A"

# ── SCRAPER ───────────────────────────────────────────────────────────────────
def scrape() -> list[dict]:
    houses: list[dict] = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()

        print(f"[{datetime.now():%H:%M:%S}] Navigeren naar Funda...")
        
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
            random_delay(2, 4)

            # Sluit cookie banner
            try:
                page.locator('button:has-text("Accepteren")').first.click(timeout=3000)
            except:
                pass

            # Scrollen voor lazy-loading afbeeldingen
            for _ in range(4):
                page.mouse.wheel(0, 1200)
                random_delay(0.5, 1.0)

            # GEFIXTE SELECTOR: We gebruiken een attribute selector om de @ te omzeilen
            cards = page.locator('div[class*="@container"][class*="border-b"]')
            count = min(NUM_HOUSES, cards.count())
            print(f"Gevonden listings: {cards.count()}. Verwerken: {count}")

            for i in range(count):
                card = cards.nth(i)
                try:
                    # 1. Stad (Ede, Utrecht, etc.)
                    # Funda HTML structuur: [data-testid="listingDetailsAddress"] bevat postcode + stad
                    city = "Onbekend"
                    addr_el = card.locator('[data-testid="listingDetailsAddress"]').first
                    if addr_el.count() > 0:
                        addr_text = addr_el.inner_text().strip()
                        # De stad is meestal het laatste woord (bijv. "6711 AP Ede")
                        city = addr_text.split('\n')[-1].split(' ')[-1].strip()

                    # 2. Prijs
                    price_el = card.locator('div:has-text("€")').first
                    price = parse_price(price_el.inner_text()) if price_el.count() > 0 else 0

                    # 3. Kenmerken (m2 en Slaapkamers)
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
                    # Funda gebruikt vaak een badge met de letter
                    label_el = card.locator('span[class*="bg-energy-label"], span:has-text("Label")').first
                    if label_el.count() > 0:
                        energy_label = parse_energy_label(label_el.inner_text())

                    # 5. Afbeelding
                    img_el = card.locator('img').first
                    local_img_path = ""
                    if img_el.count() > 0:
                        img_url = img_el.get_attribute("srcset") or img_el.get_attribute("src") or ""
                        if "," in img_url:
                            img_url = img_url.split(",")[0].split(" ")[0].strip()
                        
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
                    print(f"  ✓ #{i+1} | {city} | {m2}m² | Label {energy_label}")

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
