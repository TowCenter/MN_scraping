import json
import os
from typing import Optional
from playwright.async_api import async_playwright, Page
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://stpaulreporter.com'

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

async def _parse_date_from_element(el) -> Optional[str]:
    """
    Given a Playwright element handle that may contain a date/time, try to extract and
    normalize it to YYYY-MM-DD. Returns None if not found or parse fails.
    """
    if not el:
        return None
    # Prefer a datetime attribute if present (common on <time> elements)
    try:
        datetime_attr = await el.get_attribute('datetime')
        if datetime_attr:
            try:
                dt = parse(datetime_attr)
                return dt.strftime('%Y-%m-%d')
            except Exception:
                pass
        # Otherwise try the visible/text content
        text = await el.text_content()
        if text:
            text = text.strip()
            try:
                dt = parse(text, fuzzy=True)
                return dt.strftime('%Y-%m-%d')
            except Exception:
                return None
    except Exception:
        return None
    return None

async def scrape_page(page: Page):
    """
    Extract article data from the current page.

    Parameters:
        page: Playwright page object

    Returns:
        List of dictionaries containing article data with keys:
        - title: Headline or title of the article
        - date: Publication date in YYYY-MM-DD format or None
        - url: Link to the full article (absolute)
        - scraper: module path for traceability
    """
    items = []

    # Use a robust item selector that matches observed container classes.
    # The examples show .ultp-block-item and .ultp-block-media; include both to be safe.
    item_selector = ".ultp-block-item, .ultp-block-media"
    article_elements = await page.query_selector_all(item_selector)

    for el in article_elements:
        try:
            # Title and URL: prefer the H3 title link as shown in examples
            title_el = await el.query_selector('h3.ultp-block-title a, h2.ultp-block-title a, .ultp-block-title a')
            title_text = None
            url = None

            if title_el:
                title_raw = await title_el.text_content()
                title_text = title_raw.strip() if title_raw else None
                href = await title_el.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

            # Fallback: if no title link found, try first anchor inside the item
            if not title_el:
                fallback_a = await el.query_selector('a')
                if fallback_a:
                    href = await fallback_a.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin(base_url, href.strip())
                    fallback_text = await fallback_a.text_content()
                    if fallback_text and not title_text:
                        title_text = fallback_text.strip()

            # Date: attempt several likely locations. Return None if not found.
            date_value = None
            # Common candidate selectors inside the article container
            date_candidates = [
                'time',
                '.entry-date',
                '.post-date',
                '.published',
                '.posted-on',
                '.ultp-block-meta time',
                '.ultp-block-meta',
            ]
            for sel in date_candidates:
                try:
                    date_el = await el.query_selector(sel)
                    date_parsed = await _parse_date_from_element(date_el)
                    if date_parsed:
                        date_value = date_parsed
                        break
                except Exception:
                    continue

            # If a date wasn't found inside the item, try to find a date on the article page link target
            # (Do not navigate to each link here for performance reasons; skip.)
            # Respect requirement to return None when not found.

            # Only include items that have at least a URL and a title
            if not url and not title_text:
                # Skip entirely empty/malformed items
                continue

            item = {
                'title': title_text if title_text else None,
                'date': date_value,
                'url': url if url else None,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)
        except Exception:
            # Skip single item on unexpected error but continue processing others
            continue

    return items

async def advance_page(page: Page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Try common "next page" selectors in order of preference
    next_selectors = [
        'a[rel="next"]',
        'a.pagination-next',
        'a.next',
        'a[aria-label="Next"]',
        'a[aria-label*="next"]',
        'button.load-more',
        'a.load-more',
        'button[aria-label*="load more"]',
        'button#load-more',
        'a:has-text("Next")',
        'a:has-text("next")',
        'button:has-text("Load more")',
        'button:has-text("Load More")',
        'a:has-text("More")',
    ]

    for sel in next_selectors:
        try:
            handle = await page.query_selector(sel)
            if not handle:
                continue

            # If it's an anchor with an href, navigate to it
            href = await handle.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(base_url, href.strip())
                try:
                    await page.goto(next_url)
                    # Give time for new content to load
                    await page.wait_for_load_state('networkidle', timeout=8000)
                    return
                except Exception:
                    # If navigation fails, try clicking instead
                    pass

            # If it's a button or the anchor is JS-driven, try clicking
            try:
                await handle.scroll_into_view_if_needed()
                await handle.click()
                # Wait a bit for content to load after click (may be XHR)
                await page.wait_for_load_state('networkidle', timeout=8000)
                # Allow any lazy-loaded items to render
                await page.wait_for_timeout(1000)
                return
            except Exception:
                # If click fails, continue to other selectors
                continue
        except Exception:
            continue

    # Fallback: infinite scroll approach
    # Scroll in steps to the bottom to attempt to trigger lazy loading / infinite scroll.
    try:
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        # Perform a few incremental scrolls to encourage loading
        for _ in range(6):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == previous_height:
                # No more height change; break early
                break
            previous_height = new_height
        # Give final time for network requests to complete
        await page.wait_for_timeout(2000)
    except Exception:
        # As a last resort, do a simple scroll-to-bottom and wait (template behavior)
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)
        except Exception:
            pass

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
                    # Create a stable key for deduplication based on url and title (if present)
                    key = (item.get('url'), item.get('title'))
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