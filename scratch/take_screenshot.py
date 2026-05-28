import asyncio
from playwright.async_api import async_playwright
import os

async def main():
    artifact_dir = r"C:\Users\dka12\.gemini\antigravity\brain\409b4279-2035-4cd7-86e6-4a46ccc1facf"
    screenshot_path = os.path.join(artifact_dir, "media__dashboard_screenshot.png")
    
    print("Startar Playwright...")
    async with async_playwright() as p:
        # Starta chromium headless
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1
        )
        page = await context.new_page()
        
        url = "https://dankar120-design.github.io/Travel_Analyser/"
        print(f"Navigerar till {url}...")
        await page.goto(url, wait_until="networkidle")
        
        # Vänta lite extra för att se till att all JS-rendering och animationer är helt klara
        print("Väntar på rendering...")
        await asyncio.sleep(5)
        
        # Spara skärmdump
        print(f"Tar skärmdump till {screenshot_path}...")
        await page.screenshot(path=screenshot_path, full_page=True)
        print("Klart!")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
