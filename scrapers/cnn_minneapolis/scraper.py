import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.cnn.com/search?q=minneapolis&from=0&size=10&page=1&sort=newest&types=all&section='

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
        - title: Headline or title of the article (string)
        - date: Publication date in YYYY-MM-DD format or None
        - url: Link to the full article (string)
        - scraper: module path for traceability
    """
    items = []

    # Primary container selectors observed on the page examples.
    container_selector = 'li.card.container__item, .container__item'

    # Attempt to wait briefly for article containers to appear, but keep tolerant to timeouts.
    try:
        await page.wait_for_selector(container_selector, timeout=3000)
    except Exception:
        # If no containers within timeout, continue and return empty list (safe failure)
        pass

    containers = await page.query_selector_all(container_selector)

    for c in containers:
        try:
            # Title extraction: prefer the headline text element
            title_el = await c.query_selector('.container__headline-text')
            title = None
            if title_el:
                raw_title = await title_el.text_content()
                if raw_title:
                    title = raw_title.strip()

            # URL extraction: prefer anchor href, fallback to data-open-link attribute on container
            url = None
            link_el = await c.query_selector('a.container__link, a')
            if link_el:
                href = await link_el.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(page.url, href.strip())
            if not url:
                # fallback to data-open-link attribute present on li examples
                data_open = await c.get_attribute('data-open-link')
                if data_open:
                    url = urllib.parse.urljoin(page.url, data_open.strip())

            # Date extraction: try container date selectors
            date_el = await c.query_selector('.container__date, .container_list-images-with-description__date')
            date_val = None
            if date_el:
                raw_date = await date_el.text_content()
                if raw_date:
                    # Normalize whitespace
                    raw_date = raw_date.strip()
                    try:
                        dt = parse(raw_date, fuzzy=True)
                        # Format consistently as YYYY-MM-DD
                        date_val = dt.date().isoformat()
                    except Exception:
                        date_val = None

            # Only include items that have both title and url (required fields)
            if title and url:
                items.append({
                    'title': title,
                    'date': date_val,
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

    # Candidate selectors for "next" pagination button observed in examples.
    next_selectors = [
        '.pagination-arrow.pagination-arrow-right',
        '.pagination-arrow-right.search__pagination-link',
        '.pagination-arrow-right',
        '.search__pagination-link',
    ]

    # Container selector used to detect loaded articles (mirror of scrape_page)
    container_selector = 'li.card.container__item, .container__item'

    next_elem = None
    # Collect candidate elements and choose the best match:
    try:
        # Try the more specific selectors first to prefer the actual "Next" arrow element
        for sel in next_selectors:
            try:
                elems = await page.query_selector_all(sel)
            except Exception:
                elems = []
            for el in elems:
                try:
                    # Prefer elements that contain the text "next" (case-insensitive)
                    text = ''
                    try:
                        text = (await el.text_content() or '').strip().lower()
                    except Exception:
                        text = ''
                    # Also check aria-label if present
                    aria = await el.get_attribute('aria-label') or ''
                    aria = aria.lower() if aria else ''
                    # Check class attribute for arrow-specific classes
                    cls = await el.get_attribute('class') or ''
                    cls = cls.lower()
                    # Choose element if it explicitly says "next" or has arrow class
                    if 'next' in text or 'next' in aria or 'pagination-arrow-right' in cls or 'pagination-arrow' in cls:
                        next_elem = el
                        break
                    # If it's a search__pagination-link and contains numeric page text, skip here;
                    # but keep as fallback if nothing else matches.
                    if not next_elem and not any(ch.isdigit() for ch in text):
                        next_elem = el
                        break
                except Exception:
                    continue
            if next_elem:
                break
    except Exception:
        next_elem = None

    # Helper to attempt clicking and waiting for either navigation, URL change, or new content
    async def click_and_wait(elem):
        old_url = page.url
        try:
            await elem.scroll_into_view_if_needed()
        except Exception:
            pass

        # If the element or its descendants contain an <a href>, prefer navigating to that URL
        try:
            href = await elem.get_attribute('href')
            if not href:
                a = await elem.query_selector('a')
                if a:
                    href = await a.get_attribute('href')
            if href:
                target = urllib.parse.urljoin(page.url, href.strip())
                # Navigate to the target URL and wait for load/networkidle
                try:
                    await page.goto(target, wait_until='networkidle', timeout=10000)
                    return True
                except Exception:
                    # fallback to click approach below
                    pass
        except Exception:
            pass

        # Try to click and wait for navigation event (if any)
        try:
            # Use wait_for_navigation in case the click triggers a real navigation
            navigation = page.wait_for_navigation(wait_until='networkidle', timeout=7000)
            await elem.click()
            try:
                await navigation
                return True
            except Exception:
                # No navigation occurred; continue to check for dynamic content changes
                pass
        except Exception:
            # Click without waiting for navigation
            try:
                await elem.click()
            except Exception:
                pass

        # After click, wait for content to change: either URL change or increased number of items
        try:
            pre_count = len(await page.query_selector_all(container_selector))
        except Exception:
            pre_count = None

        # Wait up to a few seconds for changes
        for _ in range(6):
            await page.wait_for_timeout(1000)
            try:
                if page.url != old_url:
                    return True
            except Exception:
                pass
            if pre_count is not None:
                try:
                    cur_count = len(await page.query_selector_all(container_selector))
                    if cur_count > pre_count:
                        return True
                except Exception:
                    pass
        # As a last resort, wait for network idle briefly
        try:
            await page.wait_for_load_state('networkidle', timeout=3000)
            return True
        except Exception:
            return False

    if next_elem:
        try:
            changed = await click_and_wait(next_elem)
            if changed:
                return
            # If clicking the chosen element didn't result in change, try to find any <a> with "page=" in href and the next page number
            # Attempt to extract current page number and navigate to next by updating query param.
            try:
                parsed = urllib.parse.urlparse(page.url)
                qs = urllib.parse.parse_qs(parsed.query)
                current_page_vals = qs.get('page') or qs.get('from')  # check common params
                current_page = None
                if current_page_vals:
                    try:
                        current_page = int(current_page_vals[0])
                    except Exception:
                        current_page = None
                # If page param present and integer, increment it
                if current_page is not None:
                    new_page_num = current_page + 1
                    qs['page'] = [str(new_page_num)]
                    new_query = urllib.parse.urlencode(qs, doseq=True)
                    new_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
                    try:
                        await page.goto(new_url, wait_until='networkidle', timeout=10000)
                        return
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            # If next element handling fails, fall through to scroll fallback
            pass

    # Fallback: infinite scroll (attempt multiple incremental scrolls until no new height)
    try:
        previous_height = await page.evaluate('() => document.body.scrollHeight')
        # Perform a few scroll attempts to load more content
        for _ in range(5):
            await page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)')
            # give JS time to load new items
            await page.wait_for_timeout(2000)
            new_height = await page.evaluate('() => document.body.scrollHeight')
            if new_height == previous_height:
                # no more content loaded
                break
            previous_height = new_height
    except Exception:
        # If any error during scroll fallback, just sleep briefly as a last resort
        await page.wait_for_timeout(1500)

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