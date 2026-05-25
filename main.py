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

# Fallback-ordlista för charterdestinationer (IATA -> Stad, Land)
IATA_FALLBACK = {
    "AYT": ("Alanya/Antalya", "Turkiet"),
    "GZP": ("Alanya", "Turkiet"),
    "CHQ": ("Chania", "Grekland"),
    "HER": ("Heraklion", "Grekland"),
    "JSI": ("Skiathos", "Grekland"),
    "KGS": ("Kos", "Grekland"),
    "KVA": ("Thassos", "Grekland"),
    "LCA": ("Larnaca", "Cypern"),
    "LPA": ("Gran Canaria", "Spanien"),
    "PMI": ("Mallorca", "Spanien"),
    "PVK": ("Preveza/Lefkas", "Grekland"),
    "RHO": ("Rhodos", "Grekland"),
    "SPU": ("Split", "Kroatien"),
    "TFS": ("Teneriffa", "Spanien"),
    "FUE": ("Fuerteventura", "Spanien"),
    "ACE": ("Lanzarote", "Spanien"),
    "DLM": ("Dalaman", "Turkiet"),
    "BJV": ("Bodrum", "Turkiet")
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
                
                # Tillämpa IATA-fallback om geografisk information saknas
                if resort_name == "Okänt resmål" or country_name == "Okänt land":
                    fallback_info = IATA_FALLBACK.get(destination_code)
                    if fallback_info:
                        if resort_name == "Okänt resmål":
                            resort_name = fallback_info[0]
                        if country_name == "Okänt land":
                            country_name = fallback_info[1]
                    else:
                        # Fallback till själva IATA-koden om den inte finns i fallback-listan
                        if resort_name == "Okänt resmål":
                            resort_name = destination_code

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
                
            # Extrahera destination_code robust från flightInfo
            outbound_flights = offer.get("flightInfo", {}).get("outboundFlights", [])
            destination_code = None
            if outbound_flights and isinstance(outbound_flights, list) and len(outbound_flights) > 0:
                destination_code = outbound_flights[0].get("arrivalAirport")
            
            if not destination_code:
                destination_code = offer.get("hotel", {}).get("geo", {}).get("destinationCode")
            
            resort_name = offer.get("hotel", {}).get("geo", {}).get("resort") or "Okänt resmål"
            country_name = offer.get("hotel", {}).get("geo", {}).get("country") or "Okänt land"
            
            # Om IATA-kod saknas eller är en intern TUI G-kod, använd resort_name som destination
            display_destination = destination_code
            if destination_code and destination_code.startswith("G-"):
                display_destination = resort_name
                
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
                "destination": display_destination,
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


def consolidate_history_blocks(history_list):
    """Konsoliderar och deduplicerar historiska körningar så att det max finns ett block per datum."""
    by_date = {}
    for run in history_list:
        date = run.get("date")
        if not date:
            continue
        if date not in by_date:
            by_date[date] = []
        by_date[date].extend(run.get("flights", []))
        
    consolidated = []
    for date, items in sorted(by_date.items()):
        # Deduplicera items för detta datum
        unique_items_dict = {}
        for item in items:
            if item["type"] == "flight":
                key = (
                    item["origin"], 
                    item["destination"], 
                    item["departure_date"], 
                    item.get("return_date")
                )
            else: # package
                key = (
                    item["origin"], 
                    item["destination"], 
                    item["departure_date"], 
                    item.get("package_data", {}).get("nights"), 
                    item.get("package_data", {}).get("hotel_name"), 
                    item.get("package_data", {}).get("operator")
                )
            unique_items_dict[key] = item
        
        consolidated.append({
            "date": date,
            "flights": list(unique_items_dict.values())
        })
    return consolidated

def compute_deal_factor(item, history):
    """
    Beräknar deal factor taggar baserat på historiken.
    Nivå 3: Under 500 kr/natt (alltid tillgänglig för paketresor)
    Nivå 1: Lägsta pris på 30 dagar för denna rutt
    Nivå 2: Prissänkning ≥15% mot senaste observerade priset för samma rutt
    """
    tags = []
    price = float(item.get("price", 0))
    if price <= 0:
        return tags
        
    # Nivå 3: Paketpris per natt
    if item["type"] == "package":
        nights = int(item.get("package_data", {}).get("nights", 7))
        if price / max(1, nights) < 500:
            tags.append("Under 500 kr/natt!")
            
    # Samla historiska priser för denna rutt (sorterat kronologiskt)
    route_prices = []
    
    # history är listan state["history"]
    # Vi sorterar historiken på datum först
    sorted_history = sorted(history, key=lambda x: x.get("date", ""))
    
    for run in sorted_history:
        # Undvik att jämföra med dagens egen körning
        run_date = run.get("date")
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        if run_date == today_str:
            continue
            
        for h_item in run.get("flights", []):
            if h_item["type"] == item["type"] and h_item["origin"] == item["origin"] and h_item["destination"] == item["destination"]:
                # För paketresor, matcha även antal nätter så vi inte jämför 7 nätter mot 14 nätter
                if item["type"] == "package":
                    h_nights = h_item.get("package_data", {}).get("nights")
                    i_nights = item.get("package_data", {}).get("nights")
                    h_hotel = h_item.get("package_data", {}).get("hotel_name")
                    i_hotel = item.get("package_data", {}).get("hotel_name")
                    if h_nights != i_nights or h_hotel != i_hotel:
                        continue
                route_prices.append(float(h_item["price"]))
                
    if route_prices:
        min_hist = min(route_prices)
        last_hist = route_prices[-1]
        
        # Nivå 1: Historiskt lägsta
        if price <= min_hist:
            tags.append("Lägsta pris på 30 dagar!")
        # Nivå 2: Prissänkning ≥15% mot senaste
        if last_hist >= price * 1.15:
            pct = int((1 - price / last_hist) * 100)
            tags.append(f"Ned {pct}% sedan senast!")
            
    return tags


def update_state(new_flights, new_packages=None):
    """Sparar och ackumulerar historisk flyg- och paketdata i data/price_history.json (max 30 dagar) med strikt deduplicering och konsolidering."""
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

            # Sanera gamla interna TUI G-koder till riktiga IATA-koder i historiken
            if item.get("type") == "package" and item.get("source") == "tui":
                tui_g_map = {
                    "G-000000292": "CHQ", "G-000000293": "CHQ", "G-000000294": "CHQ", "G-000000295": "CHQ",
                    "G-000000243": "PMI", "G-000000238": "PMI", "G-000000562": "PMI",
                    "G-000000653": "AYT", "G-000001539": "AYT"
                }
                dest = item.get("destination")
                if dest in tui_g_map:
                    item["destination"] = tui_g_map[dest]

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

        unique_items_dict = {}
        
        for item in combined_items:
            if item["type"] == "flight":
                key = (
                    item["origin"], 
                    item["destination"], 
                    item["departure_date"], 
                    item["return_date"]
                )
            else: # package
                key = (
                    item["origin"], 
                    item["destination"], 
                    item["departure_date"], 
                    item["package_data"]["nights"], 
                    item["package_data"]["hotel_name"], 
                    item["package_data"]["operator"]
                )
            unique_items_dict[key] = item
            
        unique_items = list(unique_items_dict.values())
                
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
    
    # Konsolidera och deduplicera historiken så det max finns ett block per datum (rensar även gamla synder)
    state["history"] = consolidate_history_blocks(state["history"])
        
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
            
            # Injetera unikt ID för frontend (localStorage)
            item["id"] = key
            
            # Dynamisk beräkning av deal tags (Nivå 1, 2, 3) baserat på historiken
            item["tags"] = compute_deal_factor(item, state["history"])
            
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

        /* Mobilanpassad header-stapling */
        @media (max-width: 768px) {{
            header {{
                flex-direction: column;
                align-items: flex-start;
                gap: 1rem;
            }}
        }}

        /* Sorterings-, resetyp- och favoritfilter-rader */
        .filter-row-secondary {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 0.75rem;
        }}

        /* Interaktionsknappar på korten */
        .card-actions {{
            position: absolute;
            top: 1rem;
            left: 1rem;
            display: flex;
            gap: 0.5rem;
            z-index: 10;
        }}
        .action-btn {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            cursor: pointer;
            border-radius: 50%;
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
            color: var(--text-color);
        }}
        .action-btn:hover {{
            background: rgba(255, 255, 255, 0.1);
        }}

        /* Basstruktur för dynamic brand-badge */
        .brand-badge {{
            position: absolute;
            top: 1rem;
            right: 1rem;
            padding: 0.25rem 0.75rem;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }}

        .deal-badge {{
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
            padding: 0.25rem 0.75rem;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }}

        /* Varumärkesunika ram-accents och skugg-glows */
        :root {{
            --ving-color: var(--accent);
            --ving-glow: rgba(6, 182, 212, 0.4);
            --tui-color: #09a0e0;
            --tui-glow: rgba(9, 160, 224, 0.4);
            --skyscanner-color: var(--primary);
            --skyscanner-glow: rgba(79, 70, 229, 0.4);
        }}

        .flight-card.brand-ving:hover {{
            border-color: var(--ving-color);
            box-shadow: 0 10px 25px var(--ving-glow);
        }}
        .flight-card.brand-tui:hover {{
            border-color: var(--tui-color);
            box-shadow: 0 10px 25px var(--tui-glow);
        }}
        .flight-card.brand-skyscanner:hover {{
            border-color: var(--skyscanner-color);
            box-shadow: 0 10px 25px var(--skyscanner-glow);
        }}

        .empty-state {{
            grid-column: 1 / -1;
            text-align: center;
            padding: 4rem 2rem;
            background: var(--card-bg);
            border: 1px dashed var(--card-border);
            border-radius: 16px;
            backdrop-filter: blur(12px);
            margin: 2rem 0;
        }}
        .empty-state h2 {{
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--text-color);
            margin-bottom: 0.5rem;
        }}
        .empty-state p {{
            color: var(--text-muted);
            font-size: 0.95rem;
            max-width: 500px;
            margin: 0 auto;
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
            <div class="filter-title">Filter & Sortering</div>
            
            <div class="filter-row-secondary">
                <div style="font-size: 0.85rem; color: var(--text-muted); width: 100%;">Avreseort:</div>
                <button class="filter-btn active" data-type="origin" data-value="ALL" onclick="setFilter('origin', 'ALL')">Alla orter</button>
                <button class="filter-btn" data-type="origin" data-value="SFT" onclick="setFilter('origin', 'SFT')">Skellefteå (SFT)</button>
                <button class="filter-btn" data-type="origin" data-value="UME" onclick="setFilter('origin', 'UME')">Umeå (UME)</button>
                <button class="filter-btn" data-type="origin" data-value="LLA" onclick="setFilter('origin', 'LLA')">Luleå (LLA)</button>
                <button class="filter-btn" data-type="origin" data-value="ARN" onclick="setFilter('origin', 'ARN')">Arlanda (ARN)</button>
            </div>
            
            <div class="filter-row-secondary">
                <div style="font-size: 0.85rem; color: var(--text-muted); width: 100%;">Resetyp:</div>
                <button class="filter-btn active" data-type="type" data-value="ALL" onclick="setFilter('type', 'ALL')">Alla typer</button>
                <button class="filter-btn" data-type="type" data-value="flight" onclick="setFilter('type', 'flight')">Flygstolar ✈️</button>
                <button class="filter-btn" data-type="type" data-value="package" onclick="setFilter('type', 'package')">Paketresor 🌴</button>
            </div>

            <div class="filter-row-secondary">
                <div style="font-size: 0.85rem; color: var(--text-muted); width: 100%;">Sortering:</div>
                <button class="filter-btn active" data-type="sort" data-value="price" onclick="setFilter('sort', 'price')">💰 Pris</button>
                <button class="filter-btn" data-type="sort" data-value="date" onclick="setFilter('sort', 'date')">📅 Datum</button>
                <button class="filter-btn" data-type="sort" data-value="duration" onclick="setFilter('sort', 'duration')">⏳ Längd</button>
            </div>
            
            <div class="filter-row-secondary" style="margin-top: 1.5rem;">
                <div style="font-size: 0.85rem; color: var(--text-muted); width: 100%;">Personliga val:</div>
                <button class="filter-btn" data-type="fav" data-value="true" onclick="setFilter('fav', 'true')" style="border-color: #ef4444;">❤️ Visa endast stjärnmärkta</button>
                <button class="filter-btn" data-type="hidden" data-value="true" onclick="setFilter('hidden', 'true')">👁️ Visa dolda resor</button>
            </div>
        </section>

        <main class="flight-grid" id="flightGrid">
            <!-- Flygkort injectas via JavaScript -->
        </main>
    </div>

    <script>
        const flights = {json.dumps(items_list, ensure_ascii=False)};
        let activeOrigin = 'ALL';
        let activeType = 'ALL';
        let activeSort = 'price';
        let showFavoritesOnly = false;
        let showHidden = false;

        function toggleFav(id, event) {{
            event.preventDefault();
            let favs = JSON.parse(localStorage.getItem('travel_favs') || '[]');
            if (favs.includes(id)) {{
                favs = favs.filter(x => x !== id);
            }} else {{
                favs.push(id);
            }}
            localStorage.setItem('travel_favs', JSON.stringify(favs));
            renderFlights();
        }}

        function toggleHide(id, event) {{
            event.preventDefault();
            let hidden = JSON.parse(localStorage.getItem('travel_hidden') || '[]');
            if (hidden.includes(id)) {{
                hidden = hidden.filter(x => x !== id);
            }} else {{
                hidden.push(id);
            }}
            localStorage.setItem('travel_hidden', JSON.stringify(hidden));
            renderFlights();
        }}

        function setFilter(type, value) {{
            if (type === 'origin') activeOrigin = value;
            if (type === 'type') activeType = value;
            if (type === 'sort') activeSort = value;
            if (type === 'fav') showFavoritesOnly = !showFavoritesOnly;
            if (type === 'hidden') showHidden = !showHidden;
            
            // Uppdatera UI state för knappar
            document.querySelectorAll('.filter-btn').forEach(btn => {{
                const btnType = btn.dataset.type;
                const btnVal = btn.dataset.value;
                if (btnType === type) {{
                    if (['fav', 'hidden'].includes(btnType)) {{
                        btn.classList.toggle('active', type === 'fav' ? showFavoritesOnly : showHidden);
                    }} else {{
                        btn.classList.toggle('active', btnVal === value);
                    }}
                }}
            }});
            
            renderFlights();
        }}

        function renderFlights() {{
            const grid = document.getElementById('flightGrid');
            grid.innerHTML = '';
            
            const favs = JSON.parse(localStorage.getItem('travel_favs') || '[]');
            const hidden = JSON.parse(localStorage.getItem('travel_hidden') || '[]');
            
            let filtered = flights.filter(f => {{
                const matchOrigin = activeOrigin === 'ALL' || f.origin === activeOrigin;
                const matchType = activeType === 'ALL' || f.type === activeType;
                const matchHidden = showHidden ? hidden.includes(f.id) : !hidden.includes(f.id);
                const matchFav = showFavoritesOnly ? favs.includes(f.id) : true;
                
                return matchOrigin && matchType && matchHidden && matchFav;
            }});
            
            if (filtered.length === 0) {{
                grid.innerHTML = `
                    <div class="empty-state">
                        <h2>Inga resor hittades för dina valda filter.</h2>
                        <p>Kanske har du dolt dem alla, eller så finns det inga erbjudanden för denna kombination just nu.</p>
                    </div>
                `;
                return;
            }}

            // Sortering
            filtered.sort((a, b) => {{
                if (activeSort === 'price') return a.price - b.price;
                if (activeSort === 'date') return new Date(a.departure_date) - new Date(b.departure_date);
                if (activeSort === 'duration') {{
                    const durA = a.type === 'flight' ? (a.flight_data && a.flight_data.duration ? a.flight_data.duration : 0) : (a.package_data && a.package_data.nights ? a.package_data.nights : 0);
                    const durB = b.type === 'flight' ? (b.flight_data && b.flight_data.duration ? b.flight_data.duration : 0) : (b.package_data && b.package_data.nights ? b.package_data.nights : 0);
                    return durA - durB;
                }}
                return 0;
            }});

            filtered.forEach(f => {{
                const card = document.createElement('div');
                const isFav = favs.includes(f.id);
                const isHidden = hidden.includes(f.id);
                
                let brandClass = '';
                let badgeHtml = '';
                if (f.type === 'package') {{
                    const operator = f.package_data.operator.toLowerCase();
                    if (operator === 'ving') brandClass = 'brand-ving';
                    else if (operator === 'tui') brandClass = 'brand-tui';
                    badgeHtml = `<span class="brand-badge" style="background: rgba(6, 182, 212, 0.2); color: var(--accent); border-color: var(--accent);">🌴 Paketresa (${{f.package_data.operator}})</span>`;
                }} else {{
                    brandClass = 'brand-skyscanner';
                    badgeHtml = `<span class="brand-badge" style="background: rgba(16, 185, 129, 0.2); color: var(--success); border-color: var(--success);">✈️ Flygstol</span>`;
                }}
                
                // Generera HTML för deal-taggar (historiskt lägsta, prissänkning, pris per natt)
                let tagsHtml = '';
                if (f.tags && f.tags.length > 0) {{
                    tagsHtml = f.tags.map(t => {{
                        let icon = '🔥';
                        let style = 'background: rgba(245, 158, 11, 0.15); color: var(--warning); border: 1px solid rgba(245, 158, 11, 0.3);';
                        if (t.includes('Ned')) {{
                            icon = '📉';
                            style = 'background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3);';
                        }} else if (t.includes('Lägsta')) {{
                            icon = '⭐';
                            style = 'background: rgba(79, 70, 229, 0.2); color: #a5b4fc; border: 1px solid rgba(79, 70, 229, 0.4);';
                        }}
                        return `<span class="deal-badge" style="${{style}}">${{icon}} ${{t}}</span>`;
                    }}).join(' ');
                }}
                
                card.className = `flight-card ${{brandClass}}`;
                
                const isSFT = f.origin === 'SFT' ? '⚡ Direkt/Snabbast' : 'Bil/Transfer';
                
                const favColor = isFav ? '#ef4444' : 'var(--text-color)';
                const hideIcon = isHidden ? '👁️' : '❌';
                
                let detailsHtml = '';
                if (f.type === 'flight') {{
                    const bagText = f.flight_data.baggage_included ? '🎒 Incheckat bagage ingår' : '⚠️ Endast handbagage';
                    const bagClass = f.flight_data.baggage_included ? 'yes' : 'no';
                    detailsHtml = `
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
                    `;
                }} else {{
                    detailsHtml = `
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
                    `;
                }}
                
                let btnText = 'Boka nu';
                let btnStyle = 'background: linear-gradient(135deg, var(--primary), #3b82f6); box-shadow: 0 4px 12px var(--primary-glow);';
                if (f.type === 'package') {{
                    if (f.package_data.operator === 'TUI') {{
                        btnText = 'Boka hos TUI';
                        btnStyle = 'background: linear-gradient(135deg, #09a0e0, #0284c7); box-shadow: 0 4px 12px rgba(9, 160, 224, 0.4);';
                    }} else {{
                        btnText = 'Boka hos Ving';
                        btnStyle = 'background: linear-gradient(135deg, var(--accent), #0891b2); box-shadow: 0 4px 12px rgba(6, 182, 212, 0.4);';
                    }}
                }}

                card.innerHTML = `
                    <div class="card-actions">
                        <div class="action-btn" onclick="toggleFav('${{f.id}}', event)" style="color: ${{favColor}}" title="Markera som favorit">❤️</div>
                        <div class="action-btn" onclick="toggleHide('${{f.id}}', event)" title="${{isHidden ? 'Återställ dolda' : 'Dölj resa'}}">${{hideIcon}}</div>
                    </div>
                    <div style="display: flex; gap: 0.4rem; flex-wrap: wrap; padding-top: 2.5rem;">
                        ${{badgeHtml}}
                        ${{tagsHtml}}
                    </div>
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
                            ${{detailsHtml}}
                        </div>
                    </div>
                    <a href="${{f.deep_link}}" target="_blank" rel="noopener noreferrer" class="book-btn" style="${{btnStyle}}">${{btnText}}</a>
                `;
                
                grid.appendChild(card);
            }});
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
    
    # Pre-processing av flygfynd:
    # 1. Definiera en värde-normaliseringsfunktion (pris per dag/natt)
    def get_value_score(f):
        price = float(f.get("price", 999999))
        if f.get("type") == "package":
            nights = max(1, int(f.get("package_data", {}).get("nights", 7)))
            return price / nights
        else:
            try:
                dep = datetime.datetime.strptime(f["departure_date"], "%Y-%m-%d")
                ret = datetime.datetime.strptime(f["return_date"], "%Y-%m-%d")
                days = max(1, (ret - dep).days)
            except Exception:
                days = 1
            return price / days

    # 2. Separera Skellefteå (SFT) vs icke-SFT och sortera efter value_score
    sft_deals = [f for f in flights if f.get("origin") == "SFT"]
    non_sft_deals = [f for f in flights if f.get("origin") != "SFT"]
    
    sft_deals.sort(key=get_value_score)
    non_sft_deals.sort(key=get_value_score)
    
    # 3. Budgetbaserad trunkering (MAX_DEALS = 12, SFT i absolut första hand)
    MAX_DEALS = 12
    selected_deals = sft_deals[:MAX_DEALS]
    
    remaining = MAX_DEALS - len(selected_deals)
    if remaining > 0:
        selected_deals += non_sft_deals[:remaining]
        
    # Läs historiken för att kunna beräkna deal-taggar för Groq
    history = []
    state_file = "data/price_history.json"
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as hf:
                state_data = json.load(hf)
                history = state_data.get("history", [])
        except Exception:
            pass

    print(f"[Groq Pre-processing] Skickar {len(selected_deals)} utvalda resor av {len(flights)} totalt (SFT: {len([d for d in selected_deals if d.get('origin') == 'SFT'])}, Övriga: {len([d for d in selected_deals if d.get('origin') != 'SFT'])})")
    
    # Förbered kompakt JSON-data för Groq (polymorfiskt säker)
    compact_data = []
    for f in selected_deals:
        # Beräkna taggar dynamiskt baserat på historiken
        tags = compute_deal_factor(f, history)
        tag_str = f" [FYNDFAKTOR: {', '.join(tags)}]" if tags else ""
        
        if f['type'] == 'flight':
            compact_data.append({
                "Typ": "Flyg" + tag_str,
                "Rutt": f"{f['origin']}->{f['destination']} ({f['destination_name']})",
                "Datum": f"{f['departure_date']} till {f['return_date']}",
                "Pris": f"{f['price']} SEK",
                "Byten": f"{f['flight_data']['outbound_stops']}/{f['flight_data']['inbound_stops']}",
                "Baggage": "Ingår" if f['flight_data']['baggage_included'] else "Endast Handbagage"
            })
        elif f['type'] == 'package':
            compact_data.append({
                "Typ": f"Paketresa ({f['package_data']['operator']})" + tag_str,
                "Rutt": f"{f['origin']}->{f['destination']} ({f['destination_name']})",
                "Datum": f"{f['departure_date']} till {f['return_date']} ({f['package_data']['nights']} nätter)",
                "Pris": f"{f['price']} SEK",
                "Boende": f['package_data']['hotel_name']
            })
        
    system_prompt = (
        "Du är en personlig reseexpert för en användare bosatt i Skellefteå (SFT). "
        "Din uppgift är att skriva en extremt kortfattad, slagkraftig och lockande morgonsammanfattning på svenska "
        "av dygnets absolut bästa fynd utifrån den tillhandahållna listan (som redan är sorterad med Skellefteå-fynd först, följt av andra närliggande flygplatser).\n\n"
        "Regler:\n"
        "1. Analysera listan och lyft fram de 2-3 absolut bästa fynden.\n"
        "2. Om en resa har en '[FYNDFAKTOR: ...]'-tagg i sin Typ, ska du utnyttja detta för att förklara VARFÖR det är ett fynd på ett engagerande sätt (t.ex. 'Detta är det lägsta priset vi sett på 30 dagar!' eller 'Boendet kostar under otroliga 500 kr per natt!').\n"
        "3. Skellefteå-fokus: Om det finns ett bra fynd direkt från SFT ska det hyllas först och mest! Om det däremot finns ett enastående fynd från Umeå (UME), Luleå (LLA) eller Arlanda (ARN) som gör att det är värt transfern, nämn det kort.\n"
        "4. Håll språket personligt, inspirerande men mycket kortfattat (max 140 ord!). Använd korta meningar och punktlistor.\n"
        "5. Formatera svaret i ren och enkel text så att den lätt kan konverteras till Telegram-Markdown. Undvik komplex HTML."
    )
    
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Här är dagens absolut bästa fynd: {json.dumps(compact_data, ensure_ascii=False)}"}
        ],
        "temperature": 0.1
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
