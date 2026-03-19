"""
Articles Scraper for Unity Unitarian

Generated at: 2026-03-19 15:08:10
Target URL: https://www.unityunitarian.org/beloved-community-news/category/all
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

base_url = 'https://www.unityunitarian.org/beloved-community-news/category/all'

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
        - date: Publication date in YYYY-MM-DD format
        - url: Link to the full article
        - scraper: module path for traceability
    """

    items = []

    # Use the blog-post container which groups title, date, and link
    post_elements = await page.query_selector_all('div.blog-post')

    for post in post_elements:
        # Title and URL
        title = None
        url = None
        try:
            title_anchor = await post.query_selector('a.blog-title-link')
            if title_anchor:
                raw_title = await title_anchor.text_content()
                if raw_title:
                    title = raw_title.strip()
                href = await title_anchor.get_attribute('href')
                if href:
                    href = href.strip()
                    # Normalize protocol-relative URLs (//example.com/...)
                    if href.startswith('//'):
                        url = 'https:' + href
                    else:
                        # Use page.url as base to resolve relative hrefs
                        url = urllib.parse.urljoin(page.url, href)
        except Exception:
            title = title or None
            url = url or None

        # Date (optional)
        date_value = None
        try:
            date_el = await post.query_selector('span.date-text, p.blog-date')
            if date_el:
                raw_date = await date_el.text_content()
                if raw_date:
                    raw_date = raw_date.strip()
                    # Some date strings may contain extra whitespace/newlines
                    parsed = parse(raw_date, fuzzy=True)
                    date_value = parsed.date().isoformat()
        except Exception:
            date_value = None

        # Ensure required keys exist; optional date may be None
        items.append({
            'title': title if title else None,
            'date': date_value,
            'url': url if url else None,
            'scraper': SCRAPER_MODULE_PATH,
        })

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """

    # Candidate selectors for pagination links/buttons
    pagination_selectors = [
        'div.blog-page-nav a',           # generic nav area links
        'div.blog-page-nav-previous a',  # previous (site uses "Previous" to go to older pages)
        'div.blog-page-nav-next a',      # next (if present)
        'a.blog-link',                   # site-specific link class
        'nav a[rel="next"]',             # semantic next link
        'a[rel="next"]',
        'a:has-text("Previous")',
        'a:has-text("Next")',
        'a:has-text("older")',
        'a:has-text("Older")',
    ]

    candidates = []

    try:
        for sel in pagination_selectors:
            try:
                els = await page.query_selector_all(sel)
            except Exception:
                els = []
            for el in els:
                try:
                    href = await el.get_attribute('href')
                    # if no href, skip (could be a button requiring JS)
                    if not href:
                        continue
                    href = href.strip()
                    # Normalize protocol-relative URLs (//example.com/...)
                    if href.startswith('//'):
                        resolved = 'https:' + href
                    else:
                        resolved = urllib.parse.urljoin(page.url, href)
                    # Ignore anchors that resolve to the same URL
                    if resolved and resolved != page.url:
                        candidates.append((el, resolved))
                except Exception:
                    continue

        # Deduplicate candidates by URL, preserve order
        seen_urls = set()
        filtered = []
        for el, url in candidates:
            if url not in seen_urls:
                seen_urls.add(url)
                filtered.append((el, url))
        candidates = filtered

        # If we found suitable pagination links, choose best candidate
        if candidates:
            # Prefer links whose text contains "Previous" or that match the category path
            chosen_el = None
            chosen_url = None
            for el, url in candidates:
                try:
                    txt = (await el.text_content() or '').strip().lower()
                except Exception:
                    txt = ''
                if 'previous' in txt or 'older' in txt or '/category/' in url or '/page' in url or '/all/' in url:
                    chosen_el = el
                    chosen_url = url
                    break
            if not chosen_el:
                chosen_el, chosen_url = candidates[0]

            # Try to click the element and wait for navigation (preferred)
            try:
                # start waiting for navigation before the click
                nav_wait = page.wait_for_navigation(wait_until='networkidle', timeout=10000)
                await chosen_el.scroll_into_view_if_needed()
                await chosen_el.click()
                await nav_wait
                # small pause to allow dynamic content to settle
                await page.wait_for_timeout(800)
                return
            except Exception:
                # If click+navigation doesn't work, try direct goto to the resolved URL
                try:
                    await page.goto(chosen_url)
                    await page.wait_for_load_state('networkidle')
                    await page.wait_for_timeout(800)
                    return
                except Exception:
                    # if goto also fails, continue to infinite scroll fallback below
                    pass

    except Exception:
        # Any unexpected error while trying to find/click next -> fallback to infinite scroll below
        pass

    # Fallback: infinite scroll - attempt a few scrolls and wait for new content
    try:
        initial_count = await page.locator('div.blog-post').count()
    except Exception:
        initial_count = 0

    max_scrolls = 5
    for _ in range(max_scrolls):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            new_count = await page.locator('div.blog-post').count()
            if new_count > initial_count:
                # New items loaded; return to allow scrape_page to pick them up
                return
            initial_count = new_count
        except Exception:
            await page.wait_for_timeout(1000)
            continue

    # If infinite scroll didn't load new items, do nothing (caller will detect no growth and stop)
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