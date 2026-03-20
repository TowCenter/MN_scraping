"""
Articles Scraper for The Guardian Minneapolis

Generated at: 2026-03-20 13:26:24
Target URL: https://www.theguardian.com/us-news/minneapolis
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

base_url = 'https://www.theguardian.com/us-news/minneapolis'

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
        - date: Publication date in YYYY-MM-DD format (optional — use None if not found)
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Find article list items. Use a reasonably stable container selector from examples.
    article_nodes = await page.query_selector_all('ul.dcr-1ydzu3d > li')

    for li in article_nodes:
        # Title extraction: primary selector is the headline span; fallback to h3.card-headline or link aria-label.
        title = None
        try:
            title_el = await li.query_selector('.show-underline.headline-text')
            if title_el:
                raw = await title_el.text_content()
                if raw:
                    title = raw.strip()
        except Exception:
            title = None

        if not title:
            try:
                h3_el = await li.query_selector('h3.card-headline')
                if h3_el:
                    raw = await h3_el.text_content()
                    if raw:
                        title = raw.strip()
            except Exception:
                title = None

        if not title:
            try:
                link_for_label = await li.query_selector('a[aria-label]')
                if link_for_label:
                    aria = await link_for_label.get_attribute('aria-label')
                    if aria:
                        title = aria.strip()
            except Exception:
                title = None

        # URL extraction: prefer article anchor inside the item (image/title link).
        url = None
        try:
            # anchor used for cards (may be empty text but has href)
            anchor = await li.query_selector('a.dcr-2yd10d[href], a[aria-label][href], a[href*="/us-news/"]')
            if anchor:
                href = await anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href)
        except Exception:
            url = None

        # Date extraction: look for any <time datetime="..."> inside the item and parse its datetime attribute.
        date = None
        try:
            time_el = await li.query_selector('time[datetime], footer time[datetime]')
            if time_el:
                datetime_attr = await time_el.get_attribute('datetime')
                if datetime_attr:
                    # parse and normalize to YYYY-MM-DD
                    try:
                        dt = parse(datetime_attr)
                        date = dt.date().isoformat()
                    except Exception:
                        # final fallback: try to parse visible text content
                        txt = await time_el.text_content()
                        if txt:
                            try:
                                dt = parse(txt, fuzzy=True)
                                date = dt.date().isoformat()
                            except Exception:
                                date = None
        except Exception:
            date = None

        # Ensure required keys and stable structure; skip items missing title or url.
        if not title and not url:
            # nothing usable found for this node
            continue

        item = {
            'title': title if title else None,
            'date': date if date else None,
            'url': url if url else None,
            'scraper': SCRAPER_MODULE_PATH,
        }
        items.append(item)

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """

    # Strategy (robust):
    # 1. Collect all anchors with ?page= in href and choose the one with smallest page number > current.
    # 2. Try common next selectors (arrow class, numeric link class) if above didn't find a candidate.
    # 3. Attempt click with wait_for_navigation; if that fails, use goto with resolved href.
    # 4. Fallback to infinite scroll.

    try:
        initial_url = page.url
        parsed = urllib.parse.urlparse(initial_url)
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            current_page_num = int(qs.get('page', ['1'])[0])
        except Exception:
            current_page_num = 1

        # 1) Find all anchors with ?page= and pick smallest page > current_page_num
        anchors = await page.query_selector_all('a[href*="?page="]')
        candidate = None
        candidate_num = None
        candidate_href = None

        for a in anchors:
            try:
                href = await a.get_attribute('href')
                if not href:
                    continue
                full = urllib.parse.urljoin(initial_url, href)
                parsed_href = urllib.parse.urlparse(full)
                qs_href = urllib.parse.parse_qs(parsed_href.query)
                if 'page' not in qs_href:
                    continue
                try:
                    num = int(qs_href.get('page', [None])[0])
                except Exception:
                    continue
                if num and num > current_page_num:
                    if candidate_num is None or num < candidate_num:
                        candidate = a
                        candidate_num = num
                        candidate_href = full
            except Exception:
                continue

        if candidate and candidate_href:
            # Prefer navigation via goto (more reliable). Use goto with wait_for_load_state.
            try:
                await page.goto(candidate_href)
                await page.wait_for_load_state('networkidle')
                return
            except Exception:
                # If goto fails, attempt click with navigation waiting
                try:
                    await candidate.scroll_into_view_if_needed()
                    wait_promise = page.wait_for_navigation(wait_until='networkidle', timeout=10000)
                    await candidate.click()
                    await wait_promise
                    return
                except Exception:
                    # fall through to try other selectors or infinite scroll
                    pass

        # 2) Try specific next-arrow or numeric class selectors (fallback)
        next_selectors = ['a.dcr-jh1m5g', 'a.dcr-1nzqxjn', 'a[rel="next"]']
        for sel in next_selectors:
            try:
                el = await page.query_selector(sel)
                if not el:
                    continue
                href = await el.get_attribute('href')
                if href:
                    next_url = urllib.parse.urljoin(initial_url, href)
                    try:
                        await page.goto(next_url)
                        await page.wait_for_load_state('networkidle')
                        return
                    except Exception:
                        # try click approach
                        try:
                            await el.scroll_into_view_if_needed()
                            wait_promise = page.wait_for_navigation(wait_until='networkidle', timeout=10000)
                            await el.click()
                            await wait_promise
                            return
                        except Exception:
                            continue
                else:
                    # no href: attempt click with navigation waiting, or fallback to waiting for URL change
                    try:
                        await el.scroll_into_view_if_needed()
                        wait_promise = page.wait_for_navigation(wait_until='networkidle', timeout=10000)
                        await el.click()
                        await wait_promise
                        return
                    except Exception:
                        # try click and wait for URL change manually
                        try:
                            await el.click()
                            # wait up to 8s for URL to change
                            for _ in range(8):
                                await asyncio.sleep(1)
                                if page.url != initial_url:
                                    await page.wait_for_load_state('networkidle')
                                    return
                        except Exception:
                            continue
            except Exception:
                continue

    except Exception:
        # if any unexpected error occurs, continue to infinite scroll fallback
        pass

    # 3) Infinite scroll fallback: scroll to bottom and wait for content to load.
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Give site time to load additional content (or load more button to appear)
        await page.wait_for_timeout(3000)
        # Some sites require an additional short scroll to trigger lazy load
        await page.evaluate("window.scrollBy(0, 500)")
        await page.wait_for_timeout(1500)
    except Exception:
        # If scrolling fails for any reason, just wait a moment
        await page.wait_for_timeout(2000)

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
                    # Create dedupe key by url and title (both may be present). Use tuple to ensure hashable.
                    dedupe_key = (item.get('url'), item.get('title'))
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