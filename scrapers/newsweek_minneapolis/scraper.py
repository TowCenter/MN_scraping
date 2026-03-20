"""
Articles Scraper for Newsweek Minneapolis

Generated at: 2026-03-20 13:23:59
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

    # Primary selector for article link/title as found in the page examples
    link_selector = 'a.NewsweekLink_link__BTn_o'

    # Find all matching anchors
    anchors = await page.query_selector_all(link_selector)

    for a in anchors:
        try:
            # Title - use text_content() per instructions (works even for hidden elements)
            raw_title = await a.text_content()
            title = raw_title.strip() if raw_title and raw_title.strip() else None

            # URL - resolve relative hrefs
            href = await a.get_attribute('href')
            url = None
            if href:
                url = urllib.parse.urljoin(base_url, href)

            # Attempt to find a nearby time/date element.
            # Look in closest article ancestor, parent element, or the element itself for common date selectors.
            date_raw = await a.evaluate("""el => {
                try {
                    const ancestor = el.closest('article') || el.parentElement || el;
                    // common selectors that might contain dates
                    const timeEl = ancestor.querySelector('time, .date, [data-testid*="date"], .ArticleHeader_date, .ArticleMeta_time, .PublishedDate, .timestamp');
                    if (!timeEl) return null;
                    // Prefer datetime attribute if present
                    const dt = timeEl.getAttribute && timeEl.getAttribute('datetime');
                    return (dt && dt.trim()) ? dt.trim() : (timeEl.textContent ? timeEl.textContent.trim() : null);
                } catch (e) {
                    return null;
                }
            }""")

            date_iso = None
            if date_raw:
                try:
                    # Parse date and normalize to YYYY-MM-DD
                    parsed = parse(date_raw, fuzzy=True)
                    date_iso = parsed.date().isoformat()
                except Exception:
                    date_iso = None

            # Only include items with required fields (title and url)
            if not title or not url:
                continue

            items.append({
                'title': title,
                'date': date_iso,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })
        except Exception:
            # Skip malformed item but continue processing others
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
    # Candidate selectors for "next" or "load more" behaviors
    NEXT_SELECTORS = [
        'a[rel="next"]',
        'a[aria-label="Next"]',
        'a.pagination__next',
        'a.next',
        'a[title="Next"]',
        'button[aria-label="Load more"]',
        'button.load-more',
        'button[id*="load"]',
        'button[title*="Load"]',
        'button[title*="More"]',
        'a[href*="page="]:has-text("Next")'
    ]

    try:
        # Try to find and use explicit next/load-more controls first
        for sel in NEXT_SELECTORS:
            el = await page.query_selector(sel)
            if not el:
                continue

            # If it's an anchor with href, navigate to that url
            href = await el.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(base_url, href)
                try:
                    await el.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    # Use goto to ensure full navigation when href is present
                    await page.goto(next_url)
                    await page.wait_for_load_state('networkidle', timeout=10000)
                except Exception:
                    # fallback: click and wait a bit
                    try:
                        await el.click()
                        await page.wait_for_timeout(2000)
                    except Exception:
                        pass
                return

            # Otherwise try clicking the element (button)
            try:
                await el.scroll_into_view_if_needed()
                await el.click()
                # Wait briefly for AJAX-loaded content
                await page.wait_for_timeout(2500)
                return
            except Exception:
                # If click fails, continue trying other selectors
                continue

    except Exception:
        # If any unexpected error occurs while trying selectors, fall back to infinite scroll
        pass

    # Fallback: infinite scroll strategy
    # We'll attempt a few scrolls and wait for new items to load.
    item_selector = 'a.NewsweekLink_link__BTn_o'
    try:
        previous_count = await page.eval_on_selector_all(item_selector, 'els => els.length')
    except Exception:
        previous_count = 0

    # Try scrolling multiple times to load more content (common for infinite scroll sites)
    max_attempts = 6
    for _ in range(max_attempts):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # Small pause to allow lazy loads / XHRs
            await page.wait_for_timeout(2000)
        except Exception:
            await page.wait_for_timeout(2000)

        try:
            current_count = await page.eval_on_selector_all(item_selector, 'els => els.length')
        except Exception:
            current_count = previous_count

        if current_count > previous_count:
            # New content loaded; return to let caller scrape new items
            return
        previous_count = current_count

    # If we reach here, no new content appeared after scrolling attempts.
    # As a last resort, attempt a short page.reload() to see if additional content becomes available.
    try:
        await page.reload()
        await page.wait_for_load_state('networkidle', timeout=8000)
    except Exception:
        # nothing else to do; exit and let caller decide to stop
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