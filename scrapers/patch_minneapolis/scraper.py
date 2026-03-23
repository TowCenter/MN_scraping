"""
Articles Scraper for Patch Minneapolis

Generated at: 2026-03-20 15:03:58
Target URL: https://patch.com/minnesota/minneapolis
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

base_url = 'https://patch.com/minnesota/minneapolis'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch browser (headless by default)
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
        - date: Publication date in YYYY-MM-DD format (optional — None if not found)
        - url: Absolute link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Choose a tolerant article container selector: look for article elements that appear to be cards.
    # Use a generic article selector and then look inside each for an h2 > a or a.thumbnail/title link.
    article_nodes = await page.query_selector_all("article")

    for article in article_nodes:
        try:
            # Title: prefer h2 > a, otherwise any anchor with a title-like role inside the article
            title_el = await article.query_selector("h2 a")
            if not title_el:
                # fallback to anchor with thumbnail or title link
                title_el = await article.query_selector("a[href][title]")
            if not title_el:
                # no usable title anchor found; skip this article
                continue

            title_text = (await title_el.text_content() or "").strip()
            if not title_text:
                # fallback to title attribute
                title_text = (await title_el.get_attribute("title") or "").strip()
            if not title_text:
                # still empty, skip
                continue

            # URL: resolve relative href to absolute
            href = await title_el.get_attribute("href")
            if not href:
                # skip articles without href
                continue
            url = urllib.parse.urljoin(page.url, href)

            # Date: look for <time datetime="..."> inside the article
            date_value = None
            time_el = await article.query_selector("time[datetime], time")
            if time_el:
                datetime_attr = await time_el.get_attribute("datetime")
                # If datetime attribute exists, parse it; otherwise try text content parse as fallback
                dt_to_parse = datetime_attr or (await time_el.text_content() or "").strip()
                if dt_to_parse:
                    try:
                        parsed = parse(dt_to_parse)
                        # Format as YYYY-MM-DD
                        date_value = parsed.date().isoformat()
                    except Exception:
                        # If parsing fails, leave date_value as None
                        date_value = None

            items.append({
                "title": title_text,
                "date": date_value,
                "url": url,
                "scraper": SCRAPER_MODULE_PATH,
            })
        except Exception:
            # Skip malformed article nodes but continue processing others
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
    try:
        current_url = page.url

        # Candidate selectors, prioritized
        selectors = [
            'a[rel="next"]',
            'a[href*="?page="]',
            'a[href*="page="]',
            'a.styles_Pagination__link__MAljo',
            'a.styles_Section__linkButton__y7Z2i'
        ]

        candidates = []
        seen_hrefs = set()
        for sel in selectors:
            nodes = await page.query_selector_all(sel)
            for n in nodes:
                try:
                    href = await n.get_attribute("href")
                    if not href:
                        continue
                    # dedupe by href
                    if href in seen_hrefs:
                        continue
                    seen_hrefs.add(href)
                    resolved = urllib.parse.urljoin(current_url, href)
                    candidates.append((n, href, resolved))
                except Exception:
                    continue

        # Score candidates and pick best one
        best_candidate = None
        best_score = -1
        for node, href, resolved in candidates:
            try:
                # ignore links that resolve to current url
                if resolved == current_url:
                    continue
                text = (await node.text_content() or "").strip().lower()
                score = 0
                # prefer explicit page parameter links
                if '?page=' in href or 'page=' in href:
                    score += 20
                # textual hints
                if 'next' in text:
                    score += 10
                if 'see more' in text or 'read more' in text or 'more local' in text or 'more' == text:
                    score += 8
                # slightly prefer full path vs relative just '?page='
                if href.startswith('/'):
                    score += 1
                # choose highest scored
                if score > best_score:
                    best_score = score
                    best_candidate = (node, href, resolved)
            except Exception:
                continue

        if best_candidate:
            node, href, resolved_url = best_candidate

            # Ensure element is visible / in view
            try:
                await node.scroll_into_view_if_needed()
            except Exception:
                pass

            # Try click with navigation wait (robust for link clicks)
            try:
                # If clicking causes navigation, wait for it. If it's a regular link, it should navigate.
                await asyncio.gather(
                    page.wait_for_navigation(wait_until="load", timeout=10000),
                    node.click()
                )
                # ensure content loads
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                return
            except Exception:
                # Clicking didn't trigger navigation or timed out; try goto as fallback
                try:
                    await page.goto(resolved_url, wait_until="networkidle", timeout=15000)
                    return
                except Exception:
                    # final fallback: try simple goto without waiting too long
                    try:
                        await page.goto(resolved_url)
                        await page.wait_for_timeout(1000)
                        return
                    except Exception:
                        pass

        # If no suitable candidate or navigation attempts failed, fallback to infinite scroll
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        for _ in range(5):
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == previous_height:
                # small additional wait in case content loads slowly
                await page.wait_for_timeout(1500)
                new_height = await page.evaluate("() => document.body.scrollHeight")
                if new_height == previous_height:
                    break
            previous_height = new_height

    except Exception:
        # On any error, at minimum attempt a basic scroll to trigger dynamic loading
        try:
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
        except Exception:
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
                    # Create a dedupe key from title+url+date where available
                    key = (item.get("title"), item.get("url"), item.get("date"))
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