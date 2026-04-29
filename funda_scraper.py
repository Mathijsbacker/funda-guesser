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
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS), "Referer": "https://www.funda.nl/"}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            path = IMAGE_DIR / filename
            path.write_bytes(response.content)
            return f"images/{filename}"
    except Exception as e:
        print(f"      ! Download mislukt: {e}")
    return url


def parse_price(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def parse_m2(text: str) -> int | None:
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else None


def parse_city(text: str) -> str:
    """Haal stadsnaam op uit tekst als '6711 AP Ede' → 'Ede'."""
    m = re.search(r"\d{4}\s*[A-Z]{2}\s+(.+)", text.strip())
    return m.group(1).strip() if m else text.strip()


def scrape() -> list[dict]:
    houses: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 768},
        )
        page = ctx.new_page()

        print(f"[{datetime.now():%H:%M:%S}] Navigating to Funda …")
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30_000)
        random_delay(2, 4)

        # Cookies accepteren
        try:
            page.locator('button:has-text("Accepteren")').first.click(timeout=3000)
            random_delay(1, 2)
        except Exception:
            pass

        # Scrollen voor lazy loading
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        random_delay(1, 2)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        random_delay(2, 3)

        # ── Kaarten selecteren ────────────────────────────────────────────────
        # De kaarten hebben class "@container border-b pb-3"
        # We gebruiken een attribute-contains selector (@ hoeft niet ge-escaped in de string-waarde)
        cards = page.locator('div[class*="@container"][class*="border-b"]')
        count = min(NUM_HOUSES, cards.count())
        print(f"[{datetime.now():%H:%M:%S}] {cards.count()} kaarten gevonden, verwerk er {count} …")

        for i in range(count):
            card = cards.nth(i)
            try:
                # ── Afbeelding ────────────────────────────────────────────────
                img_el = card.locator("img").first
                remote_url = ""
                if img_el.count() > 0:
                    srcset = img_el.get_attribute("srcset") or ""
                    if srcset:
                        # Neem de laatste (hoogste resolutie) entry uit de srcset
                        parts = [p.strip() for p in srcset.split(",")]
                        remote_url = parts[-1].split(" ")[0].strip()
                    if not remote_url:
                        remote_url = img_el.get_attribute("src") or ""

                local_img_path = remote_url
                if remote_url and remote_url.startswith("http"):
                    img_name = f"house_{i+1}.jpg"
                    print(f"  → Downloading image for house #{i+1}...")
                    local_img_path = download_image(remote_url, img_name)

                # ── Adres & Stad ──────────────────────────────────────────────
                # <a data-testid="listingDetailsAddress"> (let op: geen koppelteken!)
                addr_link = card.locator('[data-testid="listingDetailsAddress"]').first
                street = ""
                city = "Onbekend"
                if addr_link.count() > 0:
                    # Straat: eerste <span class="truncate">
                    street_el = addr_link.locator("span.truncate").first
                    if street_el.count() > 0:
                        street = street_el.inner_text().strip()
                    # Stad: div met class "text-neutral-80" → bevat "1234 AB Plaatsnaam"
                    city_el = addr_link.locator('div[class*="text-neutral-80"]').first
                    if city_el.count() > 0:
                        city = parse_city(city_el.inner_text())

                # ── Prijs ─────────────────────────────────────────────────────
                # De prijs staat in een div.truncate die een '€' bevat
                price = 0
                price_candidates = card.locator("div.truncate")
                for j in range(price_candidates.count()):
                    txt = price_candidates.nth(j).inner_text()
                    if "€" in txt:
                        price = parse_price(txt) or 0
                        break

                # ── Features (m², slaapkamers, energielabel) ──────────────────
                # Ze staan als <li> elementen in een <ul>:
                # bijv. ['31 m²', '1', 'A']  of  ['113 m²', '152 m²', '5', 'B']
                m2 = None
                bedrooms = None
                energy_label = None
                feature_items = card.locator("ul li")
                feature_texts = []
                for j in range(feature_items.count()):
                    feature_texts.append(feature_items.nth(j).inner_text().strip())

                # m²: eerste feature die 'm²' bevat (woonoppervlak, niet perceeloppervlak)
                for ft in feature_texts:
                    if "m²" in ft or "m2" in ft.lower():
                        m2 = parse_m2(ft)
                        break

                # slaapkamers: eerste feature die puur een getal is
                for ft in feature_texts:
                    if re.match(r"^\d+$", ft):
                        bedrooms = int(ft)
                        break

                # energielabel: laatste feature als het een geldig label is (A–G met optionele plustekens)
                if feature_texts:
                    last = feature_texts[-1]
                    if re.match(r"^[A-G]\+*$", last):
                        energy_label = last

                houses.append({
                    "id": i + 1,
                    "image": local_img_path,
                    "street": street,
                    "city": city,
                    "price": price,
                    "m2": m2,
                    "bedrooms": bedrooms,
                    "energy_label": energy_label,
                })
                print(f"  ✓ #{i+1} {street}, {city} | €{price:,} | {m2}m² | {bedrooms}k | {energy_label}")

            except Exception as e:
                print(f"  ✗ Fout bij huis #{i+1}: {e}")

        browser.close()
    return houses


def main():
    houses = scrape()
    if len(houses) >= 5:
        OUTPUT_FILE.write_text(json.dumps(houses, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✅ Klaar! {len(houses)} huizen opgeslagen in {OUTPUT_FILE}")
    else:
        print(f"⚠️  Slechts {len(houses)} huizen gevonden — JSON niet overschreven.")


if __name__ == "__main__":
    main()
