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

## 2. API-Budget & Rate Limits (Amadeus Sandbox)

*   **Begränsning:** Amadeus Sandbox tillåter max **2000 gratis anrop per månad**.
*   **Strategi:**
    *   **Orts- & Ruttoptimering:** Vi har en central konfiguration som söker från de 4 avreseorterna (`SFT`, `UME`, `LLA`, `ARN`) till utvalda destinationer.
    *   **Rullande ruttscanning:** För att undvika rate limits under dagliga schemalagda körningar begränsar vi antalet sökta kombinationer i taget genom att fokusera på smarta helger (de närmsta 2-3 månaderna) samt konsoliderade batch-förfrågningar.
    *   **Djupanalys (`--deep`):** Den tunga och detaljerade sökningen för specifika rutter och datum utförs enbart vid manuell on-demand körning lokalt via Google AG, vilket skonar den dagliga kvoten.

## 3. Data- & Statehantering

*   **Time-Series State (`data/price_history.json`):**
    *   Istället for att köra helt stateless sparar Python-skriptet dagens billigaste priser i en ackumulativ time-series fil.
    *   GitHub Actions gör automatiskt en `git commit` av denna fil vid varje körning.
    *   Detta möjliggör att vår dashboard (`index.html`) kan rita upp prishistorik och trendlinjer utan att vi behöver en extern databas.
*   **Dolda avgifter & Bagage:**
    *   Amadeus API returnerar ofta baspriser utan incheckat bagage.
    *   Vi läser av fältet `includedCheckedBags` i responsen och flaggar flygresorna med "⚠️ Endast handbagage" i dashboarden istället för att utesluta dem, vilket ger transparens.

## 4. Valutahantering & Tröskelvärden

*   Amadeus kan returnera priser i EUR som standard. Vi tvingar fram `currencyCode=SEK` i alla API-anrop.
*   Våra filter är satta i källkoden till:
    *   **Weekend / Storstad:** Max 1500 SEK.
    *   **Klassiska veckosemestrar:** Max 3500 SEK.
*   Endast flyg som ligger under dessa tröskelvärden sparas i historiken och skickas till Groq för morgonbulletinen.

## 5. Rollfördelning

1.  **Google AG (Workspace):** Din primära samtalspartner vid datorn. Kan köra `python main.py --deep` lokalt för djupgående, interaktiva analyser i chatten.
2.  **Groq (Telegram):** En renodlad notifieringsmotor. Körs i GitHub Actions och skickar en ultra-kort, lättläst sammanfattning till Telegram, samt en länk till din dashboard på GitHub Pages.
