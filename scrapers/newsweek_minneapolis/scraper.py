"""
Articles Scraper for Newsweek Minneapolis

Generated at: 2026-03-20 14:34:36
Target URL: https://search.newsweek.com/?q=minneapolis&sort=date
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

base_url = 'https://search.newsweek.com/?q=minneapolis&sort=date'

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

async def _extract_date_from_container(container):
    """
    Helper to extract date string from a container element handle.
    Tries multiple common patterns (time[datetime], meta tags, spans with date/time in class).
    Returns parsed ISO date string YYYY-MM-DD or None.
    """
    try:
        # Try <time datetime="">
        time_el = await container.query_selector('time')
        date_str = None
        if time_el:
            date_str = await time_el.get_attribute('datetime') or await time_el.text_content()
        if not date_str:
            # meta[itemprop="datePublished"] or meta[name="date"]
            meta = await container.query_selector('meta[itemprop="datePublished"], meta[name="date"], meta[property="article:published_time"]')
            if meta:
                date_str = await meta.get_attribute('content')
        if not date_str:
            # common date-like spans or divs
            candidate = await container.query_selector('span[class*="date"], span[class*="time"], div[class*="date"], div[class*="time"], p[class*="date"]')
            if candidate:
                date_str = await candidate.text_content()
        if not date_str:
            # fallback: any element with attribute datetime inside container
            any_dt = await container.query_selector('[datetime]')
            if any_dt:
                date_str = await any_dt.get_attribute('datetime') or await any_dt.text_content()
        if date_str:
            # Normalize and parse
            try:
                parsed = parse(date_str.strip(), fuzzy=True)
                return parsed.date().isoformat()
            except Exception:
                return None
    except Exception:
        return None
    return None

async def scrape_page(page):
    """
    Extract article data from the current page.

    Parameters:
        page: Playwright page object

    Returns:
        List of dictionaries containing article data with keys:
        - title: Headline or title of the article
        - date: Publication date in YYYY-MM-DD format
        - url: Link to the full article
        - scraper: module path for traceability
    """

    items = []

    # Prefer anchor elements that represent article links on Newsweek search/list pages.
    # Based on observed HTML examples, a.NewsweekLink_link__BTn_o is a reliable article link selector.
    try:
        anchors = await page.query_selector_all('a.NewsweekLink_link__BTn_o')
    except Exception:
        anchors = []

    for a in anchors:
        try:
            # Title (use text_content to capture DOM text even if hidden)
            raw_title = await a.text_content()
            title = raw_title.strip() if raw_title else None

            # URL (may be relative)
            href = await a.get_attribute('href')
            if not href:
                # skip entries without href since url is required
                continue
            # Resolve to absolute URL using current page URL as base
            url = urllib.parse.urljoin(page.url, href)

            # Attempt to find a container for date extraction (closest article ancestor or parent element)
            container_handle = await a.evaluate_handle("el => el.closest('article') || el.parentElement || el")
            container = container_handle.as_element() if container_handle else None

            date = None
            if container:
                date = await _extract_date_from_container(container)

            items.append({
                'title': title,
                'date': date,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })
        except Exception:
            # Skip malformed entries but continue processing others
            continue

    # Additionally, there are trending story spans that are not anchors (no URL).
    # We skip any items that don't have a URL because url is required per spec.

    # Deduplicate by URL + title
    seen = set()
    unique_items = []
    for it in items:
        key = (it.get('url'), it.get('title'))
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(it)

    return unique_items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """

    # Try common "next page" link selectors first
    try:
        next_link = await page.query_selector('a[rel="next"], a.pagination__next, a[aria-label="Next"], a.next, a[role="button"].next')
        if next_link:
            href = await next_link.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(page.url, href)
                try:
                    await page.goto(next_url)
                    await page.wait_for_load_state('networkidle')
                    return
                except Exception:
                    # If direct navigation fails, try clicking
                    try:
                        await next_link.click()
                        await page.wait_for_load_state('networkidle')
                        return
                    except Exception:
                        pass
    except Exception:
        pass

    # Try "Load more" buttons (case-insensitive)
    try:
        load_more_locator = page.locator('button', has_text='Load more')
        if await load_more_locator.count() == 0:
            load_more_locator = page.locator('button', has_text='Load More')
        if await load_more_locator.count() > 0:
            try:
                await load_more_locator.first.scroll_into_view_if_needed()
                await load_more_locator.first.click()
                # wait a bit for new content to load
                await page.wait_for_timeout(1500)
                return
            except Exception:
                pass
    except Exception:
        pass

    # No explicit pagination found — fallback to infinite scroll.
    # Scroll multiple times until document height stops increasing or until a few iterations.
    try:
        max_scroll_attempts = 5
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        for _ in range(max_scroll_attempts):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # give network / lazy load some time
            await page.wait_for_timeout(2000)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == previous_height:
                # small additional wait to ensure lazy loads have chance
                await page.wait_for_timeout(1000)
                new_height = await page.evaluate("() => document.body.scrollHeight")
                if new_height == previous_height:
                    break
            previous_height = new_height
    except Exception:
        # Ensure we don't raise from pagination fallback
        await page.wait_for_timeout(1000)
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