import json
import os
import re
import urllib.parse
from datetime import datetime

from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import asyncio

base_url = 'https://www.newsbreak.com/minneapolis-mn'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch headless by default; tests/environment may override
        self.browser = await self.playwright.chromium.launch()
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.browser.close()
        await self.playwright.stop()

async def _try_parse_date(text: str):
    """
    Try to find and parse a date from the provided text.
    Returns a string in YYYY-MM-DD or None if parsing failed.
    """
    if not text:
        return None

    # Common long-form month day, year pattern (e.g., "June 4, 1974")
    month_day_year_matches = re.findall(r'([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})', text)
    candidates = month_day_year_matches

    # Also try to find ISO-like or numeric date patterns
    iso_matches = re.findall(r'(\d{4}-\d{2}-\d{2})', text)
    candidates += iso_matches

    # Also try shorter month-year patterns (e.g., "Feb 2026")
    short_matches = re.findall(r'([A-Za-z]{3,9}\s+\d{4})', text)
    candidates += short_matches

    # If nothing found by regex, try to loosely parse the whole text (fuzzy)
    if not candidates:
        try:
            dt = parse(text, fuzzy=True, default=datetime(1900, 1, 1))
            # Make sure parse found something reasonable (year > 1900)
            if dt.year >= 1900:
                return dt.strftime('%Y-%m-%d')
        except Exception:
            return None

    for c in candidates:
        try:
            dt = parse(c, fuzzy=True, default=datetime(1900, 1, 1))
            if dt.year >= 1900:
                return dt.strftime('%Y-%m-%d')
        except Exception:
            continue

    return None

async def scrape_page(page):
    """
    Extract article data from the current page.

    Parameters:
        page: Playwright page object

    Returns:
        List of dictionaries containing article data with keys:
        - title: Headline or title of the article
        - date: Publication date in YYYY-MM-DD format (optional; None if not found)
        - url: Absolute link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use a reasonably generic article container found in the examples.
    # 'section.my-1' matches article blocks on the page and is robust to minor class variations.
    article_nodes = await page.query_selector_all('section.my-1')

    for node in article_nodes:
        try:
            # Title: prefer the h3.text-xl element
            h3 = await node.query_selector('h3.text-xl')
            if not h3:
                # fallback: any h3 inside the node
                h3 = await node.query_selector('h3')
            if not h3:
                # Skip this node if no title found
                continue

            raw_title = await h3.text_content()
            title = raw_title.strip() if raw_title else None
            if not title:
                continue  # title is required

            # URL: try to find the closest anchor that wraps the title
            href = await h3.evaluate(
                "el => { const a = el.closest('a'); return a ? a.getAttribute('href') : null }"
            )
            if not href:
                # fallback: find first anchor with /news/ or /m/ inside the node
                a = await node.query_selector('a[href^="/news/"], a[href^="/m/"]')
                if a:
                    href = await a.get_attribute('href')

            url = urllib.parse.urljoin(base_url, href) if href else None
            if not url:
                # url is required; skip if not present
                continue

            # Date: attempt multiple strategies
            date_value = None

            # 1) Look for a paragraph directly after the title which may contain an absolute date (example: obituary dates)
            # Try to find a sibling paragraph of the title
            p_after = None
            # Try selecting a p that is within the same article subtree and appears after the h3
            # We will look for paragraph elements and choose the one with the most likely date text.
            p_candidates = await node.query_selector_all('p')
            for p in p_candidates:
                txt = await p.text_content() or ''
                txt = txt.strip()
                if not txt:
                    continue
                # If paragraph starts with a month name and a day, it's likely an absolute date
                if re.match(r'^[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}', txt):
                    p_after = txt
                    break
                # also consider paragraphs with a year in them (e.g., obituary lines)
                if re.search(r'\d{4}', txt):
                    # keep as a potential candidate but continue scanning for better match
                    if not p_after:
                        p_after = txt

            if p_after:
                date_value = await _try_parse_date(p_after)

            # 2) If not found, look for smaller metadata div (e.g., 'div.text-gray-light.text-sm') which sometimes contains time or date
            if not date_value:
                meta_div = await node.query_selector('div.text-gray-light.text-sm')
                if meta_div:
                    meta_txt = (await meta_div.text_content() or '').strip()
                    date_value = await _try_parse_date(meta_txt)

            # 3) As a last resort, look at the entire node text and try to extract a date
            if not date_value:
                node_text = await node.text_content() or ''
                date_value = await _try_parse_date(node_text)

            item = {
                'title': title,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)

        except Exception:
            # Don't fail the whole page because of one malformed node; continue gracefully
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks or navigates to next page URL if found. Falls back to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Try to find explicit pagination links that include a ?page= parameter.
    try:
        anchors = await page.query_selector_all('a[href*="?page="]')
        next_href = None

        if anchors:
            # Prefer an anchor whose text includes 'next' (case-insensitive)
            for a in anchors:
                try:
                    txt = (await a.text_content() or '').strip().lower()
                    href = await a.get_attribute('href')
                    if not href:
                        continue
                    if 'next' in txt or 'more' in txt:
                        next_href = href
                        break
                except Exception:
                    continue

            # If none of the anchors had explicit 'next' text, pick the first page= anchor whose href leads to a different page
            if not next_href:
                current_url = page.url
                for a in anchors:
                    try:
                        href = await a.get_attribute('href')
                        if not href:
                            continue
                        candidate = urllib.parse.urljoin(base_url, href)
                        if candidate != current_url:
                            next_href = href
                            break
                    except Exception:
                        continue

        # If we found a pagination href, navigate to it
        if next_href:
            next_url = urllib.parse.urljoin(base_url, next_href)
            try:
                await page.goto(next_url)
                await page.wait_for_load_state('networkidle')
                return
            except Exception:
                # Try a safe click on the element if goto failed (some sites use js routing)
                try:
                    # find the anchor element by href again and click it
                    el = await page.query_selector(f'a[href="{next_href}"]')
                    if el:
                        await el.scroll_into_view_if_needed()
                        await el.click()
                        await page.wait_for_load_state('networkidle')
                        return
                except Exception:
                    # fall through to infinite scroll fallback
                    pass

        # Look for rel="next" link
        rel_next = await page.query_selector('a[rel="next"]')
        if rel_next:
            href = await rel_next.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(base_url, href)
                await page.goto(next_url)
                await page.wait_for_load_state('networkidle')
                return

        # If no explicit pagination, attempt to find "Load more" or similar button
        load_more_selectors = [
            'button:has-text("Load more")',
            'button:has-text("Load More")',
            'button:has-text("More")',
            'button.load-more',
            'a:has-text("Load more")',
            'a:has-text("More")'
        ]
        for sel in load_more_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.scroll_into_view_if_needed()
                    await btn.click()
                    # allow new content to load
                    await page.wait_for_timeout(2000)
                    return
            except Exception:
                continue

    except Exception:
        # If anything unexpected happens while attempting pagination, fallback to infinite scroll
        pass

    # Fallback: infinite scroll - scroll to bottom and wait for more content to load
    try:
        # Try repeatedly to ensure lazy-loaded content has a chance to load
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        new_height = await page.evaluate("() => document.body.scrollHeight")
        # If the page height increased, assume new content loaded; otherwise a single scroll attempt was made
        if new_height > previous_height:
            return
        # Do a couple more gentle scrolls in case of slow loading
        for _ in range(2):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(2000)
    except Exception:
        # swallow exceptions to avoid breaking the overall scraping loop
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
                    # Create a dedupe key based on title + url (date may vary)
                    key = (item.get('title'), item.get('url'))
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