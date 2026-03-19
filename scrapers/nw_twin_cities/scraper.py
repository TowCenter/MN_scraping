import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://nwtwincities.com/category/local-government/'

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
        - date: Publication date in YYYY-MM-DD format (or None)
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Find all article containers
    article_handles = await page.query_selector_all("article.ultp-block-item")
    for article in article_handles:
        try:
            # Title and URL: prefer the title anchor
            title_anchor = await article.query_selector(".ultp-block-title a")
            if not title_anchor:
                # fallback to any anchor inside the title element
                title_anchor = await article.query_selector("h3.ultp-block-title a, .ultp-block-title a")
            if not title_anchor:
                # if there's no title anchor, skip this item (title is required)
                continue

            title_text = (await title_anchor.text_content() or "").strip()
            if not title_text:
                continue

            href = await title_anchor.get_attribute("href")
            url = urllib.parse.urljoin(base_url, href) if href else None

            # Date: use the span.ultp-block-date inside the article meta if present
            date_el = await article.query_selector(".ultp-block-meta .ultp-block-date, span.ultp-block-date")
            date_value = None
            if date_el:
                raw_date = (await date_el.text_content() or "").strip()
                if raw_date:
                    try:
                        # Parse and normalize to YYYY-MM-DD
                        date_parsed = parse(raw_date, fuzzy=True)
                        date_value = date_parsed.date().isoformat()
                    except Exception:
                        date_value = None

            item = {
                'title': title_text,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)
        except Exception:
            # Skip malformed article entries but continue processing others
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scrolls load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    try:
        # Try common "next" pagination link first
        next_selector = "ul.ultp-pagination li.ultp-next-page-numbers a, a.ultp-next-page, a[rel='next']"
        next_link = await page.query_selector(next_selector)

        # Try "load more" style buttons/links as second option
        if not next_link:
            next_link = await page.query_selector("a.ultp-load-more, button.ultp-load-more, a.load-more, button.load-more")

        if next_link:
            # If the link has an href, navigate directly to the href (more reliable)
            href = await next_link.get_attribute("href")
            if href:
                next_url = urllib.parse.urljoin(base_url, href)
                try:
                    await page.goto(next_url)
                    await page.wait_for_load_state("load")
                    return
                except Exception:
                    # If navigation failed (maybe it's AJAX), try clicking instead
                    pass

            # Try clicking the element (useful for JS-driven load more)
            try:
                await next_link.scroll_into_view_if_needed()
                await next_link.click()
                # wait a short time for new content to load
                await page.wait_for_timeout(2000)
                # also wait for network to be idle (best-effort)
                try:
                    await page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
                return
            except Exception:
                # If click fails, fall back to infinite scroll below
                pass

        # Fallback: infinite scroll - attempt to load more content by scrolling
        previous_count = await page.evaluate("() => document.querySelectorAll('article.ultp-block-item').length")
        # perform a few scroll attempts to trigger lazy loading
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            new_count = await page.evaluate("() => document.querySelectorAll('article.ultp-block-item').length")
            if new_count > previous_count:
                # content loaded
                return
            previous_count = new_count

        # final short wait as a last resort
        await page.wait_for_timeout(1000)

    except Exception:
        # swallow any exceptions to avoid stopping the overall scraping loop
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
                    # Create a stable key ignoring None values ordering
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