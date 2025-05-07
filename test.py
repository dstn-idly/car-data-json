import requests
from bs4 import BeautifulSoup
import json
import os
import re
import csv

def load_vin_age_map(csv_path):
    vin_age_map = {}
    with open(csv_path, mode='r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            vin = row['VIN'].strip()
            try:
                age = int(row['Age'])
                vin_age_map[vin] = age
            except ValueError:
                continue  # Skip rows with non-integer age
    return vin_age_map

def scrape_website(url):
    headers = {
        'User-Agent': 'Mozilla/5.0'
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return BeautifulSoup(response.content, 'html.parser')
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None

def extract_vehicle_data(soup, category, vin_age_map=None):
    vehicle_listings = soup.find_all('a', class_='si-vehicle-box')
    vehicles = []

    for vehicle in vehicle_listings:
        html_str = str(vehicle)

        name_tag = vehicle.find('h2')
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

        vin_tag = vehicle.find('div', id='copy_vin')
        if not vin_tag:
            vin_match = re.search(r'[A-HJ-NPR-Z0-9]{17}', html_str)
            vin = vin_match.group() if vin_match else 'N/A'
        else:
            vin = vin_tag.get_text(strip=True)

        stock_tag = vehicle.find('div', id='copy_stock')
        stock = stock_tag.get_text(strip=True) if stock_tag else 'N/A'

        exterior = 'N/A'
        interior = 'N/A'
        info_container = vehicle.find('div', class_='si-vehicle-info-left')
        if info_container:
            labels = info_container.find_all('div')
            for i in range(len(labels)):
                label = labels[i].get_text(strip=True)
                if "Exterior:" in label and i + 1 < len(labels):
                    exterior = labels[i + 1].get_text(strip=True)
                elif "Interior:" in label and i + 1 < len(labels):
                    interior = labels[i + 1].get_text(strip=True)

        engine = 'N/A'
        engine_tag = vehicle.find('div', string=lambda t: t and 'Engine:' in t)
        if engine_tag:
            engine = engine_tag.get_text(strip=True).replace("Engine:", "").strip()

        transmission_tag = vehicle.find('div', string=lambda text: text and 'Transmission:' in text)
        transmission = transmission_tag.get_text(strip=True).replace('Transmission:', '').strip() if transmission_tag else 'N/A'

        carfax_link = (
            f"https://www.carfax.com/VehicleHistory/p/Report.cfx?partner=TVO_0&vin={vin}&source=BUP"
            if vin != 'N/A' else 'N/A'
        )

        jpg_matches = re.findall(r'https?://[^"\s]+\.jpg', html_str)
        if jpg_matches:
            image_link = jpg_matches[0]
        else:
            img_tag = vehicle.find('img')
            image_link = img_tag['src'] if img_tag and img_tag.has_attr('src') else None

        price_tag = vehicle.select_one(
            '.vehiclebox-msrp, .grey-text.vehiclebox-msrp.msrp_value_custom.msrp-strike-through'
        )
        if price_tag:
            price_text = price_tag.get_text(strip=True)
            if "$" in price_text:
                market_price = price_text
            else:
                price_match = re.search(r'\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?', html_str)
                market_price = price_match.group() if price_match else 'N/A'
        else:
            price_match = re.search(r'\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?', html_str)
            market_price = price_match.group() if price_match else 'N/A'

        mileage = "6 Miles" if category == "new" else (
            vehicle.find('div', class_='mileage').get_text(strip=True).replace("Mileage: ", "")
            if vehicle.find('div', class_='mileage') else 'N/A'
        )

        age = vin_age_map.get(vin, None) if vin_age_map else None

        vehicle_data = {
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
            "mileage": mileage,
        }

        if age is not None:
            vehicle_data["age"] = age

        vehicles.append(vehicle_data)

    return vehicles

def scrape_category(category_name, urls, max_pages=20, vin_age_map=None):
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
            page_path = os.path.join("src", page_filename)
            os.makedirs(os.path.dirname(page_path), exist_ok=True)

            with open(page_path, "w") as f:
                json.dump(vehicles, f, indent=2)

            print(f"✅ Exported {len(vehicles)} vehicles to {page_filename}")

    unique_vehicles = {v['vin']: v for v in all_vehicles if v['vin'] != 'N/A'}

    if unique_vehicles:
        combined_filename = f"{category_name}_vehicle_data.json"
        combined_path = os.path.join("src", combined_filename)
        with open(combined_path, "w") as f:
            json.dump(list(unique_vehicles.values()), f, indent=2)
        print(f"📦 Combined {category_name} file written with {len(unique_vehicles)} unique vehicles.")

    return list(unique_vehicles.values())

# Delete all existing .json files in the src directory
json_folder = "src"
if os.path.exists(json_folder):
    for file in os.listdir(json_folder):
        if file.endswith(".json"):
            file_path = os.path.join(json_folder, file)
            os.remove(file_path)
            print(f"🗑️ Deleted old file: {file}")

if __name__ == "__main__":
    vin_age_map = load_vin_age_map("vin_age_data.csv")  # Your CSV file with VIN + Age

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
        category_vehicles = scrape_category(category, urls, vin_age_map=vin_age_map)
        all_data.extend(category_vehicles)

    master_path = os.path.join("src", "all_vehicle_data.json")
    with open(master_path, "w") as f:
        json.dump(all_data, f, indent=2)
    print(f"🎉 All vehicle data combined in all_vehicle_data.json ({len(all_data)} vehicles total).")
