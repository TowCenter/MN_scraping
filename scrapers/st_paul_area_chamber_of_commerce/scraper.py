import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.stpaulchamber.com/blog'

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
        - url: Absolute link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Choose item container that reliably groups title, date, and link.
    # Based on examples, use 'div.blog-post' which encloses each article.
    article_elements = await page.query_selector_all('div.blog-post')

    for el in article_elements:
        try:
            # Title and URL: anchor with class 'blog-title-link'
            title_el = await el.query_selector('a.blog-title-link')
            title = None
            url = None
            if title_el:
                raw_title = await title_el.text_content()
                if raw_title:
                    title = raw_title.strip()
                href = await title_el.get_attribute('href')
                if href:
                    # Normalize URL (handles protocol-relative // and relative paths)
                    url = urllib.parse.urljoin(base_url, href.strip())

            # Date: span.date-text preferred, fallback to p.blog-date
            date = None
            date_el = await el.query_selector('span.date-text')
            if not date_el:
                date_el = await el.query_selector('p.blog-date')
            if date_el:
                raw_date = await date_el.text_content()
                if raw_date:
                    raw_date = raw_date.strip()
                    # Safely parse date; return None if parsing fails
                    try:
                        parsed = parse(raw_date, fuzzy=True)
                        date = parsed.date().isoformat()
                    except Exception:
                        date = None

            # Only include items that have at least title and url (required)
            if title and url:
                items.append({
                    'title': title,
                    'date': date,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                })

        except Exception:
            # Skip item on unexpected errors but continue scraping others
            continue

    return items

async def advance_page(page):
    """
    Navigate to the next (older) page of blog posts.
    This Weebly blog uses "Previous" to go to older posts.

    Parameters:
        page: Playwright page object
    """
    # Weebly blogs use "Previous" link for older posts
    # Look for any anchor containing "Previous" or "Older" text
    links = await page.query_selector_all('a')
    for link in links:
        try:
            text = await link.text_content()
            if not text:
                continue
            text_lower = text.strip().lower()
            if text_lower in ('previous', 'older posts', 'older', '« previous'):
                href = await link.get_attribute('href')
                if href:
                    next_url = urllib.parse.urljoin(page.url, href.strip())
                    await page.goto(next_url)
                    await page.wait_for_load_state('networkidle', timeout=10000)
                    return
        except Exception:
            continue

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
                    # create a stable key excluding None values
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