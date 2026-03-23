import json
import os
import urllib.parse
import asyncio
from dateutil.parser import parse
from playwright.async_api import async_playwright

base_url = 'https://www.axios.com/local/twin-cities/news'

# Scraper module path for tracking the source of scraped data
try:
    SCRAPER_MODULE_PATH = '.'.join(
        os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:]
    )
except Exception:
    SCRAPER_MODULE_PATH = 'scrapers.axios.twin_cities'

# Operator user-agent (set in operator.json). Provide a sensible default to reduce detection.
USER_AGENT = ''

if not USER_AGENT:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    )


class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch headless to avoid requiring a display in test environments
        self.browser = await self.playwright.chromium.launch(headless=True)
        context_kwargs = {'user_agent': USER_AGENT}
        # Create a context with the desired UA; keep other defaults
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self.context.close()
        except Exception:
            pass
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            await self.playwright.stop()
        except Exception:
            pass


async def scrape_page(page):
    """
    Extract article data from the current page.

    Returns list of dicts with keys: title, date, url, scraper
    """
    items = []

    try:
        anchors = await page.query_selector_all(
            'a[data-cy="story-promo-headline"], a.group[data-cy="story-promo-headline"]'
        )
    except Exception:
        anchors = []

    for a in anchors:
        try:
            href = await a.get_attribute('href')
            if not href:
                continue
            url = urllib.parse.urljoin(base_url, href.strip())

            title = None
            title_el = await a.query_selector('h2 span.h-editorial-040, span.h-editorial-040, h2')
            if title_el:
                raw_title = await title_el.text_content()
                if raw_title:
                    title = raw_title.strip()
            if not title:
                raw_title = await a.text_content()
                title = raw_title.strip() if raw_title else None

            if not title:
                continue

            date_value = None
            # Look for timestamp inside anchor
            date_el = await a.query_selector('p[data-cy="timestamp"], time, .label-utility-020-thin')
            if not date_el:
                # check parent element for timestamp
                try:
                    parent_handle = await a.evaluate_handle("el => el.parentElement")
                    parent_el = parent_handle.as_element() if parent_handle else None
                    if parent_el:
                        date_el = await parent_el.query_selector(
                            'p[data-cy="timestamp"], time, .label-utility-020-thin'
                        )
                except Exception:
                    date_el = None

            if date_el:
                raw_date = await date_el.text_content()
                if raw_date:
                    raw_date = raw_date.strip()
                    try:
                        parsed = parse(raw_date, fuzzy=True)
                        if parsed.year and parsed.year > 1900:
                            date_value = parsed.date().isoformat()
                    except Exception:
                        date_value = None

            items.append({
                'title': title,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            continue

    return items


async def advance_page(page):
    """
    Attempts to advance to the next page of results. Tries explicit pagination controls first,
    then falls back to a scroll-to-bottom attempt.
    """
    keywords = ['load more', 'show more', 'see more', 'more', 'next', 'older', 'older posts', 'view more']

    try:
        candidates = await page.query_selector_all('button, a')
        for el in candidates:
            try:
                if not await el.is_visible():
                    continue
                text = await el.text_content()
                if not text:
                    continue
                text_l = text.strip().lower()
                match = any(k in text_l for k in keywords)
                if not match:
                    data_cy = await el.get_attribute('data-cy')
                    if data_cy and ('more' in data_cy or 'load' in data_cy or 'next' in data_cy):
                        match = True
                if not match:
                    continue

                tag = await el.evaluate("(e) => e.tagName.toLowerCase()")
                if tag == 'a':
                    href = await el.get_attribute('href')
                    if href:
                        next_url = urllib.parse.urljoin(base_url, href.strip())
                        try:
                            await page.goto(next_url, wait_until='networkidle', timeout=15000)
                        except Exception:
                            try:
                                await el.scroll_into_view_if_needed()
                                await el.click()
                                await page.wait_for_load_state('networkidle', timeout=10000)
                            except Exception:
                                pass
                        return
                else:
                    try:
                        await el.scroll_into_view_if_needed()
                        await el.click()
                        await page.wait_for_load_state('networkidle', timeout=10000)
                        await page.wait_for_timeout(1500)
                        return
                    except Exception:
                        try:
                            await asyncio.gather(page.wait_for_navigation(timeout=10000), el.click())
                            return
                        except Exception:
                            continue
            except Exception:
                continue
    except Exception:
        pass

    # Fallback: infinite scroll attempt
    try:
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        new_height = await page.evaluate("() => document.body.scrollHeight")
        if new_height == previous_height:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
    except Exception:
        await page.wait_for_timeout(2000)
    return


async def get_first_page(base_url=base_url):
    """Fetch only the first page of articles."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
        await page.goto(base_url, wait_until='networkidle', timeout=15000)
        items = await scrape_page(page)
        await page.close()
        return items


async def get_all_articles(base_url=base_url, max_pages=100):
    """Fetch all articles from paginated/list pages."""
    async with PlaywrightContext() as context:
        items = []
        seen = set()
        page = await context.new_page()
        page_count = 0
        item_count = 0
        new_item_count = 0

        await page.goto(base_url, wait_until='networkidle', timeout=15000)

        try:
            while page_count < max_pages:
                page_items = await scrape_page(page)
                for item in page_items:
                    # dedupe using URL (preferred) and title fallback
                    key = item.get('url') or item.get('title')
                    if not key:
                        continue
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
            # Print a concise error for debugging without raising
            print(f"Error occurred while paginating: {e}")

        try:
            await page.close()
        except Exception:
            pass
        return items


async def main():
    all_items = await get_all_articles()

    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    try:
        with open(result_path, 'w', encoding='utf-8') as f:
            json.dump(all_items, f, indent=2, ensure_ascii=False)
        print(f"Results saved to {result_path}")
    except Exception as e:
        print(f"Failed to write results: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        # If an event loop is already running (e.g., in certain test harnesses), create a task instead
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(main())
        else:
            loop.run_until_complete(main())