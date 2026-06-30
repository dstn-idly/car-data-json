import requests
from bs4 import BeautifulSoup
import json
import os
import re
import csv
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
SRC_DIR = "src"
PHOTOS_CACHE_PATH = os.path.join(SRC_DIR, "all_vehicle_photos.json")
GALLERY_MAX = 50          # hard cap on images enumerated per vehicle
HEAD_WORKERS = 8          # ThreadPoolExecutor workers for HEAD requests

# Shared session for HEAD requests (gallery enumeration)
_IMG_SESSION = requests.Session()
_IMG_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})

# Multi-word model names so we split "2013 Dodge Grand Caravan SE" correctly.
MULTI_WORD_MODELS = [
    "Grand Caravan", "Grand Cherokee", "Grand Wagoneer", "Santa Fe", "Santa Cruz",
    "Model 3", "Model S", "Model X", "Model Y", "Town & Country", "Town and Country",
    "Versa Note", "Kicks Play", "F-150", "F-250", "F-350", "Silverado 1500",
    "Silverado 2500", "Silverado 3500", "Sierra 1500", "Sierra 2500", "Ram 1500",
    "Ram 2500", "Ram 3500", "C-HR", "CX-5", "CX-9", "CX-30", "CX-50", "CR-V",
    "HR-V", "Outlander Sport", "Eclipse Cross", "Mirage G4", "Crown Victoria",
    "Land Cruiser", "Range Rover", "Wrangler Unlimited", "Pacifica Hybrid",
]


# ---------------------------------------------------------------------------
# Photos cache  (per-VIN gallery cache that persists across runs)
# ---------------------------------------------------------------------------
def load_photos_cache(path=PHOTOS_CACHE_PATH):
    """Load the persistent per-VIN photo cache.

    The file on disk (restored by git before each run) may be either:
      * a dict keyed by VIN  -> {"images": [...], "image": "..."}  (new format)
      * a list of records    (legacy format, possibly with "image_gallery")

    We normalise to a dict keyed by VIN. Any pre-existing gallery (legacy
    "image_gallery" or new "images") is preserved so we do NOT re-enumerate it.
    """
    cache = {}
    if not os.path.exists(path):
        return cache
    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"⚠️ Could not read photos cache ({e}); starting fresh.")
        return cache

    records = []
    if isinstance(raw, dict):
        for vin, val in raw.items():
            if isinstance(val, dict):
                rec = dict(val)
                rec["vin"] = vin
                records.append(rec)
    elif isinstance(raw, list):
        records = [r for r in raw if isinstance(r, dict)]

    for rec in records:
        vin = (rec.get("vin") or "").strip()
        if not vin or vin == "N/A":
            continue
        images = rec.get("images") or rec.get("image_gallery") or []
        images = [u for u in images if isinstance(u, str) and u]
        entry = {
            "images": images,
            "image": rec.get("image") or (images[0] if images else None),
        }
        cache[vin] = entry
    return cache


def save_photos_cache(cache, path=PHOTOS_CACHE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"💾 Photos cache written with {len(cache)} VINs -> {path}")


def enumerate_gallery(first_url):
    """Given a card's `_01` image url, enumerate _01,_02,... until the first
    404 (cap GALLERY_MAX). Returns a list of urls (at least [first_url] if the
    pattern doesn't match)."""
    if not first_url:
        return []

    m = re.match(r'^(.*_)(\d+)(\.[A-Za-z]+)$', first_url)
    if not m:
        # Not the enumerable _NN pattern; just keep the single image.
        return [first_url]

    prefix, num, ext = m.groups()
    width = len(num)  # preserve zero-padding (e.g. 2 -> "01")
    images = []
    i = 1
    while i <= GALLERY_MAX:
        url = f"{prefix}{i:0{width}d}{ext}"
        try:
            resp = _IMG_SESSION.head(url, timeout=10, allow_redirects=True)
        except requests.exceptions.RequestException:
            break
        if resp.status_code == 200:
            images.append(url)
            i += 1
        else:
            break

    if not images:
        # First request failed/redirected oddly; fall back to the known url.
        images = [first_url]
    return images


def resolve_galleries(vehicles, cache):
    """For every vehicle, attach an `images` gallery. Reuse the cache for known
    VINs; only enumerate (concurrently) the VINs missing from the cache.
    Updates `cache` in place."""
    to_fetch = {}  # vin -> first_url
    for v in vehicles:
        vin = v.get("vin")
        first_url = v.get("image")
        if not vin or vin == "N/A":
            # No VIN: can't cache; enumerate inline only if we have a url.
            v["images"] = [first_url] if first_url else []
            continue

        cached = cache.get(vin)
        if cached and cached.get("images"):
            v["images"] = list(cached["images"])
            if not v.get("image"):
                v["image"] = cached.get("image") or (cached["images"][0] if cached["images"] else None)
            continue

        if first_url:
            to_fetch[vin] = first_url
        else:
            v["images"] = []

    if to_fetch:
        print(f"🖼️  Enumerating galleries for {len(to_fetch)} new VIN(s)...")
        vins = list(to_fetch.keys())
        with ThreadPoolExecutor(max_workers=HEAD_WORKERS) as ex:
            results = list(ex.map(lambda vin: (vin, enumerate_gallery(to_fetch[vin])), vins))
        result_map = dict(results)
        for v in vehicles:
            vin = v.get("vin")
            if vin in result_map:
                imgs = result_map[vin]
                v["images"] = imgs
                cache[vin] = {"images": imgs, "image": imgs[0] if imgs else v.get("image")}

    # Final safety: ensure every vehicle has an `images` key.
    for v in vehicles:
        if "images" not in v:
            img = v.get("image")
            v["images"] = [img] if img else []

    return vehicles


# ---------------------------------------------------------------------------
# Marketplace inference helpers (reused from kphynn orlandonissan scraper)
# ---------------------------------------------------------------------------
def _infer_color(color_str):
    if not color_str:
        return "Other"
    color_str = color_str.lower()
    mapping = {
        "black": "Black", "white": "White", "gray": "Gray", "grey": "Gray",
        "silver": "Silver", "blue": "Blue", "red": "Red", "brown": "Brown",
        "green": "Green", "beige": "Beige", "tan": "Tan", "gold": "Gold",
        "orange": "Orange", "yellow": "Yellow", "charcoal": "Charcoal",
        "maroon": "Red", "burgundy": "Red", "pearl": "White",
    }
    for k, v in mapping.items():
        if k in color_str:
            return v
    return "Other"


def _infer_body_type(model, url):
    if not model:
        return "Other"
    model_l = model.lower()
    url_l = url.lower() if url else ""
    if "truck" in model_l or "titan" in model_l or "frontier" in model_l or "tacoma" in model_l or "f-150" in model_l or "silverado" in model_l or "sierra" in model_l or "ram" in model_l:
        return "Truck"
    if "van" in url_l or "caravan" in model_l or "pacifica" in model_l or "carnival" in model_l or "quest" in model_l or "odyssey" in model_l or "sienna" in model_l:
        return "Minivan"
    if ("suv" in url_l or "sport-utility" in url_l or "pathfinder" in model_l or "rogue" in model_l
            or "murano" in model_l or "armada" in model_l or "kicks" in model_l or "equinox" in model_l
            or "compass" in model_l or "cherokee" in model_l or "explorer" in model_l or "tahoe" in model_l
            or "cr-v" in model_l or "rav4" in model_l or "highlander" in model_l or "outlander" in model_l):
        return "SUV"
    if "sedan" in model_l or "altima" in model_l or "sentra" in model_l or "maxima" in model_l or "versa" in model_l or "camry" in model_l or "accord" in model_l or "corolla" in model_l:
        return "Sedan"
    if "coupe" in model_l or model_l == "z" or "370z" in model_l:
        return "Coupe"
    if "hatchback" in url_l or "integra" in model_l or "versa note" in model_l or "leaf" in model_l:
        return "Hatchback"
    if "convertible" in model_l:
        return "Convertible"
    return "Other"


def _infer_fuel_type(engine_str):
    if not engine_str:
        return "Gasoline"
    e = engine_str.lower()
    if "electric" in e or e.strip() == "leaf":
        return "Electric"
    if "plug-in" in e:
        return "Plug-in hybrid"
    if "hybrid" in e:
        return "Hybrid"
    if "diesel" in e:
        return "Diesel"
    if "ethanol" in e or "flex" in e:
        return "Gasoline"
    if "gas" in e or "unleaded" in e or "v6" in e or "v-6" in e or "v8" in e or "v-8" in e or "i4" in e or "i-4" in e:
        return "Gasoline"
    return "Gasoline"


def _infer_transmission(trans_str):
    if not trans_str:
        return "Automatic transmission"
    t = trans_str.lower()
    if "manual" in t:
        return "Manual transmission"
    return "Automatic transmission"


def split_name(name):
    """Split a vehicle title 'YEAR MAKE MODEL [...] TRIM' into parts.
    Returns (year, make, model, trim)."""
    if not name or name == "N/A":
        return "", "", "", ""
    parts = name.split()
    year = ""
    idx = 0
    if parts and re.fullmatch(r"\d{4}", parts[0]):
        year = parts[0]
        idx = 1
    rest_parts = parts[idx:]
    rest = " ".join(rest_parts)
    make = rest_parts[0] if rest_parts else ""
    after_make = " ".join(rest_parts[1:])

    # Try multi-word models first.
    model = ""
    trim = ""
    for mw in MULTI_WORD_MODELS:
        if after_make.lower().startswith(mw.lower()):
            model = after_make[:len(mw)]
            trim = after_make[len(mw):].strip()
            break
    if not model:
        am_parts = after_make.split()
        if am_parts:
            model = am_parts[0]
            trim = " ".join(am_parts[1:])
    return year, make, model, trim


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------
def load_vin_age_map(csv_path):
    vin_age_map = {}
    if not os.path.exists(csv_path):
        return vin_age_map
    with open(csv_path, mode='r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            vin = row['VIN'].strip()
            try:
                age = int(row['Age'])
                vin_age_map[vin] = age
            except (ValueError, KeyError):
                continue
    return vin_age_map


def scrape_website(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return BeautifulSoup(response.content, 'html.parser')
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None


def _details_map(card):
    """Build a {label: value} dict from the details-item-label / value pairs."""
    out = {}
    for label_div in card.select("div.details-item-label"):
        val_div = label_div.find_next_sibling("div")
        if val_div is None:
            continue
        key = label_div.get_text(strip=True)
        val = val_div.get_text(strip=True)
        if key:
            out[key.lower()] = val
    return out


def extract_vehicle_data(soup, category, vin_age_map=None):
    vehicle_listings = soup.find_all('a', class_='srp-vehicle-box')
    vehicles = []

    for vehicle in vehicle_listings:
        html_str = str(vehicle)
        details = _details_map(vehicle)

        # ----- name -----
        name_tag = vehicle.find(['h1', 'h2', 'h3'])
        name_candidate = name_tag.get_text(strip=True) if name_tag else ""
        if not name_candidate or "available" in name_candidate.lower():
            name = 'N/A'
            for img in vehicle.find_all('img'):
                alt = img.get('alt', '').strip()
                if len(alt) > 5 and alt.lower() not in ['playbutton', 'available', 'new inventory']:
                    name = alt
                    break
        else:
            name = name_candidate

        # ----- vin / stock / specs from details map -----
        vin = details.get("vin") or 'N/A'
        if vin == 'N/A':
            m = re.search(r'[A-HJ-NPR-Z0-9]{17}', html_str)
            if m:
                vin = m.group()

        stock = details.get("stock #") or details.get("stock") or 'N/A'
        model_code = details.get("model code") or 'N/A'
        exterior = details.get("exterior") or 'N/A'
        interior = details.get("interior") or 'N/A'
        drivetrain = details.get("drivetrain") or 'N/A'
        engine = details.get("engine") or 'N/A'
        transmission = details.get("transmission") or 'N/A'
        location = details.get("location") or "Sutherlin Nissan Orlando"

        # ----- mileage -----
        if category == "new":
            mileage = "6 Miles"
        else:
            mileage = details.get("mileage") or 'N/A'

        # ----- carfax -----
        carfax_link = (
            f"https://www.carfax.com/VehicleHistory/p/Report.cfx?partner=TVO_0&vin={vin}&source=BUP"
            if vin != 'N/A' else 'N/A'
        )

        # ----- primary image (gallery _01) -----
        homenet = re.findall(r'https?://content\.homenetiol\.com[^"\s\\]+\.jpg', html_str)
        if homenet:
            image_link = homenet[0]
        else:
            jpg_matches = re.findall(r'https?://[^"\s\\]+\.jpg', html_str)
            image_link = jpg_matches[0] if jpg_matches else (
                vehicle.find('img')['src'] if vehicle.find('img') and vehicle.find('img').has_attr('src') else None
            )

        # ----- prices -----
        market_price = 'N/A'
        sutherlins_price = 'N/A'
        msrp_tag = vehicle.select_one('.vehiclebox-msrp.msrp_value_custom')
        if msrp_tag:
            market_price = msrp_tag.get_text(strip=True)
        yp_divs = vehicle.select('.srp-your-price > div')
        if len(yp_divs) >= 2:
            sutherlins_price = yp_divs[1].get_text(strip=True)

        # ----- vehicle link -----
        href = vehicle.get("href")
        if href and href.startswith("/"):
            href = "https://orlandonissan.com" + href
        vehicle_link = href or 'N/A'

        # ----- split name -----
        year, make, model, trim = split_name(name)

        age = vin_age_map.get(vin, None) if vin_age_map else None

        # ----- marketplace inference -----
        mk_color = _infer_color(exterior if exterior != 'N/A' else None)
        mk_int_color = _infer_color(interior if interior != 'N/A' else None)
        mk_body = _infer_body_type(model, vehicle_link)
        mk_fuel = _infer_fuel_type(engine if engine != 'N/A' else None)
        mk_trans = _infer_transmission(transmission if transmission != 'N/A' else None)

        ymm = " ".join(filter(None, [year, make, model, trim]))
        desc_specs = []
        if transmission and transmission != 'N/A':
            desc_specs.append(transmission)
        if engine and engine != 'N/A':
            desc_specs.append(engine)
        if mileage and mileage != 'N/A':
            miles_num = re.sub(r'[^\d]', '', mileage)
            if miles_num:
                desc_specs.append(f"{miles_num} miles")
        mk_desc = f"{ymm}. " + (", ".join(desc_specs)) + ("." if desc_specs else "")

        vehicle_data = {
            # ----- existing keys (preserved, same order/semantics) -----
            "category": category,
            "name": name,
            "vin": vin,
            "stock": stock,
            "exterior": exterior,
            "interior": interior,
            "engine": engine,
            "transmission": transmission,
            "carfax": carfax_link,
            "image": image_link,
            "price": market_price,
            "sutherlins_price": sutherlins_price,
            "mileage": mileage,
            # ----- new descriptive keys -----
            "year": year,
            "make": make,
            "model": model,
            "trim": trim,
            "drivetrain": drivetrain,
            "model_code": model_code,
            "market_value": market_price,
            "vehicle_link": vehicle_link,
            "location": location,
            # gallery is filled in later by resolve_galleries()
            "images": [],
            # ----- marketplace fields -----
            "marketplace_color": mk_color,
            "marketplace_body_type": mk_body,
            "marketplace_fuel_type": mk_fuel,
            "marketplace_transmission": mk_trans,
            "marketplace_interior_color": mk_int_color,
            "marketplace_condition": "Good",
            "marketplace_vehicle_type": "Car/Truck",
            "marketplace_description": mk_desc,
        }

        if age is not None:
            vehicle_data["age"] = age

        vehicles.append(vehicle_data)

    return vehicles


def scrape_category(category_name, urls, max_pages=30, vin_age_map=None):
    all_vehicles = []
    for base_url in urls:
        for page in range(1, max_pages + 1):
            url = f"{base_url}{page}"
            print(f"🔍 Scraping {category_name} page {page}: {url}")
            soup = scrape_website(url)

            if not soup:
                print(f"❌ Failed to scrape {category_name} page {page}")
                break

            vehicles = extract_vehicle_data(soup, category=category_name, vin_age_map=vin_age_map)

            if not vehicles:
                print(f"⛔ No vehicles found on page {page}. Stopping {category_name} scraping.")
                break

            all_vehicles.extend(vehicles)

            page_filename = f"{category_name}_vehicle_data_page{page}.json"
            page_path = os.path.join(SRC_DIR, page_filename)
            os.makedirs(os.path.dirname(page_path), exist_ok=True)

            with open(page_path, "w") as f:
                json.dump(vehicles, f, indent=2)

            print(f"✅ Exported {len(vehicles)} vehicles to {page_filename}")

    unique_vehicles = {v['vin']: v for v in all_vehicles if v['vin'] != 'N/A'}

    if unique_vehicles:
        combined_filename = f"{category_name}_vehicle_data.json"
        combined_path = os.path.join(SRC_DIR, combined_filename)
        with open(combined_path, "w") as f:
            json.dump(list(unique_vehicles.values()), f, indent=2)
        print(f"📦 Combined {category_name} file written with {len(unique_vehicles)} unique vehicles.")

    return list(unique_vehicles.values())


# ---------------------------------------------------------------------------
# Module-level cleanup of old per-page .json files.
# IMPORTANT: load the photos cache into memory FIRST so the delete loop can
# wipe stale json without losing the gallery cache; we write it back at the end.
# ---------------------------------------------------------------------------
PHOTOS_CACHE = load_photos_cache()
print(f"📷 Loaded photos cache with {len(PHOTOS_CACHE)} VINs.")

json_folder = SRC_DIR
if os.path.exists(json_folder):
    for file in os.listdir(json_folder):
        if file.endswith(".json"):
            os.remove(os.path.join(json_folder, file))
            print(f"🗑️ Deleted old file: {file}")
# (PHOTOS_CACHE is already in memory; it will be re-written at the end.)


if __name__ == "__main__":
    vin_age_map = load_vin_age_map("vin_age_data.csv")

    categories = {
        "used": [
            "https://orlandonissan.com/inventory/used?paymenttype=cash&sorttype=priceltoh&instock=true&intransit=true&inproduction=true&page="
        ],
        "cpo": [
            "https://orlandonissan.com/inventory/cpo?paymenttype=cash&sorttype=priceltoh&instock=true&intransit=true&inproduction=true&page="
        ],
        "new": [
            "https://orlandonissan.com/inventory/new/nissan/armada,frontier,kicks,kicks-play,murano,pathfinder,rogue,sentra,versa,altima?paymenttype=cash&instock=true&intransit=true&inproduction=true&page=",
            "https://orlandonissan.com/inventory/new/nissan/z,titan,leaf?paymenttype=cash&intransit=true&instock=true&inproduction=true&page="
        ]
    }

    all_data = []
    for category, urls in categories.items():
        all_data.extend(scrape_category(category, urls, max_pages=30, vin_age_map=vin_age_map))

    # Attach galleries: reuse cache for known VINs, enumerate only new ones.
    all_data = resolve_galleries(all_data, PHOTOS_CACHE)

    # Persist the (updated) photos cache so future runs skip enumeration.
    save_photos_cache(PHOTOS_CACHE)

    # Empty-guard: never overwrite the master file with [] when 0 scraped.
    master_path = os.path.join(SRC_DIR, "all_vehicle_data.json")
    if all_data:
        os.makedirs(SRC_DIR, exist_ok=True)
        with open(master_path, "w") as f:
            json.dump(all_data, f, indent=2)
        print(f"🎉 All vehicle data combined in all_vehicle_data.json ({len(all_data)} vehicles total).")
    else:
        print("⚠️ 0 vehicles scraped — keeping existing all_vehicle_data.json (empty-guard).")
