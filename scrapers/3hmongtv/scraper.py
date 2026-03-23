import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://hbctv.net/category/3hmongtv-news/'

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
        - date: Publication date in YYYY-MM-DD format (optional — None if not found)
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Strategy:
    # - Select anchors that use the common class seen on the site: a.text-navy-200
    # - Filter out social/external links (facebook, twitter, youtube, mailto, tel, etc.)
    # - Use the anchor's surrounding container to look for nearby date/time elements
    # - Parse dates to ISO YYYY-MM-DD when possible, otherwise None

    # Get all anchors that match the class used in examples
    anchors = await page.query_selector_all("a.text-navy-200")

    # Common external domains / short labels to ignore (social icons, nav shortcuts)
    skip_domains = ("facebook.com", "twitter.com", "youtube.com", "instagram.com", "linkedin.com")
    skip_texts = {"facebook", "twitter", "youtube", "instagram", "rss", "linkedin"}

    for a in anchors:
        try:
            href = await a.get_attribute('href')
            if not href:
                continue

            # Resolve relative URLs
            href = urllib.parse.urljoin(page.url, href)

            # Filter out obvious external/social links
            lower_href = href.lower()
            if any(d in lower_href for d in skip_domains) or lower_href.startswith(("mailto:", "tel:")):
                continue

            # Extract text content for title; use text_content() to handle hidden text as requested
            raw_text = await a.text_content()
            if not raw_text:
                continue
            title = raw_text.strip()
            if not title:
                continue

            # Skip very short labels or known social words
            if len(title) < 4 and title.lower() in skip_texts:
                continue
            # Also skip titles that are very short (likely icons/labels)
            if len(title) <= 2:
                continue

            # Attempt to find a nearby date within a reasonable ancestor
            # Execute JS in page context to search for time/date within closest article-like ancestor
            date_str = await a.evaluate(
                """(anchor) => {
                    // Find the closest semantic container that might contain metadata
                    const ancestor = anchor.closest('article, li, .post, .entry, .card, .grid-item, .space-y-2, .space-y-4') || anchor.parentElement;
                    if (!ancestor) return null;

                    // Common date selectors
                    const selectors = [
                        'time[datetime]',
                        'time',
                        '[datetime]',
                        'span[class*=date]',
                        '[itemprop*=datePublished]',
                        'meta[itemprop*=datePublished]',
                        'meta[property=\"article:published_time\"]'
                    ];

                    for (const sel of selectors) {
                        const el = ancestor.querySelector(sel);
                        if (el) {
                            // Pull datetime/content attributes first when available
                            if (el.getAttribute && el.getAttribute('datetime')) return el.getAttribute('datetime');
                            if (el.getAttribute && el.getAttribute('content')) return el.getAttribute('content');
                            if (el.textContent) return el.textContent.trim();
                        }
                    }
                    return null;
                }"""
            )

            # Normalize date to YYYY-MM-DD if possible
            date_iso = None
            if date_str:
                try:
                    # parse can handle many formats and ISO strings
                    dt = parse(date_str, fuzzy=True)
                    date_iso = dt.date().isoformat()
                except Exception:
                    # If parsing fails, leave as None per requirements
                    date_iso = None

            item = {
                'title': title,
                'date': date_iso,
                'url': href,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)

        except Exception:
            # Be resilient to individual element errors; skip problematic anchors
            continue

    # If no items found via the class-based anchors, try a fallback:
    # anchors inside list items (ul.space-y-2 li a), which look like internal show/article links
    if not items:
        fallback_anchors = await page.query_selector_all("ul.space-y-2 li a")
        for a in fallback_anchors:
            try:
                href = await a.get_attribute('href')
                if not href:
                    continue
                href = urllib.parse.urljoin(page.url, href)
                lower_href = href.lower()
                if lower_href.startswith(("mailto:", "tel:")):
                    continue
                raw_text = await a.text_content()
                if not raw_text:
                    continue
                title = raw_text.strip()
                if not title:
                    continue

                # Attempt date near this anchor similar to above
                date_str = await a.evaluate(
                    """(anchor) => {
                        const ancestor = anchor.closest('li, article, .post, .entry') || anchor.parentElement;
                        if (!ancestor) return null;
                        const el = ancestor.querySelector('time[datetime], time, [datetime], span[class*=date]');
                        if (!el) return null;
                        if (el.getAttribute && el.getAttribute('datetime')) return el.getAttribute('datetime');
                        if (el.getAttribute && el.getAttribute('content')) return el.getAttribute('content');
                        return el.textContent ? el.textContent.trim() : null;
                    }"""
                )
                date_iso = None
                if date_str:
                    try:
                        dt = parse(date_str, fuzzy=True)
                        date_iso = dt.date().isoformat()
                    except Exception:
                        date_iso = None

                item = {
                    'title': title,
                    'date': date_iso,
                    'url': href,
                    'scraper': SCRAPER_MODULE_PATH,
                }
                items.append(item)
            except Exception:
                continue

    # Final safety: deduplicate by (title, url)
    seen = set()
    unique_items = []
    for it in items:
        key = (it.get('title'), it.get('url'))
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(it)

    return unique_items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Try common "next" / "load more" selectors first
    next_selectors = [
        'a[rel="next"]',
        'a:has-text("Next")',
        'a:has-text("next")',
        'a:has-text("More")',
        'a:has-text("More Posts")',
        'button:has-text("Load more")',
        'button:has-text("Load More")',
        'button.load-more',
        'div.load-more button',
    ]

    for sel in next_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                # If it's a link with href, navigate to it
                href = await el.get_attribute('href')
                if href:
                    next_url = urllib.parse.urljoin(page.url, href)
                    try:
                        await page.goto(next_url)
                        return
                    except Exception:
                        # If navigation fails, attempt to click instead
                        pass

                # If clickable element (button or link without href), attempt to click it
                try:
                    # Scroll into view and click
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    # Wait a short time for new content to load
                    await page.wait_for_timeout(2000)
                    return
                except Exception:
                    # If click fails, continue to next selector
                    continue
        except Exception:
            continue

    # No explicit pagination found — fallback to infinite scroll behavior.
    # Perform a series of incremental scrolls to trigger lazy-loading.
    try:
        # Number of incremental scroll attempts per advance
        for _ in range(3):
            await page.evaluate("""() => {
                window.scrollBy({ top: window.innerHeight * 3, left: 0, behavior: 'smooth' });
            }""")
            # Wait for potential network requests / rendering
            await page.wait_for_timeout(1500)
        # Final scroll to bottom
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)
    except Exception:
        # If anything goes wrong in scrolling, just wait briefly as a last resort
        await page.wait_for_timeout(2000)

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