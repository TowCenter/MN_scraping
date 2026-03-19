import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://westtwincities.com/category/local-government/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch a headless Chromium browser
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
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Select article containers
    article_handles = await page.query_selector_all('article.ultp-block-item, .ultp-block-item')
    for article in article_handles:
        try:
            # Title anchor (preferred selector)
            title_anchor = await article.query_selector('.ultp-block-title a')
            if not title_anchor:
                # fallback to any headline link inside the article
                title_anchor = await article.query_selector('h3.ultp-block-title a, a[href].ultp-block-title')

            title_text = None
            url = None
            if title_anchor:
                raw_title = await title_anchor.text_content() or ''
                title_text = raw_title.strip() or None
                href = await title_anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(page.url, href)

            # If title or url missing, try to find first link inside article for url
            if not url:
                first_link = await article.query_selector('a[href]')
                if first_link:
                    href = await first_link.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin(page.url, href)

            # Date extraction
            date_value = None
            date_elem = await article.query_selector('.ultp-block-date, span.ultp-block-date')
            if date_elem:
                raw_date = (await date_elem.text_content() or '').strip()
                if raw_date:
                    try:
                        dt = parse(raw_date, fuzzy=True)
                        date_value = dt.strftime('%Y-%m-%d')
                    except Exception:
                        date_value = None

            # Skip items without required fields
            if not title_text or not url:
                continue

            items.append({
                'title': title_text,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # skip malformed article entries without failing the whole page
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
    try:
        # Gather candidate pagination anchors (ordered as they appear)
        candidates = await page.query_selector_all(
            '.ultp-next-page-numbers a, .ultp-pagination a, a[rel="next"], nav a'
        )

        next_href = None
        next_element = None
        current_url = page.url

        for el in candidates:
            try:
                href = (await el.get_attribute('href') or '').strip()
                if not href:
                    continue

                text = (await el.text_content() or '').strip().lower()
                aria = (await el.get_attribute('aria-label') or '').strip().lower()
                rel = (await el.get_attribute('rel') or '').strip().lower()

                # Skip links that are explicitly previous
                if 'prev' in text or 'previous' in text or 'prev' in aria or 'previous' in aria:
                    continue

                # Prefer explicit rel="next"
                if rel == 'next' or 'next' in text or 'next' in aria:
                    next_href = href
                    next_element = el
                    break

                # Fallback: links looking like page navigation
                if '/page/' in href or 'page=' in href:
                    next_href = href
                    next_element = el
                    break

            except Exception:
                continue

        if next_href:
            next_url = urllib.parse.urljoin(page.url, next_href)

            # Avoid navigating to the same URL repeatedly
            if next_url == current_url:
                # If the anchor points to the same page, don't attempt; fallback below
                next_href = None
            else:
                # Navigate cleanly to the next page and wait for network idle if possible
                try:
                    await page.goto(next_url, wait_until='networkidle', timeout=15000)
                except Exception:
                    # fallback to a gentler wait pattern
                    try:
                        await page.goto(next_url)
                        await page.wait_for_load_state('domcontentloaded', timeout=8000)
                    except Exception:
                        await page.wait_for_timeout(1000)
                return

        # If no explicit next link found or it pointed to same page, look for a "Load more" button
        load_more_selectors = [
            'button:has-text("Load more")',
            'a:has-text("Load more")',
            'button.load-more',
            'a.load-more',
            'button[aria-label*="load" i]',
            'button[aria-label*="Load" i]',
        ]
        for sel in load_more_selectors:
            try:
                btn = await page.query_selector(sel)
                if not btn:
                    continue

                # Count current number of article items to detect new content after click
                prev_items = await page.query_selector_all('article.ultp-block-item, .ultp-block-item')
                prev_count = len(prev_items)

                await btn.scroll_into_view_if_needed()
                await btn.click()

                # Wait for new items to appear (short polling)
                for _ in range(12):
                    await page.wait_for_timeout(500)
                    new_count = len(await page.query_selector_all('article.ultp-block-item, .ultp-block-item'))
                    if new_count > prev_count:
                        return
                # if no new items detected, continue to other fallbacks
                break
            except Exception:
                continue

    except Exception:
        # swallow errors and fallback to infinite scroll
        pass

    # Infinite scroll fallback
    try:
        previous_height = await page.evaluate("() => document.body.scrollHeight")
    except Exception:
        previous_height = None

    # try up to a few scroll iterations to load more content
    for _ in range(4):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if previous_height is not None and new_height == previous_height:
                # no more content loaded
                break
            previous_height = new_height
        except Exception:
            break

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
                    # create a dedupe key that is stable
                    key = (item.get('url'), item.get('title'), item.get('date'))
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