"""
Articles Scraper for Breakthrough News

Generated at: 2026-03-20 15:00:55
Target URL: https://breakthroughnews.org/category/united-states/
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

base_url = 'https://breakthroughnews.org/category/united-states/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch headless browser; additional launch args can be added if needed
        self.browser = await self.playwright.chromium.launch(headless=False)
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

    # Use the top-level article container selector observed on the page.
    # Each article appears to be in a container with class "row postlist post_type_post".
    article_handles = await page.query_selector_all('.row.postlist.post_type_post')

    for handle in article_handles:
        try:
            # Initialize fields
            title = None
            url = None
            date_iso = None

            # Prefer anchor with class 'posttitle' (contains the h3 headline and href)
            a_posttitle = await handle.query_selector('a.posttitle')
            if a_posttitle:
                # Use text_content as required to get DOM text regardless of visibility
                raw_title = await a_posttitle.text_content()
                if raw_title:
                    title = raw_title.strip()
                href = await a_posttitle.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

            # Fallback: sometimes title might be only in a h3 without the anchor
            if not title:
                h3 = await handle.query_selector('h3')
                if h3:
                    raw_title = await h3.text_content()
                    if raw_title:
                        title = raw_title.strip()
                    # if h3 is inside an anchor, try to get parent anchor's href
                    parent = await h3.evaluate_handle("n => n.parentElement")
                    if parent:
                        try:
                            parent_tag = await parent.get_property('tagName')
                            tag_name = (await parent_tag.json_value()).lower()
                            if tag_name == 'a':
                                href = await parent.get_attribute('href')
                                if href:
                                    url = urllib.parse.urljoin(base_url, href.strip())
                        except Exception:
                            pass

            # Date: try common selector span.postdate inside the article container
            span_date = await handle.query_selector('span.postdate')
            if span_date:
                raw_date = await span_date.text_content()
                if raw_date:
                    raw_date = raw_date.strip()
                    try:
                        parsed = parse(raw_date)
                        date_iso = parsed.date().isoformat()
                    except Exception:
                        # If parsing fails, leave date as None
                        date_iso = None

            # As a last-ditch attempt, some layouts may include date in a .cat_date container
            if not date_iso:
                cat_date = await handle.query_selector('.cat_date')
                if cat_date:
                    raw_date = await cat_date.text_content()
                    if raw_date:
                        raw_date = raw_date.strip()
                        try:
                            parsed = parse(raw_date)
                            date_iso = parsed.date().isoformat()
                        except Exception:
                            date_iso = None

            # Ensure required fields are present. If url or title missing, skip this item.
            if not title and not url:
                # Nothing meaningful found for this container; skip
                continue

            item = {
                'title': title if title else None,
                'date': date_iso,
                'url': url if url else None,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)
        except Exception as e:
            # Protect against a single malformed item breaking the whole page parse
            # Continue to next item
            print(f"Error parsing an article element: {e}")
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
    # Candidate selectors for a "Load More" style button observed on the site
    load_more_selectors = [
        '#load-more-container .misha_loadmore',
        '.misha_loadmore'
    ]

    # Attempt to find and click a load-more button, prioritizing available selectors
    for sel in load_more_selectors:
        try:
            locator = page.locator(sel).first
            count = await locator.count()
            if count == 0:
                continue

            try:
                # Ensure element is in view
                await locator.scroll_into_view_if_needed()
            except Exception:
                pass

            # Get previous number of article items to detect new content after clicking
            prev_count = await page.evaluate("() => document.querySelectorAll('.row.postlist.post_type_post').length")

            clicked = False
            # Try normal click with force in case element isn't an interactive element
            try:
                await locator.click(timeout=5000, force=True)
                clicked = True
            except Exception:
                # Try dispatching a synthetic click event if direct click didn't work
                try:
                    await page.evaluate(
                        """(s) => {
                            const el = document.querySelector(s);
                            if (el) {
                                el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
                                return true;
                            }
                            return false;
                        }""",
                        sel
                    )
                    clicked = True
                except Exception:
                    clicked = False

            if not clicked:
                # If we couldn't click, try a fallback of mouse clicking at element center
                try:
                    box = await locator.bounding_box()
                    if box:
                        await page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                        await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                        clicked = True
                except Exception:
                    pass

            if not clicked:
                # Could not trigger click for this selector; try next selector
                continue

            # Wait for more items to be attached to the DOM
            try:
                await page.wait_for_function(
                    "prev => document.querySelectorAll('.row.postlist.post_type_post').length > prev",
                    prev_count,
                    timeout=15000
                )
                return
            except Exception:
                # Wait timed out — maybe content loads slower or uses different container.
                # Do a short polling loop as a last attempt
                max_attempts = 10
                for _ in range(max_attempts):
                    await page.wait_for_timeout(500)
                    cur_count = await page.evaluate("() => document.querySelectorAll('.row.postlist.post_type_post').length")
                    if cur_count > prev_count:
                        return
                # If still no new items, continue to next selector
        except Exception as e:
            # Continue to next selector if any selector causes an error
            print(f"Error attempting load-more selector '{sel}': {e}")
            continue

    # If no load-more button found or clicking it did not yield new items, fallback to infinite scroll
    # Perform a few scrolls to bottom with waits to allow lazy-loading content
    scroll_attempts = 5
    for _ in range(scroll_attempts):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        # Wait a bit for content to load
        await page.wait_for_timeout(2000)

    # No explicit navigation performed; caller will re-scrape the page to detect new items (if any)
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
                    # Create a deterministic key for deduplication (exclude None values consistently)
                    key = tuple(sorted((k, v) for k, v in item.items() if v is not None))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                # If no new items were added compared to last iteration, stop paging
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