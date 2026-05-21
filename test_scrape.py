"""
Minimal Playwright smoke test.
Run with:  py -3.13 test_scrape.py

Opens LinkedIn in a visible browser, waits for the page to load,
and prints the page title + first 500 chars of visible text.
"""

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False, slow_mo=500)
    page = browser.new_page()

    print("Opening LinkedIn...")
    page.goto("https://www.linkedin.com", wait_until="domcontentloaded", timeout=30000)

    title = page.title()
    print(f"Page title: {title}")

    body_text = page.inner_text("body")[:500]
    print(f"\nFirst 500 chars of page text:\n{body_text}")

    print("\nPlaywright is working. Closing browser...")
    browser.close()
