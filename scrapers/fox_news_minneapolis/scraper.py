import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API (best-effort compatibility handled)
from dateutil.parser import parse
import urllib.parse
import asyncio
from datetime import datetime, timedelta
import re
import traceback

base_url = 'https://www.foxnews.com/search-results/search#q=minneapolis'

# Scraper module path for tracking the source of scraped data
try:
    SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])
except Exception:
    SCRAPER_MODULE_PATH = 'unknown.scraper'

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch chromium headless and disable sandbox for typical CI environments
        self.browser = await self.playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            await self.playwright.stop()
        except Exception:
            pass

async def _apply_stealth_safe(page):
    """
    Apply stealth measures to the page if the playwright_stealth API is available.
    This helper tries several likely method names to maximize compatibility across versions.
    """
    try:
        stealth = Stealth()
    except Exception:
        return
    # Try async method names first
    if hasattr(stealth, 'apply_stealth_async'):
        try:
            await stealth.apply_stealth_async(page)
            return
        except Exception:
            pass
    if hasattr(stealth, 'apply_async'):
        try:
            await stealth.apply_async(page)
            return
        except Exception:
            pass
    # Try sync apply methods (some versions might be synchronous)
    if hasattr(stealth, 'apply_stealth'):
        try:
            stealth.apply_stealth(page)
            return
        except Exception:
            pass
    if hasattr(stealth, 'apply'):
        try:
            stealth.apply(page)
            return
        except Exception:
            pass
    # If none worked, just continue without stealth
    return

def _normalize_date_text_to_iso(date_text: str):
    """
    Convert human-friendly relative dates (e.g., "4 days ago", "yesterday") or
    absolute dates into YYYY-MM-DD string. Returns None if unable to parse.
    """
    if not date_text:
        return None

    txt = date_text.strip().lower()

    # Handle common relative times like "4 days ago", "2 hours ago", "yesterday", "today"
    m = re.search(r'(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago', txt)
    if m:
        qty = int(m.group(1))
        unit = m.group(2)
        if unit == 'second':
            delta = timedelta(seconds=qty)
        elif unit == 'minute':
            delta = timedelta(minutes=qty)
        elif unit == 'hour':
            delta = timedelta(hours=qty)
        elif unit == 'day':
            delta = timedelta(days=qty)
        elif unit == 'week':
            delta = timedelta(weeks=qty)
        elif unit == 'month':
            delta = timedelta(days=30 * qty)
        elif unit == 'year':
            delta = timedelta(days=365 * qty)
        else:
            delta = timedelta(0)
        dt = datetime.utcnow() - delta
        return dt.strftime('%Y-%m-%d')

    if 'yesterday' in txt:
        dt = datetime.utcnow() - timedelta(days=1)
        return dt.strftime('%Y-%m-%d')

    if 'today' in txt or 'just now' in txt:
        dt = datetime.utcnow()
        return dt.strftime('%Y-%m-%d')

    # Remove extraneous words like "Updated" or "Published"
    cleaned = re.sub(r'\b(updated|published|posted)\b[:\s]*', '', date_text, flags=re.I).strip()

    # Try using dateutil parser for absolute dates
    try:
        dt = parse(cleaned, fuzzy=True)
        return dt.strftime('%Y-%m-%d')
    except Exception:
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

    # Use broad, resilient selectors for article containers
    article_selectors = 'article.article, .collection-search .article'

    article_elements = await page.query_selector_all(article_selectors)

    for art in article_elements:
        try:
            # Title: prefer h2.title a, fallback to h2.title
            title_el = await art.query_selector('h2.title a, h2.title')
            title = None
            if title_el:
                raw_title = await title_el.text_content()
                if raw_title:
                    title = raw_title.strip()

            # URL: prefer href on h2.title a, fallback to first anchor inside article
            url = None
            href_el = await art.query_selector('h2.title a')
            if not href_el:
                href_el = await art.query_selector('a')
            if href_el:
                href = await href_el.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

            # Date: look for .meta .time or span.time
            date_el = await art.query_selector('.meta .time, span.time, .time, time')
            date_value = None
            if date_el:
                raw_date = await date_el.text_content()
                if raw_date:
                    date_value = _normalize_date_text_to_iso(raw_date.strip())

            items.append({
                'title': title if title else None,
                'date': date_value,
                'url': url if url else None,
                'scraper': SCRAPER_MODULE_PATH,
            })
        except Exception:
            # Robust: skip malformed item rather than halting the scraper
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
    # Candidate 'Load More' selectors
    load_more_selector = 'div.button.load-more a, div.button.load-more, a.load-more, button.load-more, .load-more'

    btn = await page.query_selector(load_more_selector)

    # Function to count current article nodes
    async def _count_articles():
        nodes = await page.query_selector_all('article.article, .collection-search .article')
        return len(nodes)

    if btn:
        try:
            before = await _count_articles()
            # Bring the button into view and attempt to click
            try:
                await btn.scroll_into_view_if_needed()
            except Exception:
                pass
            await asyncio.sleep(0.3)
            try:
                await btn.click()
            except Exception:
                # If clicking fails, try to navigate using href attribute if present
                href = await btn.get_attribute('href')
                if href:
                    try:
                        await page.goto(urllib.parse.urljoin(base_url, href))
                    except Exception:
                        pass
                else:
                    # As a last resort, evaluate a click in the page context using a safe selector (first matching)
                    try:
                        selector_for_eval = None
                        # Attempt to find a simple selector without commas
                        for sel in load_more_selector.split(','):
                            s = sel.strip()
                            if s:
                                selector_for_eval = s
                                break
                        if selector_for_eval:
                            await page.evaluate(
                                """(sel) => {
                                    const el = document.querySelector(sel);
                                    if (el) { el.scrollIntoView(); el.click(); return true; }
                                    return false;
                                }""",
                                selector_for_eval
                            )
                    except Exception:
                        pass

            # Wait for new content to load (up to ~12 seconds)
            for _ in range(12):
                await asyncio.sleep(1)
                after = await _count_articles()
                if after > before:
                    return
            # If no new items detected, give a short final wait and return
            await asyncio.sleep(1)
            return
        except Exception:
            # Fallback to infinite scroll if clicking fails unexpectedly
            pass

    # Fallback: infinite scroll behavior
    # Perform a few scrolls to trigger lazy loading
    for i in range(6):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5 + i * 0.5)

    return

async def get_first_page(base_url=base_url):
    """Fetch only the first page of articles."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
        await _apply_stealth_safe(page)
        try:
            await page.goto(base_url, timeout=30000)
        except Exception:
            try:
                await page.goto(base_url)
            except Exception:
                pass
        items = await scrape_page(page)
        try:
            await page.close()
        except Exception:
            pass
        return items

async def get_all_articles(base_url=base_url, max_pages=100):
    """Fetch all articles from all pages."""
    async with PlaywrightContext() as context:
        items = []
        seen = set()
        page = await context.new_page()
        await _apply_stealth_safe(page)

        page_count = 0
        item_count = 0  # previous count

        try:
            try:
                await page.goto(base_url, timeout=30000)
            except Exception:
                try:
                    await page.goto(base_url)
                except Exception:
                    pass

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

        except Exception:
            traceback.print_exc()

        try:
            await page.close()
        except Exception:
            pass
        return items

async def main():
    """Main execution function."""
    all_items = await get_all_articles()

    # Save results to JSON
    try:
        result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    except Exception:
        result_path = os.path.join(os.getcwd(), 'result.json')
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {result_path}")

if __name__ == "__main__":
    asyncio.run(main())