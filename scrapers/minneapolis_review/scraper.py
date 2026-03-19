"""
Articles Scraper for Minneapolis Review

Generated at: 2026-03-19 15:54:30
Target URL: https://minneapolisreview.com/
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://minneapolisreview.com/'

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
        - date: Publication date in YYYY-MM-DD format (optional — None if not found)
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Find all article containers using the reliable block class observed in examples.
    containers = await page.query_selector_all('.ultp-block-item')
    for container in containers:
        try:
            # Title and URL: prefer the anchor inside .ultp-block-title
            title_anchor = await container.query_selector('.ultp-block-title a')
            title = None
            url = None
            if title_anchor:
                # text_content() returns the DOM text regardless of visibility
                title_text = await title_anchor.text_content()
                if title_text:
                    title = title_text.strip()
                href = await title_anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

            # Date: try several reasonable locations, fall back to None
            date_value = None
            # 1) time element inside container
            time_el = await container.query_selector('time')
            if time_el:
                time_text = await time_el.get_attribute('datetime') or await time_el.text_content()
                if time_text:
                    try:
                        dt = parse(time_text.strip(), fuzzy=True)
                        date_value = dt.date().isoformat()
                    except Exception:
                        date_value = None
            # 2) metadata container text (e.g., .ultp-block-meta) which might contain a date string
            if date_value is None:
                meta_el = await container.query_selector('.ultp-block-meta')
                if meta_el:
                    meta_text = await meta_el.text_content()
                    if meta_text:
                        try:
                            dt = parse(meta_text.strip(), fuzzy=True)
                            date_value = dt.date().isoformat()
                        except Exception:
                            date_value = None
            # 3) generic selectors that sometimes contain dates
            if date_value is None:
                alt_date_el = await container.query_selector('.post-date, .entry-date, .posted-on')
                if alt_date_el:
                    alt_text = await alt_date_el.get_attribute('datetime') or await alt_date_el.text_content()
                    if alt_text:
                        try:
                            dt = parse(alt_text.strip(), fuzzy=True)
                            date_value = dt.date().isoformat()
                        except Exception:
                            date_value = None

            # Ensure required fields; skip if title or url missing
            if not title and not url:
                # Nothing usable found for this container; skip safely
                continue

            item = {
                'title': title or None,
                'date': date_value,
                'url': url or None,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)

        except Exception:
            # Protect against individual item parsing failures; continue with others
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
    # Strategy:
    # 1) Try to find a conventional next-page anchor (rel="next", .next, pagination-next)
    # 2) Try to find a "load more" button (anchor or button)
    # 3) If none found, perform an infinite-scroll style attempt: scroll and wait for new items to load

    try:
        # Attempt 1: rel="next" or common next/older selectors
        next_selectors = [
            'a[rel="next"]',
            'a.pagination-next',
            'a.next',
            'a.pager-next',
            'a.page-link.next',
            'a[aria-label*="next"]',
            'a[aria-label*="Next"]',
            'a:has-text("Next")',  # may not always work in query_selector; kept as fallback below
        ]
        next_el = None
        for sel in next_selectors:
            try:
                # Some selectors (like :has-text) may raise; wrap each attempt
                candidate = await page.query_selector(sel)
            except Exception:
                candidate = None
            if candidate:
                next_el = candidate
                break

        if next_el:
            href = await next_el.get_attribute('href')
            tag = await next_el.get_property('tagName')
            tag_name = (await tag.json_value()).lower() if tag else ''
            # If it's an anchor with href -> navigate
            if href:
                next_url = urllib.parse.urljoin(base_url, href.strip())
                try:
                    await page.goto(next_url)
                    # Wait briefly for content to load
                    await page.wait_for_load_state('networkidle', timeout=8000)
                    return
                except Exception:
                    # fallback to clicking if goto fails
                    try:
                        await next_el.click()
                        await page.wait_for_load_state('networkidle', timeout=8000)
                        return
                    except Exception:
                        pass
            else:
                # If it's a button or anchor without href, attempt to click
                try:
                    await next_el.scroll_into_view_if_needed()
                    await next_el.click()
                    # Wait for potential new content to load (either network or DOM change)
                    await page.wait_for_timeout(2000)
                    return
                except Exception:
                    pass

        # Attempt 2: look for common "load more" buttons
        load_more_selectors = [
            'button.load-more',
            'a.load-more',
            'button#load-more',
            '.load-more-button',
            'button:has-text("Load More")',
            'a:has-text("Load More")',
            'button:has-text("More")',
            'a:has-text("More")',
            'button:has-text("Show more")',
        ]
        load_el = None
        for sel in load_more_selectors:
            try:
                candidate = await page.query_selector(sel)
            except Exception:
                candidate = None
            if candidate:
                load_el = candidate
                break

        if load_el:
            try:
                await load_el.scroll_into_view_if_needed()
                await load_el.click()
                # wait for more items to appear
                await page.wait_for_timeout(2000)
                return
            except Exception:
                # If click fails, continue to infinite scroll fallback
                pass

        # Attempt 3: infinite scroll fallback
        # We'll perform a few scrolls and wait for new article containers to appear.
        prev_count = len(await page.query_selector_all('.ultp-block-item'))
        max_scroll_attempts = 5
        attempts = 0
        while attempts < max_scroll_attempts:
            attempts += 1
            # Scroll to bottom
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # Wait a bit for lazy-loaded content
            await page.wait_for_timeout(2000 + attempts * 500)
            # Optionally trigger another small scroll to ensure loading triggers
            await page.evaluate("window.scrollBy(0, 400)")
            await page.wait_for_timeout(1000)
            new_count = len(await page.query_selector_all('.ultp-block-item'))
            if new_count > prev_count:
                # New items loaded; return to let caller process them
                return
        # If no new items after attempts, do a final short pause then return (no further navigation)
        await page.wait_for_timeout(1000)
        return

    except Exception:
        # On any unexpected error, perform a safe short wait and return (prevents breaking the main loop)
        try:
            await page.wait_for_timeout(1000)
        except Exception:
            pass
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