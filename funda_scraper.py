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
        ctx = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 768},
            locale="nl-NL",
        )
        page = ctx.new_page()

        print(f"[{datetime.now():%H:%M:%S}] Navigating to Funda …")
        page.goto(TARGET_URL, wait_until="networkidle", timeout=45_000)
        random_delay(2, 4)

        # Cookies accepteren
        for label in ["Accepteren", "Alles accepteren", "Akkoord", "Accept"]:
            try:
                page.locator(f'button:has-text("{label}")').first.click(timeout=2000)
                print(f"  ✓ Cookie-dialog gesloten ({label})")
                random_delay(1, 2)
                break
            except:
                pass

        # Wacht op listings
        try:
            page.wait_for_selector('a[data-testid="listingDetailsAddress"]', timeout=15_000)
        except:
            print("⚠️  Timeout wachten op listings")

        # Scrollen voor lazy loading
        for fraction in [0.25, 0.5, 0.75, 1.0]:
            page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {fraction})")
            random_delay(0.8, 1.5)

        # ── Extraheer alle listings in één JS-call ────────────────────────
        raw_listings = page.evaluate(f"""
        () => {{
            const results = [];
            const links = document.querySelectorAll('a[data-testid="listingDetailsAddress"]');

            links.forEach((link) => {{
                // Ga omhoog naar de kaart-container
                const card = link.closest('[class*="@container"]')
                           || link.closest('.flex-col')
                           || link.parentElement?.parentElement?.parentElement;
                if (!card) return;

                // Afbeelding: pak de img met srcset die een cloud.funda.nl URL heeft
                const img = card.querySelector('img[srcset*="cloud.funda"]')
                         || card.querySelector('img[src*="cloud.funda"]');
                const srcset = img ? (img.getAttribute('srcset') || '') : '';
                const src    = img ? (img.getAttribute('src') || '') : '';

                // Stad (postcode + plaatsnaam, bijv. "7601 EH Almelo")
                const cityEl = link.querySelector('.text-neutral-80');
                const city   = cityEl ? cityEl.innerText.trim() : 'Onbekend';

                // Prijs (bijv. "€ 184.500 k.k.")
                const priceEl = card.querySelector('.font-semibold .truncate');
                const priceText = priceEl ? priceEl.innerText.trim() : '';

                // Stats: elk <li> in de ul bevat een SVG + <span>
                // Volgorde: m², [perceel m²], slaapkamers, energielabel
                const spans = [...card.querySelectorAll('ul li span')]
                    .map(s => s.innerText.trim())
                    .filter(s => s.length > 0);

                results.push({{ srcset, src, city, priceText, spans }});
            }});

            return results.slice(0, {NUM_HOUSES});
        }}
        """)

        print(f"  → {len(raw_listings)} listings gevonden in DOM")

        for i, listing in enumerate(raw_listings):
            try:
                # ── Afbeelding ────────────────────────────────────────────
                remote_url = best_url_from_srcset(listing["srcset"]) if listing["srcset"] else listing["src"]
                local_img_path = ""
                if remote_url and remote_url.startswith("http"):
                    print(f"  → Downloading image #{i+1} …")
                    local_img_path = download_image(remote_url, f"house_{i+1}")

                # ── Prijs ─────────────────────────────────────────────────
                price = parse_price(listing["priceText"]) or 0

                # ── Stad ──────────────────────────────────────────────────
                # cityEl bevat "7601 EH Almelo" — pak alles na de postcode
                city_raw = listing["city"]
                city_match = re.search(r"\d{4}\s*[A-Z]{2}\s+(.+)", city_raw)
                city = city_match.group(1).strip() if city_match else city_raw

                # ── Parse stats uit spans ─────────────────────────────────
                # Spans zijn bijv. ["62 m²", "1", "D"] of ["105 m²", "127 m²", "3", "A"]
                # m²-spans herkennen we door "m²", energielabel door A-F patroon,
                # slaapkamers is een los getal.
                m2 = None
                bedrooms = None
                energy_label = None
                m2_found = 0  # teller: eerste m² = woonoppervlak

                for span in listing["spans"]:
                    if "m²" in span:
                        val = parse_m2(span)
                        if val and m2_found == 0:
                            m2 = val        # eerste = woonoppervlak
                            m2_found += 1
                        else:
                            m2_found += 1   # tweede = perceeloppervlak, sla over
                    elif re.fullmatch(r"[A-G]\+{0,2}", span.strip()):
                        energy_label = span.strip()
                    elif re.fullmatch(r"\d{1,2}", span.strip()) and bedrooms is None:
                        bedrooms = int(span.strip())

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
                print(f"  ✗ Fout bij listing #{i+1}: {e}")

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
