import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio
import time

base_url = 'https://www.mpschools.org/about-mps/news'

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

    # Use article containers under the list items. This selector targets article elements
    # that contain the post title, date, and link.
    article_selector = '.fsListItems article.fsStyleAutoclear'
    article_elements = await page.query_selector_all(article_selector)

    # Fallback: if the article elements aren't found, attempt to find anchors with .fsPostLink
    if not article_elements:
        anchors = await page.query_selector_all('.fsListItems a.fsPostLink[href]')
        for a in anchors:
            try:
                href = await a.get_attribute('href')
                url = urllib.parse.urljoin(base_url, href) if href else None
                title_text = await a.text_content() or ''
                title = title_text.strip() or None
                # Try to find a sibling time element (may not be available here)
                parent = await a.evaluate_handle("node => node.parentElement")
                date_val = None
                try:
                    time_elem = await parent.as_element().query_selector('time.fsDate')
                    if time_elem:
                        dt = await time_elem.get_attribute('datetime')
                        if dt:
                            date_val = parse(dt).date().isoformat()
                        else:
                            txt = await time_elem.text_content()
                            if txt:
                                date_val = parse(txt).date().isoformat()
                except Exception:
                    date_val = None

                if title and url:
                    items.append({
                        'title': title,
                        'date': date_val,
                        'url': url,
                        'scraper': SCRAPER_MODULE_PATH,
                    })
            except Exception:
                continue
        return items

    for art in article_elements:
        try:
            # Title: prefer .fsTitle a.fsPostLink, fall back to any a.fsPostLink inside article
            title = None
            title_elem = await art.query_selector('.fsTitle a.fsPostLink')
            if not title_elem:
                title_elem = await art.query_selector('a.fsPostLink')
            if title_elem:
                raw_title = await title_elem.text_content()
                title = raw_title.strip() if raw_title else None

            # URL: first anchor with class fsPostLink and an href
            url = None
            link_elem = await art.query_selector('a.fsPostLink[href]')
            if link_elem:
                href = await link_elem.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href)

            # Date: look for <time class="fsDate"> and use the datetime attribute if present
            date_val = None
            time_elem = await art.query_selector('time.fsDate')
            if time_elem:
                dt = await time_elem.get_attribute('datetime')
                if dt:
                    try:
                        date_val = parse(dt).date().isoformat()
                    except Exception:
                        date_val = None
                else:
                    # fallback to parsing visible text
                    txt = await time_elem.text_content()
                    if txt:
                        try:
                            date_val = parse(txt).date().isoformat()
                        except Exception:
                            date_val = None

            # Only include items that have at least a title and url (required fields)
            if title and url:
                items.append({
                    'title': title,
                    'date': date_val,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                })
        except Exception:
            # Skip malformed article entries rather than failing the whole page
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
    # Preferred pagination: "Load More" button
    load_more_selector = 'button.fsLoadMoreButton'
    load_more = await page.query_selector(load_more_selector)
    if load_more:
        try:
            # Count current number of articles to detect progress
            article_selector = '.fsListItems article.fsStyleAutoclear'
            current_articles = await page.query_selector_all(article_selector)
            current_count = len(current_articles)

            # Ensure button is visible/clickable
            try:
                await load_more.scroll_into_view_if_needed()
            except Exception:
                pass

            # Click the button to load more items
            try:
                await load_more.click()
            except Exception:
                # Fallback to invoking click via JS if normal click fails
                await page.evaluate("el => el.click()", load_more)

            # Wait for new content to be appended (poll for a short timeout)
            max_wait = 10  # seconds
            poll_interval = 0.5
            elapsed = 0.0
            while elapsed < max_wait:
                await page.wait_for_timeout(int(poll_interval * 1000))
                new_articles = await page.query_selector_all(article_selector)
                if len(new_articles) > current_count:
                    # New items loaded
                    return
                elapsed += poll_interval
            # If no new items after timeout, just return (fallback behavior)
            return
        except Exception:
            # If any error occurs interacting with the button, fall back to infinite scroll below
            pass

    # No explicit pagination found or clicking failed: fallback to infinite scroll
    try:
        # Scroll down incrementally to trigger lazy loading
        prev_height = await page.evaluate("() => document.body.scrollHeight")
        # Perform a few scrolls (this function is called repeatedly by get_all_articles loop)
        scroll_rounds = 3
        for _ in range(scroll_rounds):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)  # allow content to load
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == prev_height:
                # No more content loaded
                break
            prev_height = new_height
        return
    except Exception:
        # As a last resort, do a simple wait to give time for any auto-loading content
        await page.wait_for_timeout(3000)
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
                    # Create a stable key for de-duplication; ignore None values in key
                    key = tuple(sorted((k, v) for k, v in item.items() if v is not None))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                if new_item_count <= item_count:
                    # No new items were added on this iteration => stop
                    break

                page_count += 1
                item_count = new_item_count

                # Attempt to advance to next page / load more items
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