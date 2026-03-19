"""
Articles Scraper for Mary Spence

Generated at: 2026-03-19 15:15:11
Target URL: https://maryspence.org/news-events/
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

base_url = 'https://maryspence.org/news-events/'

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

    # Use container selectors observed on the page: .ctaStory and .featuredGrantee
    containers = await page.query_selector_all('div.ctaStory, div.featuredGrantee')

    for container in containers:
        try:
            # Prefer title link from known title selectors inside the container
            title_el = await container.query_selector('h3.featuredStoryTitle a, .featuredGranteeContent h2 a')
            # Fallback: sometimes an <a.readMore> points to article as well
            readmore_el = await container.query_selector('a.readMore')

            # Extract URL: first try title link, then readMore
            url = None
            if title_el:
                href = await title_el.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())
            if not url and readmore_el:
                href = await readmore_el.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

            # Extract title text using text_content() to avoid hidden/visible issues
            title = None
            if title_el:
                title_text = await title_el.text_content()
                if title_text:
                    title = title_text.strip()

            # Attempt to find a date within common structures (time tag, .date, .posted-on, .entry-date)
            date = None
            date_el = await container.query_selector('time, .date, .posted-on, .entry-date')
            if date_el:
                date_text = await date_el.get_attribute('datetime')
                if not date_text:
                    # fallback to text content
                    date_text = await date_el.text_content()
                if date_text:
                    try:
                        dt = parse(date_text, fuzzy=True)
                        date = dt.date().isoformat()
                    except Exception:
                        date = None

            # Ensure required fields are present; skip if missing essential data
            if not title or not url:
                # skip incomplete items
                continue

            items.append({
                'title': title,
                'date': date,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Robustness: skip problematic container but continue
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
    # Try common "next page" / "more" selectors (prioritize explicit pagination links)
    next_selectors = [
        '.nav-previous a',             # observed: « More Stories
        '#pagination .nav-previous a', # observed alternative
        'a[rel="next"]',               # semantic next link
        'a.next',                      # common class
        'a.load-more',                 # possible load more button
        'button.load-more',            # possible button variant
    ]

    for sel in next_selectors:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            # Prefer href navigation if available
            href = await el.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(base_url, href.strip())
                try:
                    await page.goto(next_url)
                    # wait for navigation to settle
                    await page.wait_for_load_state('load')
                    await asyncio.sleep(1)
                    return
                except Exception:
                    # If direct navigation fails, try clicking
                    try:
                        await el.scroll_into_view_if_needed()
                        await el.click()
                        await page.wait_for_load_state('load')
                        await asyncio.sleep(1)
                        return
                    except Exception:
                        # fallback to continue trying other selectors
                        continue
            else:
                # If no href, attempt to click (for buttons)
                try:
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    # give time for content to load
                    await asyncio.sleep(2)
                    return
                except Exception:
                    continue
        except Exception:
            continue

    # Fallback: infinite scroll behaviour
    # Scroll to bottom repeatedly and wait for content to load
    try:
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        # perform a few scroll steps to try and trigger lazy load
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
        new_height = await page.evaluate("() => document.body.scrollHeight")

        # If height increased, assume new content loaded; otherwise no-op
        if new_height > previous_height:
            return
        # As a final resort, wait a bit to allow any JS to fetch content
        await asyncio.sleep(1)
    except Exception:
        # If anything goes wrong, don't raise — just return to allow loop to exit if no new items
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