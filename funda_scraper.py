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

def best_url_from_srcset(srcset: str) -> str:
    """Kiest de URL met de hoogste breedte (bijv. '1200w') uit een srcset-string."""
    best_url, best_w = "", 0
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        url = tokens[0]
        w = 0
        if len(tokens) > 1 and tokens[-1].endswith("w"):
            try:
                w = int(tokens[-1][:-1])
            except ValueError:
                pass
        if w > best_w or best_url == "":
            best_w, best_url = w, url
    return best_url

def download_image(url: str, filename_stem: str) -> str:
    """Downloadt de afbeelding en geeft het relatieve lokale pad terug."""
    try:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": "https://www.funda.nl/",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            ct = response.headers.get("Content-Type", "image/jpeg")
            ext = "png" if "png" in ct else "webp" if "webp" in ct else "jpg"
            fname = f"{filename_stem}.{ext}"
            path = IMAGE_DIR / fname
            path.write_bytes(response.content)
            print(f"      ✓ {fname} ({len(response.content) // 1024} KB)")
            return f"images/{fname}"
        else:
            print(f"      ! HTTP {response.status_code}")
    except Exception as e:
        print(f"      ! Download mislukt: {e}")
    return url

def parse_price(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None

def parse_m2(text: str) -> int | None:
    m = re.search(r"(\d+)\s*m", text)
    return int(m.group(1)) if m else None

def parse_int(text: str) -> int | None:
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else None

def parse_energy_label(text: str) -> str | None:
    """Haal energielabel op uit tekst, bijv. 'A+', 'B', 'C'."""
    m = re.search(r"\b([A-G]\+{0,2})\b", text.strip())
    return m.group(1) if m else None

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
        except:
            pass

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
                # ── Afbeelding ────────────────────────────────────────────
                img_el = card.locator("img").first
                srcset = img_el.get_attribute("srcset") or ""
                src    = img_el.get_attribute("src") or ""
                remote_url = best_url_from_srcset(srcset) if srcset else src

                local_img_path = ""
                if remote_url and remote_url.startswith("http"):
                    print(f"  → Downloading image for house #{i+1} …")
                    local_img_path = download_image(remote_url, f"house_{i+1}")

                # ── Prijs ─────────────────────────────────────────────────
                price_el = card.locator('[class*="price"]').first
                price = parse_price(price_el.inner_text()) if price_el.count() > 0 else 0

                # ── Stad ──────────────────────────────────────────────────
                addr_el = card.locator('[class*="address"]').first
                city = addr_el.inner_text().split("\n")[-1].strip() if addr_el.count() > 0 else "Onbekend"

                # ── Oppervlakte (m²) ──────────────────────────────────────
                # Funda toont kenmerken als losse elementen; probeer meerdere selectors
                m2 = None
                try:
                    for sel in [
                        '[data-test-id="object-primary-info"] li',
                        '[class*="kenmerken"] li',
                        '[class*="object-kenmerken"] li',
                        '[class*="listing-features"] li',
                    ]:
                        items = card.locator(sel)
                        for j in range(items.count()):
                            txt = items.nth(j).inner_text()
                            if "m²" in txt or "m2" in txt.lower():
                                m2 = parse_m2(txt)
                                break
                        if m2:
                            break
                except:
                    pass

                # ── Slaapkamers ───────────────────────────────────────────
                bedrooms = None
                try:
                    for sel in [
                        '[data-test-id*="bedroom"]',
                        '[aria-label*="slaapkamer"]',
                        '[class*="bedroom"]',
                    ]:
                        el = card.locator(sel).first
                        if el.count() > 0:
                            bedrooms = parse_int(el.inner_text())
                            break
                    # Fallback: zoek in kenmerken-items naar slaapkamer-tekst
                    if bedrooms is None:
                        for sel in [
                            '[class*="kenmerken"] li',
                            '[class*="listing-features"] li',
                        ]:
                            items = card.locator(sel)
                            for j in range(items.count()):
                                txt = items.nth(j).inner_text().lower()
                                if "slaapkamer" in txt or "bedroom" in txt:
                                    bedrooms = parse_int(txt)
                                    break
                            if bedrooms is not None:
                                break
                except:
                    pass

                # ── Energielabel ──────────────────────────────────────────
                energy_label = None
                try:
                    for sel in [
                        '[data-test-id*="energy-label"]',
                        '[class*="energy-label"]',
                        '[class*="energylabel"]',
                        '[class*="energy_label"]',
                        '[aria-label*="energielabel"]',
                    ]:
                        el = card.locator(sel).first
                        if el.count() > 0:
                            raw = el.inner_text().strip() or el.get_attribute("aria-label") or ""
                            energy_label = parse_energy_label(raw)
                            break
                except:
                    pass

                houses.append({
                    "id": i + 1,
                    "image": local_img_path,
                    "price": price,
                    "m2": m2,
                    "bedrooms": bedrooms,
                    "energy_label": energy_label,
                    "city": city,
                })
                print(f"  ✓ {city} | €{price} | {m2}m² | {bedrooms}k | {energy_label}")

            except Exception as e:
                print(f"  ✗ Fout bij huis #{i+1}: {e}")

        browser.close()
    return houses

def main():
    houses = scrape()
    if len(houses) >= 5:
        OUTPUT_FILE.write_text(json.dumps(houses, indent=2), encoding="utf-8")
        print(f"✅ Klaar! {len(houses)} huizen en beelden verwerkt.")
    else:
        print(f"⚠️  Slechts {len(houses)} huizen gevonden — JSON niet overschreven.")

if __name__ == "__main__":
    main()
