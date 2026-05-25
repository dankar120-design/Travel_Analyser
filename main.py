import os
import sys
import json
import datetime
import requests
import time
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Konstanter för Rate Limiting & Säkerhet
RATE_LIMITED = "RATE_LIMITED"
MAX_DAILY_CALLS = 500
api_call_count = 0

# Hämta miljövariabler
TRAVELPAYOUTS_TOKEN = os.getenv("TRAVELPAYOUTS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "dka12/Travel_Analyser")

# Standardkonfiguration för sökning (Skellefteå-fokus)
DESTINATIONS = {
    "LPA": "Gran Canaria",
    "PMI": "Mallorca",
    "AGP": "Malaga",
    "ALC": "Alicante",
    "CHQ": "Chania",
    "LON": "London",
    "FCO": "Rom"
}

# Maxpriser i SEK
THRESHOLD_WEEKEND = 1500  # weekend/storstad
THRESHOLD_LONG = 3500     # längre solresor

def generate_search_dates():
    """
    Genererar sökdatum för de kommande 3 månaderna:
    - Helger (Tors/Fre till Sön/Mån, längd 3-4 dagar)
    - Veckosemestrar (Längd 7-10 dagar)
    """
    today = datetime.date.today()
    dates = []
    
    # Generera helger (fredag till söndag/måndag) de kommande 12 veckorna
    for i in range(1, 13):
        # Hitta nästa fredag
        days_ahead = (4 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_friday = today + datetime.timedelta(days=days_ahead + (i - 1) * 7)
        
        # Helg-kombinationer: Fre-Sön (2 nätter), Fre-Mån (3 nätter), Tor-Sön (3 nätter)
        dates.append({
            "type": "weekend",
            "departure": next_friday.strftime("%Y-%m-%d"),
            "return": (next_friday + datetime.timedelta(days=2)).strftime("%Y-%m-%d") # Sön
        })
        dates.append({
            "type": "weekend",
            "departure": next_friday.strftime("%Y-%m-%d"),
            "return": (next_friday + datetime.timedelta(days=3)).strftime("%Y-%m-%d") # Mån
        })
    
    # Generera veckosemestrar (lördag till nästa söndag, 8 nätter) för närmsta 3 månaderna (4 st spridda)
    for i in range(1, 5):
        days_ahead = (5 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_saturday = today + datetime.timedelta(days=days_ahead + (i * 2 - 1) * 7)
        dates.append({
            "type": "long",
            "departure": next_saturday.strftime("%Y-%m-%d"),
            "return": (next_saturday + datetime.timedelta(days=8)).strftime("%Y-%m-%d")
        })

    return dates

def get_rotating_origin():
    """
    För att inte överskrida gratis rate limits roterar vi avreseort baserat på veckodag:
    - Måndag, Fredag, Söndag: SFT (Skellefteå - prioriterad!)
    - Tisdag: UME (Umeå)
    - Onsdag: LLA (Luleå)
    - Torsdag, Lördag: ARN (Stockholm Arlanda)
    Detta garanterar maximal täckning under en vecka utan API-krasch.
    """
    weekday = datetime.date.today().weekday()
    if weekday in [0, 4, 6]:
        return "SFT"
    elif weekday == 1:
        return "UME"
    elif weekday == 2:
        return "LLA"
    else:
        return "ARN"

def search_flight(token, origin, destination, dep_date, ret_date):
    """Söker efter flygpriser via Travelpayouts Data Access API (cache)."""
    url = "https://api.travelpayouts.com/v2/prices/latest"
    params = {
        "token": token,
        "origin": origin,
        "destination": destination,
        "depart_date": dep_date,
        "return_date": ret_date,
        "currency": "SEK",
        "limit": 5,
        "show_to_affiliates": "true"
    }
    
    global api_call_count
    api_call_count += 1
    if api_call_count > MAX_DAILY_CALLS:
        print(f"Säkerhetsgräns ({MAX_DAILY_CALLS} anrop) uppnådd. Stoppar för att undvika API-blockering.")
        return RATE_LIMITED

    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 429:
            print("Rate limit uppnådd (429).")
            return RATE_LIMITED
        response.raise_for_status()
        data = response.json()
        if data.get("success"):
            return data.get("data", [])
        return []
    except Exception as e:
        print(f"Fel vid flygsökning {origin}->{destination} ({dep_date}): {e}")
        return []

def format_skyscanner_date(date_str):
    """
    Formaterar om YYYY-MM-DD till YYMMDD för Skyscanner-kompatibilitet.
    Använder strikt datetime-parsing för att förhindra felaktig formatering.
    """
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%y%m%d")
    except Exception as e:
        print(f"Fel vid omformatering av datum '{date_str}' för Skyscanner: {e}")
        return "".join(date_str.split("-"))[2:]

def parse_flights(flight_offers, origin, destination, trip_type):
    """Rensar och filtrerar Travelpayouts-datan lokalt i Python."""
    parsed = []
    
    for offer in flight_offers:
        try:
            price = float(offer["value"])
            
            # Prisfiltrering direkt i Python
            threshold = THRESHOLD_WEEKEND if trip_type == "weekend" else THRESHOLD_LONG
            if price > threshold:
                continue
                
            dep_date = offer["depart_date"]
            ret_date = offer["return_date"]
            
            stops = offer.get("number_of_changes", 0)
            gate = offer.get("gate", "Flygbolag")
            
            dep_date_sky = format_skyscanner_date(dep_date)
            ret_date_sky = format_skyscanner_date(ret_date)
            
            parsed.append({
                "source": "travelpayouts",
                "type": "flight",
                "origin": origin,
                "destination": destination,
                "destination_name": DESTINATIONS.get(destination, destination),
                "price": price,
                "departure_date": dep_date,
                "return_date": ret_date,
                "deep_link": f"https://www.skyscanner.se/transport/flights/{origin.lower()}/{destination.lower()}/{dep_date_sky}/{ret_date_sky}?adults=1",
                "flight_data": {
                    "departure_time": "--:--",
                    "arrival_time": "--:--",
                    "return_time": "--:--",
                    "outbound_stops": stops,
                    "inbound_stops": stops,
                    "carrier": gate,
                    "baggage_included": False
                },
                "package_data": None
            })
        except Exception as e:
            print(f"Fel vid parning av specifikt flygerbjudande: {e}")
            continue
            
    return parsed

def scrape_ving_lastminute():
    """
    Hämtar sista-minuten-paketresor direkt från Vings GraphQL-gränssnitt.
    Filtrerar priser upp till 5000 kr för boende (specified/unspecified).
    """
    print("Söker efter Ving sista-minuten-paketresor...")
    url = "https://origo-sc.nltg.com"
    headers = {
        "content-type": "application/json",
        "marketUnit": "vs",
        "x-caller-app": "lastminutesales",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    query_str = """
    {
      lmsTrips(first: 100, departureCode: ["ARN", "SFT", "UME", "LLA"], priceTo: 5000, tripTypes: [SPECIFIED, UNSPECIFIED]) {
        edges {
          node {
            date {
              raw
              short
            }
            duration
            destinationCode
            departureCode
            numFreeSeats
            serialNumber
            departure {
              caId
            }
            offers {
              price
              type
              hotelCode
            }
            hotel {
              content {
                name
                geographical {
                  country {
                    caId
                    name
                  }
                  resort {
                    caId
                    name
                  }
                  area {
                    caId
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    
    payload = {"query": query_str}
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code != 200:
            print(f"Fel vid anrop till Ving API: Status {response.status_code}")
            return []
            
        data = response.json()
        edges = data.get("data", {}).get("lmsTrips", {}).get("edges", [])
        
        parsed_packages = []
        for edge in edges:
            node = edge.get("node", {})
            offers = node.get("offers", [])
            for offer in offers:
                # Vi vill endast ha paketresor (Specified och Unspecified), inte flightOnly
                if offer["type"] not in ["specified", "unspecified"]:
                    continue
                    
                price = float(offer["price"])
                if price > 5000:
                    continue
                    
                departure_code = node.get("departureCode")
                destination_code = node.get("destinationCode")
                departure_date = node.get("date", {}).get("short")
                duration = node.get("duration", 7)
                
                # Räkna ut returdatum
                try:
                    dep_dt = datetime.datetime.strptime(departure_date, "%Y-%m-%d")
                    ret_dt = dep_dt + datetime.timedelta(days=duration)
                    return_date = ret_dt.strftime("%Y-%m-%d")
                except Exception:
                    return_date = departure_date
                    
                hotel = node.get("hotel") or {}
                hotel_name = "Ospecificerat boende"
                country_name = "Okänt land"
                resort_name = "Okänt resmål"
                
                if hotel and hotel.get("content"):
                    content = hotel["content"] or {}
                    hotel_name = content.get("name") or "Specified boende"
                    geo = content.get("geographical") or {}
                    
                    country_obj = geo.get("country") or {}
                    country_name = country_obj.get("name") or "Okänt land"
                    
                    resort_obj = geo.get("resort") or {}
                    resort_name = resort_obj.get("name") or "Okänt resmål"
                
                if offer["type"] == "unspecified":
                    hotel_name = "Ospecificerat boende"
                
                # Bygg en sök-länk för sista-minuten som bypassar sessionskravet och 403
                dep_date_clean = (node.get("date") or {}).get("raw", "").split("T")[0].replace("-", "")
                
                hotel_node = node.get("hotel") or {}
                content_node = hotel_node.get("content") or {}
                geo_node = content_node.get("geographical") or {}
                
                country_obj = geo_node.get("country") or {}
                resort_obj = geo_node.get("resort") or {}
                area_obj = geo_node.get("area") or {}
                
                country_ca_id = country_obj.get("caId", "-1")
                resort_ca_id = resort_obj.get("caId", "-1")
                area_ca_id = area_obj.get("caId", "-1")
                
                departure_obj = node.get("departure") or {}
                dep_id = departure_obj.get("caId", "-1")
                
                query_res_id = resort_ca_id if offer["type"] == "specified" else "-1"
                
                # Bygg en robust sista-minuten sök-länk som inte ger 403
                serial_no = node.get('serialNumber') or '-1'
                deep_link = f"https://www.ving.se/sista-minuten?SelectedDepCd={departure_code}&SelectedDestCd={destination_code}&QueryDepDate={dep_date_clean}&QueryDur={duration}&QueryRoomAges=42&QueryUnits=1&SelectedHotCd={offer['hotelCode']}&QueryResID={query_res_id}&QueryCtryID={country_ca_id}&QueryAreaID={area_ca_id}&QueryDepID={dep_id}&SelectedSerNo={serial_no}"
                
                parsed_packages.append({
                    "source": "ving",
                    "type": "package",
                    "origin": departure_code,
                    "destination": destination_code,
                    "destination_name": f"{resort_name} ({country_name})",
                    "price": price,
                    "departure_date": departure_date,
                    "return_date": return_date,
                    "deep_link": deep_link,
                    "flight_data": None,
                    "package_data": {
                        "hotel_name": hotel_name,
                        "stars": None,
                        "nights": duration,
                        "operator": "Ving"
                    }
                })
        
        print(f"Hittade {len(parsed_packages)} st Ving-paket under tröskelvärdet!")
        return parsed_packages
        
    except Exception as e:
        print(f"Fel vid hämtning av Ving sista minuten: {e}")
        return []

def scrape_tui_lastminute():
    """
    Hämtar sista-minuten-paketresor direkt från TUI:s CloudFront deals API.
    Använder curl_cffi med impersonate="chrome" för att bypassa Akamai WAF.
    Felhanterad för att förhindra krascher på Linux.
    """
    print("Söker efter TUI sista-minuten-paketresor...")
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        print("Varning: curl_cffi kunde inte importeras. TUI-skrapning inaktiverad.")
        return []
        
    url = "https://dy5sej8tzf1b8.cloudfront.net/ml/deals/deals/"
    params = {
        "market": "se",
        "locale": "sv-SE",
        "offset": 1,
        "numberOfResults": 100,
        "dealsId": "sista-minuten-nu",
        "sortBy": "price"
    }
    
    try:
        response = curl_requests.get(url, params=params, impersonate="chrome", timeout=20)
        if response.status_code != 200:
            print(f"Fel vid anrop till TUI API: Status {response.status_code}")
            return []
            
        data = response.json()
        offers = data.get("offers", [])
        
        parsed_packages = []
        allowed_origins = ["ARN", "SFT", "UME", "LLA"]
        
        for offer in offers:
            origin = offer.get("departureAirport", {}).get("code")
            if origin not in allowed_origins:
                continue
                
            price = float(offer.get("pricePerPerson", {}).get("amount", 0))
            if price > 5000:
                continue
                
            destination_code = offer.get("hotel", {}).get("geo", {}).get("destinationCode")
            resort_name = offer.get("hotel", {}).get("geo", {}).get("resort", "Okänt resmål")
            country_name = offer.get("hotel", {}).get("geo", {}).get("country", "Okänt land")
            
            departure_date = offer.get("departureDate", offer.get("arrivalDate"))
            duration = int(offer.get("duration", 7))
            
            try:
                dep_dt = datetime.datetime.strptime(departure_date, "%Y-%m-%d")
                ret_dt = dep_dt + datetime.timedelta(days=duration)
                return_date = ret_dt.strftime("%Y-%m-%d")
            except Exception:
                return_date = departure_date
                
            hotel_name = offer.get("hotel", {}).get("name", "Ospecificerat boende")
            stars = offer.get("hotel", {}).get("tuiRating")
            
            book_link = offer.get("bookLink", "")
            deep_link = f"https://www.tui.se{book_link}" if book_link else "https://www.tui.se"
            
            parsed_packages.append({
                "source": "tui",
                "type": "package",
                "origin": origin,
                "destination": destination_code,
                "destination_name": f"{resort_name} ({country_name})",
                "price": price,
                "departure_date": departure_date,
                "return_date": return_date,
                "deep_link": deep_link,
                "flight_data": None,
                "package_data": {
                    "hotel_name": hotel_name,
                    "stars": stars,
                    "nights": duration,
                    "operator": "TUI"
                }
            })
            
        print(f"Hittade {len(parsed_packages)} st TUI-paket under tröskelvärdet!")
        return parsed_packages
    except Exception as e:
        print(f"Fel vid hämtning av TUI sista minuten: {e}")
        return []


def update_state(new_flights, new_packages=None):
    """Sparar och ackumulerar historisk flyg- och paketdata i data/price_history.json (max 30 dagar) med strikt deduplicering."""
    state_file = "data/price_history.json"
    
    if new_packages is None:
        new_packages = []
        
    # Kombinera all ny skörd
    all_new_items = new_flights + new_packages
    
    # Skapa mappen om den inte finns
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {"history": []}
    else:
        state = {"history": []}
        
    # Kontrollera och utför migration av gamla platta flygposter i hela historiken
    for run in state.get("history", []):
        migrated_items = []
        for item in run.get("flights", []):
            # Om posten är det gamla platta formatet, migrera den till nya polymorfiska formatet
            if "flight_data" not in item and "package_data" not in item:
                # Det gamla platta formatet
                item = {
                    "source": "travelpayouts",
                    "type": "flight",
                    "origin": item.get("origin"),
                    "destination": item.get("destination"),
                    "destination_name": item.get("destination_name"),
                    "price": item.get("price"),
                    "departure_date": item.get("departure_date"),
                    "return_date": item.get("return_date"),
                    "deep_link": item.get("deep_link"),
                    "flight_data": {
                        "departure_time": item.get("departure_time", "--:--"),
                        "arrival_time": item.get("arrival_time", "--:--"),
                        "return_time": item.get("return_time", "--:--"),
                        "outbound_stops": item.get("outbound_stops", 0),
                        "inbound_stops": item.get("inbound_stops", 0),
                        "carrier": item.get("carrier", "Flygbolag"),
                        "baggage_included": item.get("baggage_included", False)
                    },
                    "package_data": None
                }
            
            # Städa Skyscanner-länkar i befintliga/migrerade flighter om de har YYYY-MM-DD-format
            if item.get("type") == "flight" and "deep_link" in item:
                link = item["deep_link"]
                dates = re.findall(r"\d{4}-\d{2}-\d{2}", link)
                if dates:
                    for d in dates:
                        try:
                            yyyymmdd = datetime.datetime.strptime(d, "%Y-%m-%d").strftime("%y%m%d")
                            link = link.replace(d, yyyymmdd)
                        except Exception:
                            pass
                    item["deep_link"] = link

            # Städa Ving-länkar i befintliga paketresor om de pekar på den gamla 403-upsell routen
            if item.get("type") == "package" and "deep_link" in item and "resor/bokningssteg/upsell" in item["deep_link"]:
                item["deep_link"] = item["deep_link"].replace("resor/bokningssteg/upsell", "sista-minuten").replace("QueryRoomAges=42,42", "QueryRoomAges=42")

            migrated_items.append(item)
        run["flights"] = migrated_items

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    # Förbered dagens körning och gör en strikt deduplicering av dagens skörd
    if all_new_items:
        today_run = None
        for run in state["history"]:
            if run.get("date") == today_str:
                today_run = run
                break
                
        if today_run is not None:
            combined_items = today_run.get("flights", []) + all_new_items
        else:
            combined_items = all_new_items

        unique_items = []
        seen_keys = set()
        
        for item in combined_items:
            if item["type"] == "flight":
                key = (
                    item["origin"], 
                    item["destination"], 
                    item["departure_date"], 
                    item["return_date"], 
                    item["price"]
                )
            else: # package
                key = (
                    item["origin"], 
                    item["destination"], 
                    item["departure_date"], 
                    item["package_data"]["nights"], 
                    item["package_data"]["hotel_name"], 
                    item["package_data"]["operator"],
                    item["price"]
                )
                
            if key not in seen_keys:
                seen_keys.add(key)
                unique_items.append(item)
                
        if today_run is not None:
            today_run["flights"] = unique_items
        else:
            # Lägg till dagens skörd med tidsstämpel
            state["history"].append({
                "date": today_str,
                "flights": unique_items
            })
        
    # Behåll endast de senaste 30 dagarnas körningar för att förhindra gigantiska filer
    thirty_days_ago = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    state["history"] = [run for run in state["history"] if run["date"] >= thirty_days_ago]
    
    # Extra deduplicering över hela historiken (för att städa upp eventuella gamla duplikatposter per dag)
    for run in state["history"]:
        run_seen = set()
        run_unique = []
        for item in run.get("flights", []):
            if item["type"] == "flight":
                key = (
                    item["origin"], 
                    item["destination"], 
                    item["departure_date"], 
                    item["return_date"], 
                    item["price"]
                )
            else:
                key = (
                    item["origin"], 
                    item["destination"], 
                    item["departure_date"], 
                    item["package_data"]["nights"], 
                    item["package_data"]["hotel_name"], 
                    item["package_data"]["operator"],
                    item["price"]
                )
            if key not in run_seen:
                run_seen.add(key)
                run_unique.append(item)
        run["flights"] = run_unique
        
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Fel vid sparning av state-fil: {e}")
        
    return state

def generate_html_dashboard(state):
    """Genererar en premium och visuellt slående dashboard (index.html)."""
    # Samla alla unika flyg och paket från de senaste dagarna till dashboarden
    all_items = {}
    
    # Sortera historiken så att vi får de senaste priserna först
    for run in sorted(state["history"], key=lambda x: x["date"]):
        for item in run["flights"]:
            if item["type"] == "flight":
                key = f"flight-{item['origin']}-{item['destination']}-{item['departure_date']}-{item['return_date']}"
            else:
                key = f"package-{item['origin']}-{item['destination']}-{item['departure_date']}-{item['package_data']['hotel_name']}"
            all_items[key] = item # Skriver över med senaste priset och info
            
    items_list = list(all_items.values())
    
    # HTML-mall med modern glassmorphism styling och polymorfiska kort
    html_content = f"""<!DOCTYPE html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reseanalysatorn ✈️ - Skellefteå-fynd</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0b0f19;
            --card-bg: rgba(255, 255, 255, 0.04);
            --card-border: rgba(255, 255, 255, 0.08);
            --primary: #4f46e5;
            --primary-glow: rgba(79, 70, 229, 0.4);
            --accent: #06b6d4;
            --text-color: #f3f4f6;
            --text-muted: #9ca3af;
            --success: #10b981;
            --warning: #f59e0b;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: 'Outfit', sans-serif;
            padding: 2rem 1rem;
            min-height: 100vh;
            background-image: 
                radial-gradient(at 0% 0%, rgba(79, 70, 229, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(6, 118, 212, 0.15) 0px, transparent 50%);
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 2rem;
            border-bottom: 1px solid var(--card-border);
            margin-bottom: 2.5rem;
        }}

        .brand h1 {{
            font-size: 2.5rem;
            font-weight: 800;
            background: linear-gradient(to right, #a5b4fc, #818cf8, #06b6d4);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}

        .brand p {{
            color: var(--text-muted);
            margin-top: 0.25rem;
        }}

        .timestamp {{
            font-size: 0.9rem;
            color: var(--text-muted);
            background: var(--card-bg);
            padding: 0.5rem 1rem;
            border-radius: 20px;
            border: 1px solid var(--card-border);
        }}

        /* Filter bar */
        .filter-section {{
            margin-bottom: 2rem;
        }}

        .filter-title {{
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 0.75rem;
            color: var(--text-muted);
        }}

        .filter-bar {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
        }}

        .filter-btn {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            color: var(--text-color);
            padding: 0.6rem 1.2rem;
            border-radius: 25px;
            cursor: pointer;
            font-family: inherit;
            font-size: 0.95rem;
            transition: all 0.25s ease;
        }}

        .filter-btn:hover {{
            background: rgba(255, 255, 255, 0.08);
            border-color: var(--accent);
        }}

        .filter-btn.active {{
            background: var(--primary);
            border-color: var(--primary);
            box-shadow: 0 0 15px var(--primary-glow);
        }}

        /* Flight Grid */
        .flight-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 1.5rem;
        }}

        /* Flight Card */
        .flight-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            backdrop-filter: blur(12px);
            position: relative;
            overflow: hidden;
        }}

        .flight-card:hover {{
            transform: translateY(-5px);
            border-color: var(--accent);
            box-shadow: 0 10px 25px rgba(6, 182, 212, 0.1);
        }}

        .card-badge {{
            position: absolute;
            top: 1rem;
            right: 1rem;
            background: rgba(16, 185, 129, 0.2);
            color: var(--success);
            border: 1px solid var(--success);
            padding: 0.25rem 0.75rem;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
        }}

        .route-info {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            padding-right: 4rem; /* Undvik krock med badge */
        }}

        .route-code {{
            font-size: 1.6rem;
            font-weight: 800;
            letter-spacing: 1px;
        }}

        .route-arrow {{
            color: var(--accent);
            font-size: 1.4rem;
        }}

        .destination-name {{
            font-size: 0.9rem;
            color: var(--text-muted);
            margin-top: 0.25rem;
        }}

        .price-section {{
            margin-bottom: 1.5rem;
            display: flex;
            align-items: baseline;
            gap: 0.5rem;
        }}

        .price-val {{
            font-size: 2.2rem;
            font-weight: 800;
            color: var(--text-color);
        }}

        .price-currency {{
            font-size: 1rem;
            color: var(--text-muted);
            font-weight: 600;
        }}

        .flight-details {{
            border-top: 1px solid var(--card-border);
            padding-top: 1rem;
            margin-bottom: 1.5rem;
            font-size: 0.9rem;
        }}

        .detail-row {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 0.5rem;
        }}

        .detail-label {{
            color: var(--text-muted);
        }}

        .detail-val {{
            font-weight: 600;
        }}

        .baggage-badge {{
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
            font-size: 0.8rem;
            padding: 0.2rem 0.5rem;
            border-radius: 6px;
            margin-top: 0.5rem;
        }}

        .baggage-badge.no {{
            background: rgba(245, 158, 11, 0.15);
            color: var(--warning);
            border: 1px solid var(--warning);
        }}

        .baggage-badge.yes {{
            background: rgba(16, 185, 129, 0.15);
            color: var(--success);
            border: 1px solid var(--success);
        }}

        .book-btn {{
            display: block;
            width: 100%;
            background: linear-gradient(135deg, var(--primary), #3b82f6);
            color: #fff;
            text-align: center;
            padding: 0.8rem;
            border-radius: 10px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.25s ease;
            box-shadow: 0 4px 12px var(--primary-glow);
        }}

        .book-btn:hover {{
            background: linear-gradient(135deg, #5a52ff, #4f87ff);
            box-shadow: 0 6px 18px var(--primary-glow);
        }}

        .empty-state {{
            grid-column: 1 / -1;
            text-align: center;
            padding: 4rem 2rem;
            background: var(--card-bg);
            border: 1px dashed var(--card-border);
            border-radius: 16px;
            color: var(--text-muted);
        }}

        /* System Status Banner */
        .system-status-banner {{
            background: rgba(245, 158, 11, 0.06);
            border: 1px solid rgba(245, 158, 11, 0.15);
            border-radius: 14px;
            padding: 1rem 1.25rem;
            margin-bottom: 2.5rem;
            display: flex;
            align-items: center;
            gap: 1rem;
            backdrop-filter: blur(8px);
        }}

        .status-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background-color: var(--warning);
            box-shadow: 0 0 10px rgba(245, 158, 11, 0.6);
            flex-shrink: 0;
            animation: pulse-warn 2s infinite;
        }}

        .status-text {{
            font-size: 0.9rem;
            color: var(--text-color);
            line-height: 1.5;
        }}

        .status-text strong {{
            color: var(--warning);
            font-weight: 600;
        }}

        @keyframes pulse-warn {{
            0% {{
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.5);
            }}
            70% {{
                transform: scale(1);
                box-shadow: 0 0 0 8px rgba(245, 158, 11, 0);
            }}
            100% {{
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(245, 158, 11, 0);
            }}
        }}

        @keyframes pulse-success {{
            0% {{
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.5);
            }}
            70% {{
                transform: scale(1);
                box-shadow: 0 0 0 8px rgba(16, 185, 129, 0);
            }}
            100% {{
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(16, 185, 129, 0);
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <h1>Reseanalysatorn ✈️</h1>
                <p>Daglig prissökningsbevakning optimerad för Skellefteå-bor</p>
            </div>
            <div class="timestamp">
                Uppdaterad: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
            </div>
        </header>

        <!-- System Status Banner -->
        <div class="system-status-banner" style="background: rgba(16, 185, 129, 0.06); border-color: rgba(16, 185, 129, 0.15);">
            <span class="status-dot" style="background-color: var(--success); box-shadow: 0 0 10px rgba(16, 185, 129, 0.6); animation: pulse-success 2s infinite;"></span>
            <div class="status-text">
                <strong>System Status:</strong> Aktiv & Framtidssäkrad (Travelpayouts API & Ving & TUI Sista-Minuten). Skanning rullar dagligen med full täckning och livslängd!
            </div>
        </div>

        <section class="filter-section">
            <div class="filter-title">Filtrera på avreseort:</div>
            <div class="filter-bar">
                <button class="filter-btn active" onclick="filterOrigin('ALL')">Alla avreseorter</button>
                <button class="filter-btn" onclick="filterOrigin('SFT')">Skellefteå (SFT)</button>
                <button class="filter-btn" onclick="filterOrigin('UME')">Umeå (UME)</button>
                <button class="filter-btn" onclick="filterOrigin('LLA')">Luleå (LLA)</button>
                <button class="filter-btn" onclick="filterOrigin('ARN')">Arlanda (ARN)</button>
            </div>
        </section>

        <main class="flight-grid" id="flightGrid">
            <!-- Flygkort injectas via JavaScript -->
        </main>
    </div>

    <script>
        const flights = {json.dumps(items_list, ensure_ascii=False)};
        let activeOrigin = 'ALL';

        function renderFlights() {{
            const grid = document.getElementById('flightGrid');
            grid.innerHTML = '';
            
            const filtered = flights.filter(f => activeOrigin === 'ALL' || f.origin === activeOrigin);
            
            if (filtered.length === 0) {{
                grid.innerHTML = `
                    <div class="empty-state">
                        <h2>Inga resor hittades under gränsvärdena för detta filter just nu.</h2>
                        <p>Bevakningen fortsätter dagligen för att hitta nya prissänkningar!</p>
                    </div>
                `;
                return;
            }}

            // Sortera efter pris (billigast först)
            filtered.sort((a, b) => a.price - b.price);

            filtered.forEach(f => {{
                const card = document.createElement('div');
                card.className = 'flight-card';
                
                const isSFT = f.origin === 'SFT' ? '⚡ Direkt/Snabbast' : 'Bil/Transfer';
                
                if (f.type === 'flight') {{
                    const bagText = f.flight_data.baggage_included ? '🎒 Incheckat bagage ingår' : '⚠️ Endast handbagage';
                    const bagClass = f.flight_data.baggage_included ? 'yes' : 'no';
                    
                    card.innerHTML = `
                        <span class="card-badge">${{isSFT}}</span>
                        <div>
                            <div class="route-info">
                                <div>
                                    <div class="route-code">${{f.origin}}</div>
                                    <div style="font-size: 0.8rem; color: var(--text-muted);">Utresa</div>
                                </div>
                                <div class="route-arrow">➔</div>
                                <div>
                                    <div class="route-code">${{f.destination}}</div>
                                    <div class="destination-name">${{f.destination_name}}</div>
                                </div>
                            </div>

                            <div class="price-section">
                                <span class="price-val">${{Math.round(f.price).toLocaleString('sv-SE')}}</span>
                                <span class="price-currency">SEK</span>
                            </div>

                            <div class="flight-details">
                                <div class="detail-row">
                                    <span class="detail-label">Utresa:</span>
                                    <span class="detail-val">${{f.departure_date}} (${{f.flight_data.departure_time}})</span>
                                </div>
                                <div class="detail-row">
                                    <span class="detail-label">Hemresa:</span>
                                    <span class="detail-val">${{f.return_date}} (${{f.flight_data.return_time}})</span>
                                </div>
                                <div class="detail-row">
                                    <span class="detail-label">Byten (Ut/Hem):</span>
                                    <span class="detail-val">${{f.flight_data.outbound_stops}} / ${{f.flight_data.inbound_stops}}</span>
                                </div>
                                <div class="detail-row">
                                    <span class="detail-label">Aktör:</span>
                                    <span class="detail-val">${{f.flight_data.carrier}}</span>
                                </div>
                                <span class="baggage-badge ${{bagClass}}">${{bagText}}</span>
                            </div>
                        </div>
                        <a href="${{f.deep_link}}" target="_blank" rel="noopener noreferrer" class="book-btn">Sök på Skyscanner</a>
                    `;
                }} else if (f.type === 'package') {{
                    const isTUI = f.package_data.operator === 'TUI';
                    const btnText = isTUI ? 'Boka hos TUI' : 'Boka hos Ving';
                    const btnStyle = isTUI 
                        ? 'background: linear-gradient(135deg, #09a0e0, #0284c7); box-shadow: 0 4px 12px rgba(9, 160, 224, 0.4);' 
                        : 'background: linear-gradient(135deg, var(--accent), #0891b2); box-shadow: 0 4px 12px rgba(6, 182, 212, 0.4);';
                        
                    card.innerHTML = `
                        <span class="card-badge" style="background: rgba(6, 182, 212, 0.2); color: var(--accent); border-color: var(--accent);">🌴 Paketresa (Flyg+Hotell)</span>
                        <div>
                            <div class="route-info">
                                <div>
                                    <div class="route-code">${{f.origin}}</div>
                                    <div style="font-size: 0.8rem; color: var(--text-muted);">Utresa</div>
                                </div>
                                <div class="route-arrow">➔</div>
                                <div>
                                    <div class="route-code">${{f.destination}}</div>
                                    <div class="destination-name">${{f.destination_name}}</div>
                                </div>
                            </div>

                            <div class="price-section">
                                <span class="price-val">${{Math.round(f.price).toLocaleString('sv-SE')}}</span>
                                <span class="price-currency">SEK</span>
                            </div>

                            <div class="flight-details">
                                <div class="detail-row">
                                    <span class="detail-label">Boende:</span>
                                    <span class="detail-val" style="color: #fff; font-weight: 600;">${{f.package_data.hotel_name}}</span>
                                </div>
                                <div class="detail-row">
                                    <span class="detail-label">Reslängd:</span>
                                    <span class="detail-val">${{f.package_data.nights}} dagar</span>
                                </div>
                                <div class="detail-row">
                                    <span class="detail-label">Utresa:</span>
                                    <span class="detail-val">${{f.departure_date}}</span>
                                </div>
                                <div class="detail-row">
                                    <span class="detail-label">Hemresa:</span>
                                    <span class="detail-val">${{f.return_date}}</span>
                                </div>
                                <div class="detail-row">
                                    <span class="detail-label">Arrangör:</span>
                                    <span class="detail-val" style="color: var(--warning); font-weight: 600;">${{f.package_data.operator}}</span>
                                </div>
                            </div>
                        </div>
                        <a href="${{f.deep_link}}" target="_blank" rel="noopener noreferrer" class="book-btn" style="${{btnStyle}}">${{btnText}}</a>
                    `;
                }}
                
                grid.appendChild(card);
            }});
        }}

        function filterOrigin(origin) {{
            activeOrigin = origin;
            
            // Uppdatera knappar
            const buttons = document.querySelectorAll('.filter-btn');
            buttons.forEach(btn => {{
                if (btn.textContent.includes(origin) || (origin === 'ALL' && btn.textContent.includes('Alla'))) {{
                    btn.classList.add('active');
                }} else {{
                    btn.classList.remove('active');
                }}
            }});
            
            renderFlights();
        }}

        // Initial rendering
        renderFlights();
    </script>
</body>
</html>
"""
    
    try:
        os.makedirs("dist", exist_ok=True)
        with open("dist/index.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        print("HTML-dashboard genererad (dist/index.html).")
    except Exception as e:
        print(f"Fel vid generering av HTML-dashboard: {e}")

def run_groq_analysis(flights):
    """Skickar den optimerade rese- och paketdatan till Groq för anomalidetektering och kort bulletin."""
    if not GROQ_API_KEY:
        print("GROQ_API_KEY saknas. Skippar AI-analys.")
        return "AI-analys är inaktiv (saknar API-nyckel)."
        
    if not flights:
        return "Inga nya billiga resor under tröskelvärdena upptäcktes under dagens skanning."

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Förbered kompakt JSON-data för Groq (polymorfiskt säker)
    compact_data = []
    for f in flights:
        if f['type'] == 'flight':
            compact_data.append({
                "Typ": "Flyg",
                "Rutt": f"{f['origin']}->{f['destination']} ({f['destination_name']})",
                "Datum": f"{f['departure_date']} till {f['return_date']}",
                "Pris": f"{f['price']} SEK",
                "Byten": f"{f['flight_data']['outbound_stops']}/{f['flight_data']['inbound_stops']}",
                "Baggage": "Ingår" if f['flight_data']['baggage_included'] else "Endast Handbagage"
            })
        elif f['type'] == 'package':
            compact_data.append({
                "Typ": f"Paketresa ({f['package_data']['operator']})",
                "Rutt": f"{f['origin']}->{f['destination']} ({f['destination_name']})",
                "Datum": f"{f['departure_date']} till {f['return_date']} ({f['package_data']['nights']} dagar)",
                "Pris": f"{f['price']} SEK",
                "Boende": f['package_data']['hotel_name']
            })
        
    system_prompt = (
        "Du är en personlig reseexpert för en användare bosatt i Skellefteå (SFT). "
        "Din uppgift är att skriva en extremt kortfattad, slagkraftig och lockande morgonsammanfattning på svenska "
        "av dygnets absolut bästa fynd (både reguljärflyg och paketresor från Ving och TUI). "
        "Regler:\n"
        "1. Analysera datan och lyft fram de 2-3 absolut bästa priserna, särskilt om det finns billiga sista-minuten paketresor.\n"
        "2. Skellefteå-fokus: Om det finns ett bra fynd direkt från SFT ska det hyllas. Om det däremot finns ett fynd från Umeå (UME), Luleå (LLA) eller Arlanda (ARN) som gör att det är värt transfern, förklara det kort.\n"
        "3. Håll språket personligt, inspirerande men mycket kortfattat (max 140 ord!). Använd korta meningar och punktlistor.\n"
        "4. Formatera svaret i ren och enkel text så att den lätt kan konverteras till Telegram-Markdown. Undvik komplex HTML."
    )
    
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Här är dagens hittade resor under tröskelvärdena: {json.dumps(compact_data, ensure_ascii=False)}"}
        ],
        "temperature": 0.3
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=20)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Fel vid anrop till Groq API: {e}")
        return "Ett oväntat fel uppstod under AI-analysen."

def escape_markdown_v2(text):
    """Escapar specialtecken för strikt Telegram MarkdownV2-stabilitet."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join('\\' + char if char in escape_chars else char for char in text)

def send_telegram_message(bulletin):
    """Skickar morgonsammanfattningen till Telegram med MarkdownV2."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram-miljövariabler saknas. Skippar notis.")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Skapa en vacker rubrik och lägg till dashboard-länk
    username = GITHUB_REPOSITORY.split("/")[0] if "/" in GITHUB_REPOSITORY else "dka12"
    repo_name = GITHUB_REPOSITORY.split("/")[1] if "/" in GITHUB_REPOSITORY else "Travel_Analyser"
    dashboard_url = f"https://{username}.github.io/{repo_name}/"
    
    # Segmenterad escape för säker Telegram MarkdownV2
    header = escape_markdown_v2("✈️ Dagens Reserapport ✈️")
    escaped_bulletin = escape_markdown_v2(bulletin)
    link_text = escape_markdown_v2("Se hela din dashboard här")
    footer = escape_markdown_v2("📊")
    
    stable_message = (
        f"*{header}*\n\n"
        f"{escaped_bulletin}\n\n"
        f"{footer} [{link_text}]({dashboard_url})" # dashboard_url OESCAPAD inuti ()
    )
    
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": stable_message,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": False
    }
    
    try:
        response = requests.post(url, json=data, timeout=15)
        response.raise_for_status()
        print("Telegram-meddelande skickat framgångsrikt.")
    except Exception as e:
        print(f"Fel vid sändning till Telegram: {e}")
        # Fallback-anrop utan Markdown om det kraschar pga parsningsfel
        try:
            fallback_data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": f"✈️ Dagens Reserapport ✈️\n\n{bulletin}\n\nSe din dashboard här: {dashboard_url}"
            }
            requests.post(url, json=fallback_data, timeout=15)
            print("Telegram fallback skickat (utan MarkdownV2).")
        except Exception as e2:
            print(f"Helt misslyckad Telegram-sändning: {e2}")

def main():
    print("=== Startar Reseanalysatorn (Travelpayouts API + Ving & TUI Scrapers) ===")
    
    if not TRAVELPAYOUTS_TOKEN:
        print("Fel: TRAVELPAYOUTS_TOKEN måste vara satt.")
        sys.exit(1)
        
    token = TRAVELPAYOUTS_TOKEN
        
    # 1. Hämta Ving sista-minuten-paketresor (Skrapas alltid eftersom det är gratis GraphQL utan API limits)
    ving_packages = scrape_ving_lastminute()
    tui_packages = scrape_tui_lastminute()
    
    # 2. Generera sökdatum och avreseort för flygsökningar
    search_dates = generate_search_dates()
    
    # Kolla om vi kör lokalt med djupanalys
    is_deep = "--deep" in sys.argv
    is_all_origins = "--all-origins" in sys.argv
    
    if is_deep or is_all_origins:
        origins = ["SFT", "UME", "LLA", "ARN"]
        print(f"Kör fullständig flygskanning för ALLA avreseorter: {origins}")
    else:
        # Normal rullande skanning för att spara API-gränser
        rotating_origin = get_rotating_origin()
        origins = [rotating_origin]
        print(f"Normal schemalagd flygkörning. Aktiv avreseort idag: {rotating_origin}")
        
    all_found_flights = []
    
    # 3. Sök igenom flyg (Matris loop)
    for origin in origins:
        for dest in DESTINATIONS.keys():
            # Begränsa antalet datum i Sandbox för att inte få 429
            # Vi söker de 6 närmsta helgerna för normal skanning, och alla om det är --deep
            dates_to_search = search_dates if (is_deep or len(origins) == 1) else search_dates[:6]
            
            print(f"Söker flyg: {origin} -> {dest} för {len(dates_to_search)} olika datumfönster...")
            circuit_breaker_active = False
            for date_window in dates_to_search:
                if circuit_breaker_active:
                    break

                offers = search_flight(
                    token, 
                    origin, 
                    dest, 
                    date_window["departure"], 
                    date_window["return"]
                )
                
                if offers == RATE_LIMITED:
                    print("Circuit breaker flyg aktiverad. Pausar 60 sekunder...")
                    time.sleep(60)
                    # Försök en gång till
                    offers = search_flight(
                        token, origin, dest, date_window["departure"], date_window["return"]
                    )
                    if offers == RATE_LIMITED:
                        print("Fortfarande rate-limitad! Bryter API-skanningen.")
                        circuit_breaker_active = True
                        break
                
                if offers and offers != RATE_LIMITED:
                    parsed = parse_flights(offers, origin, dest, date_window["type"])
                    if parsed:
                        all_found_flights.extend(parsed)
                        print(f"  Hittade {len(parsed)} st flyg under tröskelvärdena!")
                
                # Liten paus för att undvika rate limits
                time.sleep(0.5)
            
            if circuit_breaker_active:
                break
        
        if circuit_breaker_active:
            break

    print(f"Flygskanning klar! Hittade totalt {len(all_found_flights)} st intressanta flyg.")
    
    # 4. Spara till state och generera dashboard (både flyg och paket)
    state = update_state(all_found_flights, ving_packages + tui_packages)
    generate_html_dashboard(state)
    
    # Kombinera dagens skörd för Groq AI-Analys
    dagens_skord = all_found_flights + ving_packages + tui_packages
    
    # 5. Groq AI-Analys (Bulletin)
    if is_deep:
        print("\n=== GENERERAR DJUPANALYS LOKALT ===")
        bulletin = run_groq_analysis(dagens_skord)
        print(bulletin)
    else:
        # Standard workflow: kort morgonbulletin + Telegram
        bulletin = run_groq_analysis(dagens_skord)
        send_telegram_message(bulletin)

if __name__ == "__main__":
    main()
