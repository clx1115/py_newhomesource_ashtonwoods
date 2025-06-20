from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup
import json
import time
import logging
import os
import sys
from datetime import datetime
import re
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import argparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def setup_driver():
    """Set up Chrome driver with appropriate options"""
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    return webdriver.Chrome(options=chrome_options)

def extract_price_range(text):
    """Extract price range from text"""
    if not text:
        return None
    # Remove any non-price characters and split
    prices = re.findall(r'\$[\d,]+K?', text)
    if not prices:
        return None
    return f"From {prices[0]}" if len(prices) == 1 else f"{prices[0]}-{prices[1]}"

def extract_number_range(text):
    """Extract number range from text"""
    if not text:
        return None
    numbers = re.findall(r'\d+(?:,\d+)?', text)
    if not numbers:
        return None
    return f"{numbers[0]} - {numbers[-1]}" if len(numbers) > 1 else numbers[0]

def clean_text(text):
    """Clean and normalize text"""
    if not text:
        return None
    return ' '.join(text.strip().split())

def parse_address(soup):
    """Parse address from the sales office section"""
    try:
        # Find the div containing the sales office information
        content_div = soup.find('div', class_='image-content__main')
        if content_div:
            # Find the sales office text and get the next paragraph
            sales_office = content_div.find('p', string='Sales Office')
            if sales_office and sales_office.find_next_sibling('p'):
                # Get the address text and split by <br> tags
                address_p = sales_office.find_next_sibling('p')
                address_parts = [part.strip() for part in address_p.stripped_strings]
                
                if len(address_parts) >= 2:
                    street = address_parts[0]
                    city_state_zip = address_parts[1].split(',')
                    if len(city_state_zip) >= 2:
                        city = city_state_zip[0].strip()
                        state_zip = city_state_zip[1].strip().split()
                        if len(state_zip) >= 2:
                            state = state_zip[0]
                            zip_code = state_zip[1]
                            full_address = f"{street}, {city}, {state} {zip_code}"
                            return {
                                "full_address": full_address,
                                "city": city,
                                "state": state,
                                "market": "Phoenix"
                            }
    except Exception as e:
        logger.error(f"Error parsing address: {str(e)}")
    
    # Default return if no address found
    return {
        "full_address": "",
        "city": "",
        "state": "",
        "market": "Phoenix"
    }

def parse_community_data(driver, url):
    """Parse community page and extract data"""
    data = {
        "timestamp": datetime.now().isoformat(),
        "url": url,
        "builder": "Ashton Woods",
        "status": None
    }
    
    try:
        # Wait for main content to load
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1"))
        )
        time.sleep(5)  # Allow dynamic content to load
        
        # Get page source and create soup
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Extract community name
        name_elem = soup.find('h1')
        data["name"] = clean_text(name_elem.text) if name_elem else None
        
        # Extract price range
        price_text = soup.find(string=re.compile(r'Plans from \$[\d,]+'))
        if price_text:
            data["price_from"] = extract_price_range(price_text)
        
        # Extract address and location information
        address_info = parse_address(soup)
        data["address"] = address_info["full_address"]
        
        # Create location dict with coordinates and address
        data["location"] = {
            "latitude": 33.3539,  # Coordinates for Laveen, AZ 85339
            "longitude": -112.1597,
            "address": {
                "city": address_info["city"],
                "state": address_info["state"],
                "market": address_info["market"]
            }
        }
        
        # Extract phone
        phone_elem = soup.find(string=re.compile(r'\(\d{3}\) \d{3}-\d{4}'))
        data["phone"] = clean_text(phone_elem) if phone_elem else None
        
        # Extract description
        desc_container = soup.find('div', {'class': 'js-expando is-initialized is-disabled is-expanded'})
        data["description"] = None
        if desc_container and desc_container.find('div', {'class': 'image-content__main-content'}):
            first_p = desc_container.find('div', {'class': 'image-content__main-content'}).find('p')
            if first_p:
                data["description"] = clean_text(first_p.text)
        
        # Extract one image from carousel
        data["images"] = []
        carousel = soup.find('div', class_='image-content__slider-container')
        if carousel:
            # Try to find desktop images from the carousel
            slides = carousel.find_all('div', class_='image-content__slide')
            for slide in slides:
                if slide.get('data-desktop-image'):
                    data["images"].append(slide['data-desktop-image'])
                    break
        
        # Find all floor plans and homesites first
        data["homeplans"] = parse_homeplans(soup)
        data["homesites"] = parse_homesites(soup, driver)
        
        # If no images found in carousel, try to get one from homeplans or homesites
        if not data["images"]:
            # Try homeplans first
            for plan in data["homeplans"]:
                if plan.get("details", {}).get("image_url"):
                    data["images"].append(plan["details"]["image_url"])
                    break
            
            # If still no image, try homesites
            if not data["images"]:
                for site in data["homesites"]:
                    if site.get("image_url"):
                        data["images"].append(site["image_url"])
                        break
        
        # Generate ranges from homesites and homeplans
        def get_range_from_values(values):
            if not values:
                return None
            values = [float(v) for v in values if v is not None]
            if not values:
                return None
            min_val = min(values)
            max_val = max(values)
            return f"{int(min_val)}" if min_val == max_val else f"{int(min_val)} - {int(max_val)}"
        
        # Try to get ranges from homesites first
        sqft_values = [h["sqft"] for h in data["homesites"] if h.get("sqft")]
        bed_values = [h["beds"] for h in data["homesites"] if h.get("beds")]
        bath_values = [float(h["baths"]) for h in data["homesites"] if h.get("baths")]
        
        # If no values in homesites, try homeplans
        if not sqft_values:
            sqft_values = [h["details"]["sqft"] for h in data["homeplans"] if h.get("details", {}).get("sqft")]
        if not bed_values:
            bed_values = [h["details"]["beds"] for h in data["homeplans"] if h.get("details", {}).get("beds")]
        if not bath_values:
            bath_values = [float(h["details"]["baths"]) for h in data["homeplans"] if h.get("details", {}).get("baths")]
        
        # Create details dict
        data["details"] = {
            "price_range": data["price_from"],
            "sqft_range": get_range_from_values(sqft_values),
            "bed_range": get_range_from_values(bed_values),
            "bath_range": get_range_from_values(bath_values),
            "stories_range": "1 - 2",
            "community_count": 1
        }
        
        # Extract amenities
        data["amenities"] = parse_amenities(soup)
        
        # Extract nearby places
        data["nearbyplaces"] = parse_nearby_places(soup)
        
        # Extract collections and schools
        data["collections"] = parse_collections(soup)
        
    except Exception as e:
        logger.error(f"Error parsing community data: {str(e)}")
        
    return data

def parse_homeplans(soup):
    """Parse home plans data"""
    homeplans = []
    
    # First find the home plans panel
    panel = soup.find('li', {'id': 'panel-home-plans'})
    if panel:
        # Then find all series items within the panel
        plan_elements = panel.find_all('div', class_='tabs__series-item tabs__series-item--third js-iframe-url')
        
        for plan in plan_elements:
            try:
                plan_data = {
                    "name": None,
                    "url": None,
                    "details": {
                        "price": None,
                        "beds": None,
                        "baths": None,
                        "sqft": None,
                        "status": "Actively selling",
                        "image_url": None
                    },
                    "includedFeatures": [],
                    "floorplan_images": []
                }
                
                # Extract plan name and URL
                name_elem = plan.find('h4', class_='property-card__title')
                if name_elem and name_elem.find('a'):
                    plan_data["name"] = clean_text(name_elem.find('a').text)
                    plan_data["url"] = "https://www.ashtonwoods.com" + name_elem.find('a')['href']
                
                # Extract image URL
                image_elem = plan.find('a', class_='property-card__image')
                if image_elem:
                    if image_elem.get('data-desktop-image'):
                        plan_data["details"]["image_url"] = image_elem['data-desktop-image']
                    elif image_elem.get('style'):
                        style = image_elem['style']
                        url_match = re.search(r"url\('([^']+)'\)", style)
                        if url_match:
                            plan_data["details"]["image_url"] = url_match.group(1)
                
                # Extract price
                price_elem = plan.find('div', class_='property-card__price')
                if price_elem:
                    price_text = clean_text(price_elem.text)
                    if price_text:
                        # Remove 'From ' prefix if exists
                        price_text = price_text.replace('From ', '')
                        plan_data["details"]["price"] = price_text
                
                # Extract beds/baths/sqft from feature list
                feature_list = plan.find('ul', class_='property-card__feature-list')
                if feature_list:
                    features = feature_list.find_all('li')
                    for feature in features:
                        text = clean_text(feature.get_text())
                        if 'Beds' in text:
                            plan_data["details"]["beds"] = text.split()[0]
                        elif 'Baths' in text:
                            bath_parts = text.split('|')
                            main_baths = bath_parts[0].strip().split()[0]
                            if len(bath_parts) > 1 and 'Half' in bath_parts[1]:
                                plan_data["details"]["baths"] = f"{main_baths}.5"
                            else:
                                plan_data["details"]["baths"] = main_baths
                        elif 'sq. ft.' in text:
                            plan_data["details"]["sqft"] = text.split()[0].replace(',', '')
                
                # Extract included features from highlights section
                highlights = plan.find('div', class_='property-card__content')
                if highlights:
                    feature_items = highlights.find_all('li')
                    for idx, item in enumerate(feature_items):
                        feature_text = clean_text(item.get_text())
                        if feature_text:
                            plan_data["includedFeatures"].append({
                                "section_index": str(idx),
                                "description": feature_text
                            })
                
                # Only add plans that have required data
                if plan_data["name"] and plan_data["url"]:
                    homeplans.append(plan_data)
                
            except Exception as e:
                logger.error(f"Error parsing home plan: {str(e)}")
                continue
    
    return homeplans

def get_coordinates(address):
    """Get latitude and longitude for an address using geocoding"""
    try:
        geolocator = Nominatim(user_agent="ashtonwoods_scraper")
        location = geolocator.geocode(address)
        time.sleep(1)  # Rate limiting
        if location:
            return location.latitude, location.longitude
        return None, None
    except GeocoderTimedOut:
        return None, None
    except Exception as e:
        logger.error(f"Error geocoding address: {str(e)}")
        return None, None

def get_homesite_images(driver, url):
    """Get images from homesite detail page."""
    try:
        driver.get(url)
        # Wait for content to load - try different selectors
        wait = WebDriverWait(driver, 10)
        try:
            wait.until(EC.presence_of_element_located((By.CLASS_NAME, "gallery-modal__item")))
        except:
            try:
                wait.until(EC.presence_of_element_located((By.CLASS_NAME, "image-content__slider-container")))
            except:
                wait.until(EC.presence_of_element_located((By.CLASS_NAME, "col-12")))

        # Get page source after JavaScript renders
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        images = []
        
        # Helper function to validate and clean image URL
        def is_valid_image_url(url):
            if not url:
                return False
            if not isinstance(url, str):
                return False
            if 'bizible.com' in url or 'marvel-b1-cdn' in url:
                return False
            if not ('ashtonwoods.com' in url or 'widen.net' in url):
                return False
            return True

        # Try to get images from gallery modal
        gallery_items = soup.find_all('div', class_='gallery-modal__item')
        for item in gallery_items:
            img_url = item.get('data-desktop-image')
            if is_valid_image_url(img_url) and img_url not in images:
                images.append(img_url)

        # Try to get images from main content area
        if not images:
            for img in soup.find_all(['div', 'img']):
                # Check data-desktop-image attribute
                img_url = img.get('data-desktop-image')
                if is_valid_image_url(img_url) and img_url not in images:
                    images.append(img_url)
                    
                # Check src attribute
                img_url = img.get('src')
                if is_valid_image_url(img_url) and img_url not in images:
                    images.append(img_url)
                    
                # Check style attribute for background-image
                style = img.get('style')
                if style and 'background-image' in style:
                    match = re.search(r'url\(["\']?(.*?)["\']?\)', style)
                    if match:
                        img_url = match.group(1)
                        if is_valid_image_url(img_url) and img_url not in images:
                            images.append(img_url)

        # Limit to 12 images
        return images[:12]
        
    except Exception as e:
        print(f"Error getting images from {url}: {str(e)}")
        return []

def parse_homesites(soup, driver):
    """Parse available homes data"""
    homesites = []
    
    # First find the quick-move-ins panel
    panel = soup.find('li', {'id': 'panel-quick-move-ins'})
    if panel:
        # Find all quick move-in homes within the panel
        home_elements = panel.find_all('div', class_='tabs__series-item tabs__series-item--third js-iframe-url')
        
        for idx, home in enumerate(home_elements, 1):
            try:
                home_data = {
                    "name": None,
                    "plan": None,
                    "id": str(idx),
                    "address": None,
                    "price": None,
                    "beds": None,
                    "baths": None,
                    "sqft": None,
                    "status": "Move-in Ready",
                    "image_url": None,
                    "url": None,
                    "latitude": None,
                    "longitude": None,
                    "overview": None,
                    "images": []
                }
                
                # Extract home name and URL
                name_elem = home.find('h4', class_='property-card__title')
                if name_elem and name_elem.find('a'):
                    # Get original plan name from the title
                    home_data["plan"] = clean_text(name_elem.find('a').text)
                    home_data["url"] = "https://www.ashtonwoods.com" + name_elem.find('a')['href']
                    
                    # Get images from detail page
                    detail_images = get_homesite_images(driver, home_data["url"])
                    if detail_images:
                        home_data["images"] = detail_images
                        home_data["image_url"] = detail_images[0]  # Use first image as main image
                    
                    # Extract address from URL
                    url_parts = home_data["url"].split('/')
                    if len(url_parts) > 0:
                        lot_address = url_parts[-1]
                        # Convert lot-584-5510-w-paseo-way-jade format to actual address
                        address_parts = lot_address.split('-')
                        if len(address_parts) >= 4:
                            # Reconstruct the address
                            street_number = address_parts[2]
                            street_direction = address_parts[3].upper()
                            street_name = ' '.join(part.capitalize() for part in address_parts[4:-1])
                            address = f"{street_number} {street_direction} {street_name}, Laveen, AZ 85339"
                            name_without_zip = f"{street_number} {street_direction} {street_name}, Laveen, AZ"
                            home_data["address"] = address
                            home_data["name"] = name_without_zip
                            
                            # Get coordinates for the address
                            lat, lon = get_coordinates(address)
                            if lat and lon:
                                home_data["latitude"] = lat
                                home_data["longitude"] = lon
                            else:
                                # Fallback to community coordinates if geocoding fails
                                home_data["latitude"] = 33.3539
                                home_data["longitude"] = -112.1597
                
                # Extract price
                price_elem = home.find('div', class_='property-card__price')
                if price_elem:
                    price_text = clean_text(price_elem.text)
                    if price_text:
                        # Remove 'From ' prefix if exists
                        price_text = price_text.replace('From ', '')
                        home_data["price"] = price_text
                
                # Extract beds/baths/sqft from feature list
                feature_list = home.find('ul', class_='property-card__feature-list')
                if feature_list:
                    features = feature_list.find_all('li')
                    for feature in features:
                        text = clean_text(feature.get_text())
                        if 'Beds' in text:
                            home_data["beds"] = text.split()[0]
                        elif 'Baths' in text:
                            bath_parts = text.split('|')
                            main_baths = bath_parts[0].strip().split()[0]
                            if len(bath_parts) > 1 and 'Half' in bath_parts[1]:
                                home_data["baths"] = f"{main_baths}.5"
                            else:
                                home_data["baths"] = main_baths
                        elif 'sq. ft.' in text:
                            home_data["sqft"] = text.split()[0].replace(',', '')
                
                # Extract overview/description
                content = home.find('div', class_='property-card__content')
                if content:
                    overview = content.find('p')
                    if overview:
                        home_data["overview"] = clean_text(overview.get_text())
                
                # Only add homes that have required data
                if home_data["name"] and home_data["url"]:
                    homesites.append(home_data)
                
            except Exception as e:
                logger.error(f"Error parsing home site: {str(e)}")
                continue
    
    return homesites

def parse_amenities(soup):
    """Parse community amenities"""
    amenities = []
    amenity_text = soup.find_all(string=re.compile(r'(RV Garage|Private Bedroom|Covered Entry|Sliding Door)'))
    
    for text in amenity_text:
        try:
            amenity_data = {
                "name": clean_text(text),
                "description": clean_text(text),
                "icon_url": None
            }
            amenities.append(amenity_data)
            
        except Exception as e:
            logger.error(f"Error parsing amenity: {str(e)}")
            continue
    
    return amenities

def parse_nearby_places(soup):
    """Parse nearby places"""
    # This is placeholder data as the website doesn't show nearby places
    return []

def parse_collections(soup):
    """Parse collections and nearby schools"""
    collections = []
    collection_names = ["Estates at Estrella Crossing"]
    
    for name in collection_names:
        try:
            collection_data = {
                "name": name,
                "id": "0",
                "isActive": True,
                "nearbySchools": []
            }
            collections.append(collection_data)
            
        except Exception as e:
            logger.error(f"Error parsing collection: {str(e)}")
            continue
    
    return collections

def process_community_url(driver, url):
    """Process a single community URL"""
    try:
        # Extract community name from URL to use in filename
        community_name = url.split('/')[-2] if url.split('/')[-1].startswith('#') else url.split('/')[-1]
        community_name = community_name.split('?')[0]  # Remove query parameters
        
        # Check if JSON already exists
        output_file = f'data/ashtonwoods/json/ashtonwoods_{community_name}.json'
        if os.path.exists(output_file):
            logger.info(f"Skipping {community_name} - JSON already exists at {output_file}")
            return
            
        logger.info(f"Processing community: {community_name}")
        
        # Get page
        driver.get(url)
        
        # Wait for content to load
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1"))
        )
        time.sleep(5)  # Allow dynamic content to load
        
        # Save HTML content
        os.makedirs('data/ashtonwoods/html', exist_ok=True)
        html_file = f'data/ashtonwoods/html/ashtonwoods_{community_name}.html'
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
        logger.info(f"HTML content has been saved to {html_file}")
        
        # Parse community data
        data = parse_community_data(driver, url)
        
        # Save JSON data
        os.makedirs('data/ashtonwoods/json', exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"JSON data has been saved to {output_file}")
        
    except Exception as e:
        logger.error(f"Error processing {url}: {str(e)}")

def main():
    """Main function to scrape community data"""
    parser = argparse.ArgumentParser(description='Scrape Ashton Woods community data')
    parser.add_argument('--url', help='Single community URL to scrape')
    parser.add_argument('--batch', action='store_true', help='Process all URLs from ashtonwoods_links.json')
    args = parser.parse_args()
    
    try:
        # Setup driver
        driver = setup_driver()
        
        if args.url:
            # Process single URL
            process_community_url(driver, args.url)
            
        elif args.batch:
            # Process multiple URLs from default JSON file
            json_file = 'ashtonwoods_links.json'
            try:
                if not os.path.exists(json_file):
                    logger.error(f"Error: {json_file} not found")
                    return
                    
                with open(json_file, 'r') as f:
                    urls = json.load(f)
                logger.info(f"Found {len(urls)} URLs in {json_file}")
                for url in urls:
                    process_community_url(driver, url)
            except Exception as e:
                logger.error(f"Error reading URLs file: {str(e)}")
                
        else:
            # Default URL if no arguments provided
            default_url = "https://www.ashtonwoods.com/phoenix/estrella-crossing-community?comm=PHO|MCESCR#quick-move-ins"
            process_community_url(driver, default_url)
        
    except Exception as e:
        logger.error(f"Error in main execution: {str(e)}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main() 