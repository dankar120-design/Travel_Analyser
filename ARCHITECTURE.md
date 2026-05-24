# Architecture - Travel Analyser

Denna fil beskriver systemarkitekturen, dataflödet och de viktigaste strategierna för den automatiserade reseanalysatorn.

## 1. Systemöversikt

Reseanalysatorn är ett serverlöst, automatiserat system som körs via GitHub Actions och skapar en premium dashboard på GitHub Pages samt skickar morgonsammanfattningar till Telegram.

```
+-------------------------------------------------------------+
|                     GitHub Actions (cron)                   |
|                                                             |
| 1. Anropa Amadeus API (SEK) --+                              |
|                               v                              |
| 2. Rengör & filtrera rådata (Max 1500 kr / 3500 kr)         |
|                               |                              |
| 3. Spara till local state ----v----> [data/price_history.json]|
|                               |                              |
| 4. Generera statisk HTML -----v----> [index.html]            |
|                               |                              |
| 5. Deploy till GitHub Pages --+                              |
|                               |                              |
| 6. Skicka rensad JSON --------v----> [Groq Llama 3.3]        |
|                                        |                     |
| 7. Telegram push-bulletin ------------v----> [Telegram Bot]  |
+-------------------------------------------------------------+
```

## 2. API-Budget & Rate Limits (Travelpayouts API)

*   **Begränsning:** Travelpayouts Data Access API är helt gratis och tillåter mycket generösa rate limits (tusentals anrop per dag), vilket gör det perfekt för hobbyprojekt.
*   **Strategi:**
    *   **Cachad sökdata:** API:et returnerar priser som sökts av andra användare de senaste 48 timmarna. Det innebär att sökningarna går extremt snabbt.
    *   **Orts- & Ruttoptimering:** För att hålla cachen levande och relevant scannar vi en specifik ort (SFT, UME, LLA, ARN) per veckodag under normal schemalagd körning.
    *   **Fullständig skanning (`--all-origins`):** Scannar alla 4 avreseorter parallellt, vilket är perfekt för att snabbt populera dashboarden.

## 3. Data- & Statehantering

*   **Time-Series State (`data/price_history.json`):**
    *   Istället för att köra helt stateless sparar Python-skriptet dagens billigaste priser i en ackumulativ time-series fil.
    *   GitHub Actions gör automatiskt en `git commit` av denna fil vid varje körning.
    *   Detta möjliggör att vår dashboard (`dist/index.html`) kan rita upp prishistorik och trendlinjer utan att vi behöver en extern databas.
*   **Dolda avgifter & Bagage:**
    *   Eftersom Travelpayouts är ett cachedata-API garanterar responsen inte bagageinfo. Vi flaggar resorna som "🎒 Endast handbagage" som standard och uppmuntrar till paritetskontroll vid bokning.

## 4. Valutahantering & Tröskelvärden

*   Vi tvingar fram `currency=SEK` i alla API-anrop till Travelpayouts.
*   Våra filter är satta i källkoden till:
    *   **Weekend / Storstad:** Max 1500 SEK.
    *   **Klassiska veckosemestrar:** Max 3500 SEK.
*   Endast flyg som ligger under dessa tröskelvärden sparas i historiken och skickas till Groq för morgonbulletinen.

## 5. Rollfördelning

1.  **Google AG (Workspace):** Din primära samtalspartner vid datorn. Kan köra `python main.py --deep` lokalt för djupgående, interaktiva analyser i chatten.
2.  **Groq (Telegram):** En renodlad notifieringsmotor. Körs i GitHub Actions och skickar en ultra-kort, lättläst sammanfattning till Telegram, samt en länk till din dashboard på GitHub Pages.

## 6. Framtida Utbyggnad: Ving Sista-Minuten-skrapare 🌴

För att utöka systemet med fullständiga paketresor (flyg + hotell) från Vings eller TUIs sista-minuten-sidor planerar vi följande modulära skraparkomponent:

```
+------------------+     +------------------------+     +--------------------------+
|  Ving.se / TUI   | --> | Ving Scraper Component | --> |    Travel Analyser       |
| sista-minuten-   |     | (requests / BS4 i      |     | (Sparar sista-minuten-   |
| HTML eller JSON  |     | main.py)               |     | paket i price_history)   |
+------------------+     +------------------------+     +--------------------------+
                                                                     |
                                                                     v
                                                        +--------------------------+
                                                        | Dashboard & Telegram     |
                                                        | ritar ut paket & skickar |
                                                        | specialbulletiner!       |
                                                        +--------------------------+
```

### Tekniskt genomförande:
1.  **Skrapningsmotor (BeautifulSoup):** En ny python-funktion `scrape_ving_lastminute()` läggs till i `main.py`. Den laddar ner HTML-källkoden från Vings sista-minuten-portal för t.ex. SFT, UME, LLA och ARN.
2.  **Rengöring & Parsning:** Roboten extraherar destination, utresedatum, antal dagar (ofta 7 eller 14), hotellnamn, klassificering (stjärnor/ospecificerat) samt det totala paketpriset.
3.  **Matris- & Prisfiltrering:** Endast paketresor under t.ex. **4 000 SEK** (vilket är ett fantastiskt pris för flyg + hotell) sparas.
4.  **Enhetlig Presentation:** Resorna sparas i `price_history.json` med en ny typ-tagg (`"package"` istället för `"flight"`). Både dashboarden och Groq-analysatorn uppdateras för att vackert kunna presentera dessa paket som separata kort och bulletiner!

