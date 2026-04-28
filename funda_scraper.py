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

CARD_SELECTORS = [
    '[data-test-id="search-result-item"]',      # oud
    '[data-test-id="search-result"]',
    'div[class*="search-result"]',
    'div[class*="SearchResult"]',
    'li[class*="search-result"]',
    'article[class*="listing"]',
    'div[class*="listing-"]',
    'a[data-test-id*="object"]',
    '[class*="object-list"] > div',             # generieke fallback
]

def find_cards(page):
    """Probeer meerdere selectors; geef de eerste terug die resultaten geeft."""
    for sel in CARD_SELECTORS:
        loc = page.locator(sel)
        n = loc.count()
        if n > 0:
            print(f"  ✓ Selector gevonden: '{sel}' → {n} cards")
            return loc, n
    return None, 0

def scrape() -> list[dict]:
    houses: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 768},
            locale="nl-NL",
        )
        page = ctx.new_page()

        print(f"[{datetime.now():%H:%M:%S}] Navigating to Funda …")
        page.goto(TARGET_URL, wait_until="networkidle", timeout=45_000)
        random_delay(2, 4)

        # Cookies accepteren – probeer meerdere knopteksten
        for label in ["Accepteren", "Alles accepteren", "Akkoord", "Accept"]:
            try:
                page.locator(f'button:has-text("{label}")').first.click(timeout=2000)
                print(f"  ✓ Cookie-dialog gesloten ({label})")
                random_delay(1, 2)
                break
            except:
                pass

        # Wacht tot er iets zichtbaars op de pagina staat
        try:
            page.wait_for_selector("main", timeout=10_000)
        except:
            pass

        # Scrollen voor lazy loading
        for fraction in [0.25, 0.5, 0.75, 1.0]:
            page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {fraction})")
            random_delay(0.8, 1.5)

        # ── DEBUG: dump de eerste 3000 tekens HTML naar stdout ────────────
        html_snippet = page.content()[:3000]
        print(f"\n── PAGE HTML (eerste 3000 chars) ──\n{html_snippet}\n──────────────────────────────────\n")

        cards, count = find_cards(page)
        if count == 0:
            print("⚠️  Geen cards gevonden met bekende selectors!")
            # Dump alle unieke class-namen als extra debug-info
            classes = page.evaluate("""
                () => [...new Set(
                    [...document.querySelectorAll('*')]
                    .map(el => el.className)
                    .filter(c => typeof c === 'string' && c.includes('search') || (typeof c === 'string' && c.includes('listing')))
                )].slice(0, 40)
            """)
            print("Gevonden klassen met 'search'/'listing':", classes)
            browser.close()
            return []

        count = min(NUM_HOUSES, count)

        for i in range(count):
            card = cards.nth(i)
            try:
                full_text = card.inner_text()   # volledige tekst als fallback

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
                price = 0
                for sel in ['[class*="price"]', '[class*="Price"]', '[data-test-id*="price"]']:
                    el = card.locator(sel).first
                    if el.count() > 0:
                        price = parse_price(el.inner_text()) or 0
                        if price:
                            break
                if not price:
                    # fallback: zoek "€ 123.000" patroon in volledige tekst
                    m = re.search(r"€\s*([\d.,]+)", full_text)
                    if m:
                        price = parse_price(m.group(1)) or 0

                # ── Stad ──────────────────────────────────────────────────
                city = "Onbekend"
                for sel in ['[class*="address"]', '[class*="Address"]', '[class*="city"]',
                            '[data-test-id*="address"]', 'h2', 'h3']:
                    el = card.locator(sel).first
                    if el.count() > 0:
                        txt = el.inner_text().strip()
                        if txt:
                            city = txt.split("\n")[-1].strip()
                            break

                # ── Oppervlakte (m²) ──────────────────────────────────────
                m2 = None
                for sel in ['[data-test-id*="floor-area"]', '[class*="kenmerken"] li',
                            '[class*="features"] li', 'ul li']:
                    items = card.locator(sel)
                    for j in range(items.count()):
                        txt = items.nth(j).inner_text()
                        if "m²" in txt or "m2" in txt.lower():
                            m2 = parse_m2(txt)
                            break
                    if m2:
                        break
                if not m2:
                    m = re.search(r"(\d+)\s*m²", full_text)
                    if m:
                        m2 = int(m.group(1))

                # ── Slaapkamers ───────────────────────────────────────────
                bedrooms = None
                for sel in ['[data-test-id*="bedroom"]', '[aria-label*="slaapkamer"]',
                            '[class*="bedroom"]']:
                    el = card.locator(sel).first
                    if el.count() > 0:
                        bedrooms = parse_int(el.inner_text())
                        break
                if bedrooms is None:
                    for sel in ['[class*="kenmerken"] li', '[class*="features"] li', 'ul li']:
                        items = card.locator(sel)
                        for j in range(items.count()):
                            txt = items.nth(j).inner_text().lower()
                            if "slaapkamer" in txt or "bedroom" in txt:
                                bedrooms = parse_int(txt)
                                break
                        if bedrooms is not None:
                            break
                if bedrooms is None:
                    m = re.search(r"(\d+)\s*slaapkamer", full_text, re.I)
                    if m:
                        bedrooms = int(m.group(1))

                # ── Energielabel ──────────────────────────────────────────
                energy_label = None
                for sel in ['[data-test-id*="energy"]', '[class*="energy"]',
                            '[aria-label*="energie"]']:
                    el = card.locator(sel).first
                    if el.count() > 0:
                        raw = el.inner_text().strip() or el.get_attribute("aria-label") or ""
                        energy_label = parse_energy_label(raw)
                        break
                if not energy_label:
                    m = re.search(r"\benergie(?:label)?\s*:?\s*([A-G]\+{0,2})\b", full_text, re.I)
                    if m:
                        energy_label = m.group(1).upper()

                houses.append({
                    "id": i + 1,
                    "image": local_img_path,
                    "price": price,
                    "m2": m2,
                    "bedrooms": bedrooms,
                    "energy_label": energy_label,
                    "city": city,
                })
                print(f"  ✓ #{i+1} {city} | €{price} | {m2}m² | {bedrooms}k | {energy_label}")

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
