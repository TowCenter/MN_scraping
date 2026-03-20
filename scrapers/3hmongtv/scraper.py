import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://hbctv.net/stories'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.browser.close()
        await self.playwright.stop()

async def scrape_page(page):
    """
    Extract article data from the current page.

    Parameters:
        page: Playwright page object

    Returns:
        List of dictionaries containing article data with keys:
        - title: Headline or title of the article
        - date: Publication date in YYYY-MM-DD format or None
        - url: Absolute URL to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use anchor elements that link to story pages as article containers.
    # This selector is robust based on the provided examples.
    try:
        anchors = await page.query_selector_all('a[href^="/stories/"]')
    except Exception:
        anchors = []

    for a in anchors:
        try:
            # Extract URL and resolve to absolute
            href = await a.get_attribute('href')
            if not href:
                continue
            url = urllib.parse.urljoin(base_url, href.strip())

            # Extract title using h3 inside the anchor; use text_content() per instructions
            title_el = await a.query_selector('h3')
            title = None
            if title_el:
                raw_title = await title_el.text_content()
                if raw_title:
                    title = raw_title.strip()

            # Title and URL are required; if title missing, skip this item
            if not title or not url:
                continue

            # Extract date if present. Based on examples, date sits in a .flex... span:last-child inside the anchor.
            date = None
            try:
                date_el = await a.query_selector('.flex.items-center.justify-between span:last-child')
                if date_el:
                    raw_date = await date_el.text_content()
                    if raw_date:
                        raw_date = raw_date.strip()
                        # Parse with dateutil; if parsing fails, set None
                        try:
                            parsed = parse(raw_date, fuzzy=True)
                            date = parsed.date().isoformat()
                        except Exception:
                            date = None
            except Exception:
                date = None

            items.append({
                'title': title,
                'date': date,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Protect against unexpected DOM issues for individual items
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    If no explicit pagination is present, performs an infinite-scroll style load:
    scroll to the bottom repeatedly until page height no longer increases or a short timeout is reached.

    Parameters:
        page: Playwright page object
    """
    # Attempt a few gentle scrolls to trigger lazy-loading/infinite scroll.
    try:
        # Initial page height
        prev_height = await page.evaluate("() => document.body.scrollHeight")
        max_attempts = 6
        attempts = 0

        while attempts < max_attempts:
            # Scroll to bottom
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            # Wait for potential content to load
            await page.wait_for_timeout(1500)

            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == prev_height:
                # No more content loaded on this scroll; try one more time and then stop
                attempts += 1
                prev_height = new_height
            else:
                # Content increased; reset attempts and continue scrolling
                prev_height = new_height
                attempts = 0

        # Small pause after scrolling finished to allow any remaining dynamic content to settle
        await page.wait_for_timeout(1000)

    except Exception:
        # If anything goes wrong with scrolling, just return and allow caller to handle termination.
        return

async def get_first_page(base_url=base_url):
    """Fetch only the first page of articles."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        await page.goto(base_url)
        items = await scrape_page(page)
        await page.close()
        return items

async def get_all_articles(base_url=base_url, max_pages=100):
    """Fetch all articles from all pages."""

    async with PlaywrightContext() as context:
        items = []
        seen = set()
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        page_count = 0

        await page.goto(base_url)

        page_count = 0
        item_count = 0  # previous count
        new_item_count = 0  # current count

        try:
            while page_count < max_pages:
                page_items = await scrape_page(page)
                for item in page_items:
                    key = tuple(sorted((k, v) for k, v in item.items() if v is not None))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                if new_item_count <= item_count:
                    break

                page_count += 1
                item_count = new_item_count

                await advance_page(page)

        except Exception as e:
            print(f"Error occurred while getting next page: {e}")


        await page.close()
        return items

async def main():
    """Main execution function."""
    all_items = await get_all_articles()

    # Save results to JSON
    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    with open(result_path, 'w') as f:
        json.dump(all_items, f, indent=2)
    print(f"Results saved to {result_path}")

if __name__ == "__main__":
    asyncio.run(main())