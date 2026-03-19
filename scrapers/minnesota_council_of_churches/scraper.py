import json
import os
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.mnchurches.org/news'

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

    # Selectors chosen from provided examples:
    # - Article container: div.views-row
    # - Title: .teaser-title a (use text_content())
    # - Date/time: time.datetime (prefer datetime attribute)
    # - URL: href on the title link (relative links resolved to absolute)
    article_nodes = await page.query_selector_all('div.views-row')
    for node in article_nodes:
        try:
            # Title
            title_el = await node.query_selector('.teaser-title a')
            title = None
            if title_el:
                title_text = await title_el.text_content()
                if title_text:
                    title = title_text.strip()
            if not title:
                # If title missing, skip this node (required field)
                continue

            # URL: Prefer href from title link, fallback to any first anchor in the node
            href = None
            if title_el:
                href = await title_el.get_attribute('href')
            if not href:
                other_a = await node.query_selector('a[href]')
                if other_a:
                    href = await other_a.get_attribute('href')
            url = None
            if href:
                url = urllib.parse.urljoin(page.url, href.strip())

            # Date: Prefer datetime attribute on time.datetime
            date_value = None
            time_el = await node.query_selector('.post-date time.datetime, time.datetime')
            if time_el:
                datetime_attr = await time_el.get_attribute('datetime')
                text_content = await time_el.text_content()
                # Try ISO datetime attribute first
                if datetime_attr:
                    try:
                        dt = parse(datetime_attr)
                        date_value = dt.date().isoformat()
                    except Exception:
                        date_value = None
                # Fallback: parse visible text
                if not date_value and text_content:
                    try:
                        dt = parse(text_content, fuzzy=True)
                        date_value = dt.date().isoformat()
                    except Exception:
                        date_value = None

            items.append({
                'title': title,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })
        except Exception:
            # Skip individual item on unexpected errors to keep scraper robust
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Preferred next-page selectors based on provided HTML examples.
    next_selectors = [
        'nav.pager a[rel="next"]',
        '.pager__item--next a[href]',
        'a[rel="next"]',
        'nav.pager a[href*="page="]',
    ]

    next_handle = None
    for sel in next_selectors:
        try:
            next_handle = await page.query_selector(sel)
            if next_handle:
                break
        except Exception:
            next_handle = None

    if next_handle:
        # Try to click the next link first. If clicking fails, fall back to href navigation.
        try:
            await next_handle.scroll_into_view_if_needed()
            # Some pagers use normal navigation; wait for networkidle to detect the page change.
            await next_handle.click()
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except PlaywrightTimeoutError:
                # If waiting for networkidle times out, give a small pause to allow content to load
                await page.wait_for_timeout(1500)
            return
        except Exception:
            # Fallback to href navigation
            try:
                href = await next_handle.get_attribute('href')
                if href:
                    next_url = urllib.parse.urljoin(page.url, href.strip())
                    await page.goto(next_url)
                    try:
                        await page.wait_for_load_state('networkidle', timeout=10000)
                    except PlaywrightTimeoutError:
                        await page.wait_for_timeout(1500)
                    return
            except Exception:
                pass

    # If no next link found or navigation failed, perform infinite scroll fallback.
    # Scroll down in steps to allow lazy-loading content to appear.
    previous_height = await page.evaluate("() => document.body.scrollHeight")
    for _ in range(6):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        await page.wait_for_timeout(1200)
        new_height = await page.evaluate("() => document.body.scrollHeight")
        if new_height == previous_height:
            # If no change in height, give a short pause and break early
            await page.wait_for_timeout(800)
            break
        previous_height = new_height

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
                    # Use a deterministic key for deduplication (title + url + date)
                    dedupe_key = (item.get('title'), item.get('url'), item.get('date'))
                    if dedupe_key not in seen:
                        seen.add(dedupe_key)
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