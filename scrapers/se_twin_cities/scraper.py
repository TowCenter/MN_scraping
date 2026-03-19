"""
Articles Scraper for SE Twin Cities

Generated at: 2026-03-19 15:52:30
Target URL: https://setwincities.com/category/local-government/
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse as parse_date
import urllib.parse
import asyncio

base_url = 'https://setwincities.com/category/local-government/'

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

    # Select article containers
    article_elems = await page.query_selector_all('article.ultp-block-item')
    for article in article_elems:
        # Title: prefer .ultp-block-title a
        title_el = await article.query_selector('.ultp-block-title a')
        title = None
        if title_el:
            raw_title = await title_el.text_content()
            if raw_title:
                title = raw_title.strip()

        # URL: prefer href on title anchor, fallback to first anchor in article
        url = None
        if title_el:
            href = await title_el.get_attribute('href')
            if href:
                url = urllib.parse.urljoin(base_url, href.strip())
        if not url:
            first_a = await article.query_selector('a[href]')
            if first_a:
                href = await first_a.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

        # Date: span.ultp-block-date if present
        date = None
        date_el = await article.query_selector('.ultp-block-date')
        if date_el:
            raw_date = await date_el.text_content()
            if raw_date:
                raw_date = raw_date.strip()
                try:
                    dt = parse_date(raw_date, fuzzy=True)
                    date = dt.strftime('%Y-%m-%d')
                except Exception:
                    date = None

        # Only include items that have at least title and url
        # If either is missing, still include but set missing field to None (per requirements)
        items.append({
            'title': title if title else None,
            'date': date,
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
    # Try common "next" selectors, prioritizing explicit next links
    next_selectors = [
        'a[rel="next"]',
        '.ultp-next-page-numbers a',
        '.ultp-pagination a',
        'a[aria-label*="next" i]',
        'a:has-text("Next")',
        'a:has-text("Load more")',
        'a:has-text("More")',
        '.pagination a.next',
        'a.next',
    ]

    next_link = None
    next_href = None

    for sel in next_selectors:
        try:
            candidates = await page.query_selector_all(sel)
        except Exception:
            candidates = []
        if not candidates:
            continue

        # Evaluate candidates to find the best "next" link
        for cand in candidates:
            try:
                href = await cand.get_attribute('href')
                text = await cand.text_content() or ''
                aria = await cand.get_attribute('aria-label') or ''
                rel = await cand.get_attribute('rel') or ''
                txt_lower = text.lower()
                aria_lower = aria.lower()
                rel_lower = rel.lower()

                # Prefer candidates that explicitly indicate "next" or have rel="next"
                if href:
                    if 'next' in rel_lower or 'next' in aria_lower or 'next' in txt_lower or 'more' in txt_lower or 'load' in txt_lower:
                        next_link = cand
                        next_href = href
                        break
                    # fallback: if only one candidate and href looks like page/N
                    if not next_link and ('page/' in href or '?paged=' in href or 'p=' in href):
                        next_link = cand
                        next_href = href
                        # don't break immediately; prefer explicit next if present among next selectors
                else:
                    # candidate without href might be a button that triggers JS - keep as candidate
                    if not next_link:
                        next_link = cand
                        next_href = None
            except Exception:
                continue

        if next_link:
            break

    # If we found a next href, navigate to it
    if next_href:
        next_url = urllib.parse.urljoin(page.url, next_href.strip())
        try:
            await page.goto(next_url)
            await page.wait_for_load_state('networkidle')
            return
        except Exception:
            # If goto failed, attempt click as fallback
            try:
                if next_link:
                    await next_link.scroll_into_view_if_needed()
                    await next_link.click()
                    # wait for potential navigation or new content
                    try:
                        await page.wait_for_load_state('networkidle', timeout=5000)
                    except Exception:
                        await page.wait_for_timeout(2000)
                    return
            except Exception:
                pass

    # If we found a next_link without href (JS button), attempt click
    if next_link and not next_href:
        try:
            await next_link.scroll_into_view_if_needed()
            await next_link.click()
            try:
                await page.wait_for_load_state('networkidle', timeout=5000)
            except Exception:
                await page.wait_for_timeout(2000)
            return
        except Exception:
            pass

    # Fallback to infinite scroll: scroll to bottom and wait for content to load
    # Perform multiple small scrolls to try to trigger lazy loading
    total_scrolls = 3
    for _ in range(total_scrolls):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
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
                    # create a stable key from the item's values (order-independent)
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