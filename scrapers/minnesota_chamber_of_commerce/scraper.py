"""
Articles Scraper for Minnesota Chamber of Commerce

Generated at: 2026-03-19 15:13:23
Target URL: https://www.mnchamber.com/minnsights-blog
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

base_url = 'https://www.mnchamber.com/minnsights-blog'

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

    # Choose robust article container selector
    # Prefer semantic article.node--type-article but accept mncc--article as well.
    article_selectors = [
        'article.node--type-article',
        '.mncc--article',
        'ul.info-topics.mediaList li article'
    ]

    article_elements = []
    for sel in article_selectors:
        els = await page.query_selector_all(sel)
        if els:
            article_elements = els
            break

    # If nothing found, return empty list
    if not article_elements:
        return items

    for article in article_elements:
        try:
            # Title: look for h2.node__title a (headline anchor)
            title = None
            title_el = await article.query_selector('h2.node__title a')
            if title_el:
                # Use text_content() (per instructions) and strip whitespace/newlines
                raw_title = await title_el.text_content()
                if raw_title:
                    title = raw_title.strip()

            # URL: prefer the h2 anchor href, fallback to first article link to /blog/ or .more-link
            url = None
            href = None
            if title_el:
                href = await title_el.get_attribute('href')
            if not href:
                # fallback to any link to /blog/ inside article
                link_el = await article.query_selector('a[href^="/blog/"], a.more-link')
                if link_el:
                    href = await link_el.get_attribute('href')
            if href:
                url = urllib.parse.urljoin(base_url, href.strip())

            # Date: find span.node__meta inside the article (there may be multiple node__meta classes; target the one in header)
            date_str = None
            date_el = await article.query_selector('div.article-hd span.node__meta, div.node__meta span.node__meta, span.node__meta')
            if date_el:
                raw_date = await date_el.text_content()
                if raw_date:
                    # Clean up whitespace and non-breaking spaces
                    date_str = raw_date.strip().replace('\xa0', ' ')
                    # Sometimes there are line breaks and extra spacing; collapse spaces
                    date_str = ' '.join(date_str.split())

            date_formatted = None
            if date_str:
                try:
                    parsed = parse(date_str, fuzzy=True)
                    date_formatted = parsed.strftime('%Y-%m-%d')
                except Exception:
                    date_formatted = None

            # title and url are required — if title or url missing, skip item
            if not title or not url:
                # still include partially if url or title available? Requirement says title and url required,
                # so skip records that lack either to avoid invalid items.
                continue

            items.append({
                'title': title,
                'date': date_formatted,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Robustness: ignore individual article parsing errors, continue with others
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

    # Strategy:
    # 1. Try to find a dedicated "next" pager link (preferred)
    # 2. Try any link with rel="next"
    # 3. Try pager links with ?page= pattern
    # 4. If none found, perform infinite scroll fallback (scroll to bottom and wait)

    # Helper to attempt clicking an element and wait for navigation
    async def _click_and_wait(el):
        try:
            await el.scroll_into_view_if_needed()
        except Exception:
            pass
        # Attempt to click and wait for navigation; if no navigation occurs, wait a short time
        try:
            async with page.expect_navigation(timeout=8000):
                await el.click()
            # Wait for network to be mostly idle
            try:
                await page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                pass
            return True
        except Exception:
            # If navigation didn't occur, try a normal click and small wait
            try:
                await el.click()
                await page.wait_for_timeout(2000)
                return True
            except Exception:
                return False

    # 1. Prefer next pager item link
    next_selectors = [
        'li.pager__item.pager__item--next a',     # explicit next link
        'ul.pager__items a[rel="next"]',          # rel=next in pager
        'a[rel="next"]',                          # generic rel=next
        'ul.pager__items a[href*="?page="][title*="Next"], ul.pager__items a[href*="?page="][aria-label*="Next"]',
        'ul.pager__items a[href*="?page="]',      # any pager page links (we will pick the one with rel or "next" ideally)
    ]

    for sel in next_selectors:
        els = await page.query_selector_all(sel)
        if not els:
            continue
        # Prefer the element whose href contains page= and rel=next or that has "next" text
        candidate = None
        for el in els:
            # choose by rel attribute first
            rel = await el.get_attribute('rel')
            if rel and 'next' in rel.lower():
                candidate = el
                break
        if not candidate:
            # try to find visible element with "Next" in title/aria-label/text
            for el in els:
                title = (await el.get_attribute('title') or '').lower()
                aria = (await el.get_attribute('aria-label') or '').lower()
                txt = (await el.text_content() or '').lower()
                if 'next' in title or 'next' in aria or 'next' in txt:
                    candidate = el
                    break
        if not candidate:
            # fallback to first element in list
            candidate = els[0]

        # Try clicking candidate; if click not successful, try to get href and goto
        try:
            clicked = await _click_and_wait(candidate)
            if clicked:
                return
            href = await candidate.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(base_url, href.strip())
                await page.goto(next_url)
                try:
                    await page.wait_for_load_state('networkidle', timeout=8000)
                except Exception:
                    pass
                return
        except Exception:
            # Try next selector if this fails
            continue

    # If no pagination selectors matched, attempt to find a "Load more" style button
    load_more_selectors = [
        'button.load-more, a.load-more, button#load-more, .load-more-button, .button.load-more',
        'button[aria-label*="Load"], a[aria-label*="Load"]',
        'button[title*="Load"], a[title*="Load"]',
    ]
    for sel in load_more_selectors:
        els = await page.query_selector_all(sel)
        if not els:
            continue
        for el in els:
            try:
                clicked = await _click_and_wait(el)
                if clicked:
                    return
            except Exception:
                continue

    # Fallback: infinite scroll - scroll to bottom and wait for content to load
    try:
        previous_height = await page.evaluate("document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Wait for potential lazy-load
        await page.wait_for_timeout(3000)
        # Attempt one more incremental scroll to trigger loading
        await page.evaluate("window.scrollBy(0, 1000)")
        await page.wait_for_timeout(2000)
        # Optionally wait until scrollHeight changes or short timeout
        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height > previous_height:
            # Give time for elements to render
            await page.wait_for_timeout(1000)
    except Exception:
        # If anything fails, just wait a little to let content load
        await page.wait_for_timeout(2000)
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