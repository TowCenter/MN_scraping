"""
Articles Scraper for Hennepin County Library

Generated at: 2026-03-19 15:16:09
Target URL: https://www.hclib.org/about/news
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

import json
import os
import re
import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse

base_url = 'https://www.hclib.org/about/news'

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

async def _clean_text(text):
    """Normalize whitespace and strip."""
    if text is None:
        return None
    # Replace non-breaking spaces and collapse whitespace
    txt = re.sub(r'\s+', ' ', text.replace('\u00A0', ' ')).strip()
    return txt if txt else None

async def _parse_date(text):
    """Parse date text into YYYY-MM-DD or return None."""
    if not text:
        return None
    try:
        dt = parse(text, fuzzy=True, dayfirst=False)
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
        - date: Publication date in YYYY-MM-DD format or None
        - url: Absolute link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Choose robust container selectors observed on the page.
    # Use both wrapper and content selectors to cover different DOM arrangements.
    container_selectors = 'div.listing.feed-listing, div.listing__content'

    containers = await page.query_selector_all(container_selectors)

    for container in containers:
        try:
            # Title and URL: prefer the H3 anchor
            title_el = await container.query_selector('h3.h3 a')
            if not title_el:
                # Fallback to any anchor in the container
                title_el = await container.query_selector('a[href]')

            title = None
            url = None
            if title_el:
                raw_title = await title_el.text_content()
                title = await _clean_text(raw_title)
                href = await title_el.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(page.url, href.strip())

            # Date: look for p.news-date or elements with class news-date
            date_el = await container.query_selector('p.news-date, .news-date, p.news-date.h4')
            date_val = None
            if date_el:
                raw_date = await date_el.text_content()
                raw_date = await _clean_text(raw_date)
                date_val = await _parse_date(raw_date)

            # If we have at least title and url, append the item.
            # Date is optional; use None when not available.
            if title and url:
                items.append({
                    'title': title,
                    'date': date_val,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                })

        except Exception:
            # Skip malformed item but continue scraping others
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
    # Helper to safely get href and text
    async def _get_attr(el, name):
        try:
            return await el.get_attribute(name)
        except Exception:
            return None

    # Container selector used to detect content changes after pagination
    container_selector = 'div.listing.feed-listing, div.listing__content'
    try:
        initial_nodes = await page.query_selector_all(container_selector)
        initial_count = len(initial_nodes)
    except Exception:
        initial_count = 0

    original_url = page.url

    # Strategy 1: explicit "next" pagination link (preferred)
    next_selectors = [
        'li.pagination__next a',   # example provided
        'a[rel="next"]',          # common semantic next link
        'ul.pagination a.pager',   # discovered selector
        'ul.pagination li a',      # generic pagination links
        '.pagination a',           # generic
    ]

    async def _post_click_wait():
        # Wait for navigation OR for DOM to change (more items), or slight timeout as fallback
        try:
            # First try to wait for networkidle for JS-driven loads
            await page.wait_for_load_state('networkidle', timeout=4000)
        except Exception:
            pass

        # Wait for new content to appear (more article containers) up to a few seconds
        try:
            await page.wait_for_function(
                "(sel, old) => document.querySelectorAll(sel).length > old",
                container_selector,
                initial_count,
                timeout=8000
            )
            return True
        except Exception:
            # If content count didn't change, but URL changed, consider success
            if page.url != original_url:
                return True
            return False

    for sel in next_selectors:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue

            # If this element is clearly a page number link (like .pager) we may want to click next numeric link.
            # If selector matched a container of multiple anchors, handle separately.
            # Prefer clicking explicit "Next" text first
            txt = None
            try:
                txt = await el.text_content()
            except Exception:
                txt = None

            href = await _get_attr(el, 'href')

            # If this looks like a direct URL and not just '#', navigate directly
            if href and href.strip() and not href.strip().startswith('#'):
                next_url = urllib.parse.urljoin(page.url, href.strip())
                try:
                    await page.goto(next_url)
                except Exception:
                    # fallback to click if goto fails
                    try:
                        await el.scroll_into_view_if_needed()
                        await el.click()
                    except Exception:
                        pass
                success = await _post_click_wait()
                if success:
                    return
                else:
                    # try clicking as alternative
                    try:
                        await el.scroll_into_view_if_needed()
                        await el.click()
                    except Exception:
                        pass
                    success = await _post_click_wait()
                    if success:
                        return
                    # otherwise continue to next selector
                    continue

            # If href is '#' or empty, try clicking (after scrolling) and wait
            try:
                await el.scroll_into_view_if_needed()
                # Use JS click if normal click sometimes fails
                try:
                    await el.click()
                except Exception:
                    try:
                        await page.evaluate("(e) => e.click()", el)
                    except Exception:
                        pass
                success = await _post_click_wait()
                if success:
                    return
            except Exception:
                continue
        except Exception:
            continue

    # Strategy 2: Find any anchor or button with visible text "Next" (case-insensitive)
    try:
        anchors = await page.query_selector_all('a, button')
        for a in anchors:
            try:
                txt = await a.text_content()
                if not txt:
                    continue
                if 'next' not in txt.lower():
                    continue

                href = await _get_attr(a, 'href')
                if href and href.strip() and not href.strip().startswith('#'):
                    next_url = urllib.parse.urljoin(page.url, href.strip())
                    try:
                        await page.goto(next_url)
                    except Exception:
                        try:
                            await a.scroll_into_view_if_needed()
                            await a.click()
                        except Exception:
                            pass
                    success = await _post_click_wait()
                    if success:
                        return
                else:
                    try:
                        await a.scroll_into_view_if_needed()
                        try:
                            await a.click()
                        except Exception:
                            await page.evaluate("(e) => e.click()", a)
                        success = await _post_click_wait()
                        if success:
                            return
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass

    # Strategy 3: Numeric pager handling - click the pager link after the active/current one
    try:
        # Find pagination container(s)
        pag_containers = await page.query_selector_all('ul.pagination, nav.pagination, .pagination')
        for pc in pag_containers:
            try:
                # find current/active item
                active = await pc.query_selector('li.active a, a.active, [aria-current="page"], li.current a')
                if active:
                    # find next sibling li > a
                    parent_li = await active.evaluate_handle("(el) => el.closest('li') || el.parentElement")
                    if parent_li:
                        # get next sibling
                        next_sibling = await page.evaluate_handle("(el) => el.nextElementSibling", parent_li)
                        if next_sibling:
                            try:
                                next_link = await next_sibling.as_element().query_selector('a')
                                if next_link:
                                    href = await _get_attr(next_link, 'href')
                                    if href and href.strip() and not href.strip().startswith('#'):
                                        next_url = urllib.parse.urljoin(page.url, href.strip())
                                        try:
                                            await page.goto(next_url)
                                        except Exception:
                                            try:
                                                await next_link.scroll_into_view_if_needed()
                                                await next_link.click()
                                            except Exception:
                                                pass
                                        success = await _post_click_wait()
                                        if success:
                                            return
                                    else:
                                        try:
                                            await next_link.scroll_into_view_if_needed()
                                            try:
                                                await next_link.click()
                                            except Exception:
                                                await page.evaluate("(e) => e.click()", next_link)
                                            success = await _post_click_wait()
                                            if success:
                                                return
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                # If no active found, as fallback click the pager anchor with text matching "Next" or the highest numeric not yet visited
                pager_links = await pc.query_selector_all('a')
                for pl in pager_links:
                    try:
                        txt = await pl.text_content()
                        if not txt:
                            continue
                        if 'next' in txt.lower():
                            href = await _get_attr(pl, 'href')
                            if href and href.strip() and not href.strip().startswith('#'):
                                next_url = urllib.parse.urljoin(page.url, href.strip())
                                try:
                                    await page.goto(next_url)
                                except Exception:
                                    try:
                                        await pl.scroll_into_view_if_needed()
                                        await pl.click()
                                    except Exception:
                                        pass
                                success = await _post_click_wait()
                                if success:
                                    return
                            else:
                                try:
                                    await pl.scroll_into_view_if_needed()
                                    try:
                                        await pl.click()
                                    except Exception:
                                        await page.evaluate("(e) => e.click()", pl)
                                    success = await _post_click_wait()
                                    if success:
                                        return
                                except Exception:
                                    continue
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass

    # Strategy 4: "Load more" style buttons (text matches)
    try:
        buttons = await page.query_selector_all('button, a')
        for b in buttons:
            try:
                txt = await b.text_content()
                if txt and 'load more' in txt.lower():
                    try:
                        href = await _get_attr(b, 'href')
                        if href and href.strip() and not href.strip().startswith('#'):
                            next_url = urllib.parse.urljoin(page.url, href.strip())
                            await page.goto(next_url)
                            await page.wait_for_load_state('networkidle', timeout=4000)
                        else:
                            await b.scroll_into_view_if_needed()
                            try:
                                await b.click()
                            except Exception:
                                await page.evaluate("(e) => e.click()", b)
                            await page.wait_for_load_state('networkidle', timeout=4000)
                        success = await _post_click_wait()
                        if success:
                            return
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass

    # Fallback: infinite scroll - attempt to scroll to the bottom a few times to trigger lazy loading
    try:
        for _ in range(6):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
            try:
                increased = await page.evaluate(
                    "(sel, old) => document.querySelectorAll(sel).length > old",
                    container_selector,
                    initial_count
                )
                if increased:
                    return
            except Exception:
                pass
    except Exception:
        # As final fallback, perform a single short sleep to allow dynamic content to appear
        await page.wait_for_timeout(1500)

    # Return without navigation: caller will detect no new items and stop when appropriate.
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