import json
import os
import re
import urllib.parse
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import asyncio

"""
Articles Scraper for YWCA St Paul

Generated at: 2026-03-19 15:06:59
Target URL: https://www.ywcastpaul.org/blog/
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

base_url = 'https://www.ywcastpaul.org/blog/'

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

async def _safe_text(element):
    """Return text_content() stripped or None if element is None."""
    if not element:
        return None
    txt = await element.text_content()
    if txt is None:
        return None
    return txt.strip()

async def _safe_attr(element, attr):
    """Return attribute value or None if missing."""
    if not element:
        return None
    return await element.get_attribute(attr)

def _normalize_date(date_str):
    """Parse a date string into YYYY-MM-DD or return None."""
    if not date_str:
        return None
    try:
        # Remove stray HTML entities/newlines and trailing content
        cleaned = re.sub(r'\s+', ' ', date_str).strip()
        dt = parse(cleaned, fuzzy=True)
        return dt.date().isoformat()
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

    # Prefer article.container selector; fallback to card containers if articles not present
    containers = await page.query_selector_all('article.elementor-post')
    if not containers:
        containers = await page.query_selector_all('div.elementor-post__card')

    for container in containers:
        try:
            # Title: prefer h3.elementor-post__title a
            title_anchor = await container.query_selector('h3.elementor-post__title a')
            title = await _safe_text(title_anchor)
            if not title:
                # fallback: h3 text node
                h3 = await container.query_selector('h3.elementor-post__title')
                title = await _safe_text(h3)

            # URL: prefer the href on the title anchor, fallback to thumbnail link
            url = None
            if title_anchor:
                href = await _safe_attr(title_anchor, 'href')
                if href:
                    url = urllib.parse.urljoin(base_url, href)
            if not url:
                thumb = await container.query_selector('a.elementor-post__thumbnail__link')
                href = await _safe_attr(thumb, 'href')
                if href:
                    url = urllib.parse.urljoin(base_url, href)

            # Date: span.elementor-post-date
            date_el = await container.query_selector('span.elementor-post-date')
            date_text = await _safe_text(date_el)
            date = _normalize_date(date_text)

            # Ensure required fields exist; url and title are required per spec.
            # If missing, set to None (tests may handle missing optional date).
            item = {
                'title': title if title else None,
                'date': date,
                'url': url if url else None,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)
        except Exception:
            # Skip problematic container but continue with others
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

    # 1) Try explicit 'next' pagination link
    try:
        next_link = await page.query_selector('a.page-numbers.next')
        if next_link:
            href = await _safe_attr(next_link, 'href')
            if href:
                next_url = urllib.parse.urljoin(base_url, href)
                await page.goto(next_url)
                await page.wait_for_load_state('networkidle')
                return

        # 2) Try pagination nav links: nav.elementor-pagination a.page-numbers
        nav_links = await page.query_selector_all('nav.elementor-pagination a.page-numbers')
        if nav_links:
            # determine current page number from URL if possible
            cur_page_num = 1
            m = re.search(r'/page/(\d+)/?', page.url)
            if m:
                try:
                    cur_page_num = int(m.group(1))
                except Exception:
                    cur_page_num = 1

            # Try to find link with page number = current + 1 by inspecting href or text
            candidate_href = None
            for el in nav_links:
                href = await _safe_attr(el, 'href')
                # try extract page num from href
                if href:
                    mhref = re.search(r'/page/(\d+)/?', href)
                    if mhref:
                        try:
                            pnum = int(mhref.group(1))
                            if pnum == cur_page_num + 1:
                                candidate_href = href
                                break
                        except Exception:
                            pass
                # fallback: check text content if it contains an integer equal to current+1
                text = await _safe_text(el)
                if text:
                    digits = re.findall(r'(\d+)', text)
                    if digits:
                        try:
                            tnum = int(digits[-1])
                            if tnum == cur_page_num + 1:
                                href = await _safe_attr(el, 'href')
                                if href:
                                    candidate_href = href
                                    break
                        except Exception:
                            pass

            # If we found candidate_href navigate to it
            if candidate_href:
                next_url = urllib.parse.urljoin(base_url, candidate_href)
                await page.goto(next_url)
                await page.wait_for_load_state('networkidle')
                return

            # If nothing matched, as a last-resort try to click a link that contains 'Next' or '»'
            for el in nav_links:
                txt = await _safe_text(el) or ''
                if 'next' in txt.lower() or '»' in txt:
                    href = await _safe_attr(el, 'href')
                    if href:
                        next_url = urllib.parse.urljoin(base_url, href)
                        await page.goto(next_url)
                        await page.wait_for_load_state('networkidle')
                        return

    except Exception:
        # If any error occurs while attempting pagination, fall back to infinite scroll behavior below
        pass

    # 3) Fallback to infinite scroll: scroll to bottom and wait for content to load
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Allow lazy-loaded or infinite-scroll content to load
        await page.wait_for_timeout(3000)
    except Exception:
        # ignore scroll failures
        await page.wait_for_timeout(1000)


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