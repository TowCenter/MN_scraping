import json
import os
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.startribune.com/news-politics/twin-cities'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch in headless mode by default
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
        - url: Absolute URL to the full article
        - scraper: module path for traceability
    """
    items = []

    # Find headline elements which reliably identify article entries
    # Using h3.rt-Heading as observed in page examples
    headline_handles = await page.query_selector_all("h3.rt-Heading")

    for h in headline_handles:
        try:
            # Get title text using textContent semantics via evaluate (robust regardless of visibility)
            title = await h.evaluate("el => el.textContent && el.textContent.trim() ? el.textContent.trim() : null")
            if not title:
                continue

            # Attempt to find a URL related to this headline by:
            # 1) closest ancestor <a>
            # 2) first descendant <a> within reasonable ancestor search
            href = await h.evaluate("""
                (el) => {
                    // 1. nearest ancestor anchor
                    let a = el.closest('a');
                    if (a && a.getAttribute('href')) return a.getAttribute('href');
                    // 2. search within up to 8 ancestor levels for a descendant anchor with href
                    let p = el;
                    for (let i = 0; i < 8 && p; i++) {
                        let link = p.querySelector('a[href]');
                        if (link) return link.getAttribute('href');
                        p = p.parentElement;
                    }
                    return null;
                }
            """)

            if not href:
                # Could not find a URL for this headline; skip this item
                continue

            # Normalize to absolute URL
            url = urllib.parse.urljoin(page.url or base_url, href)

            # Attempt to locate a date string near the headline:
            # search up the DOM tree up to several levels for known date selectors
            date_text = await h.evaluate("""
                (el) => {
                    let selectors = ['span.font-utility-label-reg-caps-02', 'div.font-utility-label-reg-caps-03', 'time', 'span.rt-Text'];
                    let p = el;
                    for (let depth = 0; depth < 8 && p; depth++) {
                        for (let s of selectors) {
                            let found = p.querySelector(s);
                            if (found && found.textContent && found.textContent.trim()) {
                                return found.textContent.trim();
                            }
                        }
                        p = p.parentElement;
                    }
                    return null;
                }
            """)

            date_iso = None
            if date_text:
                # Try to parse human readable date into YYYY-MM-DD
                try:
                    dt = parse(date_text, fuzzy=True)
                    date_iso = dt.date().isoformat()
                except Exception:
                    date_iso = None

            items.append({
                'title': title,
                'date': date_iso,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Skip problematic headline elements but continue scraping others
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scrolls load more button into view if not visible.
    Falls back to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Helper to get current article count reliably
    async def get_heading_count():
        return await page.evaluate("() => document.querySelectorAll('h3.rt-Heading').length")

    # Primary approach: look for a "Load More" button (various selectors) and click it,
    # then wait until the number of headings increases.
    try:
        before = await get_heading_count()

        # Build candidate element list: prioritize observed specific selector, then data-testid, then any button/a
        candidates = []

        # Try specific observed class first
        try:
            el = await page.query_selector("button.Button_secondary-default-mode__yzoDW")
            if el:
                candidates.append(el)
        except Exception:
            pass

        # Try data-testid button(s)
        try:
            els = await page.query_selector_all("button[data-testid='text-button']")
            for e in els:
                candidates.append(e)
        except Exception:
            pass

        # Add all buttons and anchors as broader fallback to search their text content
        try:
            els = await page.query_selector_all("button, a")
            for e in els:
                candidates.append(e)
        except Exception:
            pass

        # Deduplicate handles while preserving order
        seen_handles = set()
        unique_candidates = []
        for h in candidates:
            try:
                hid = await h.evaluate("e => e.outerHTML")
            except Exception:
                hid = None
            if hid and hid not in seen_handles:
                seen_handles.add(hid)
                unique_candidates.append(h)

        for el in unique_candidates:
            try:
                txt = await el.evaluate("e => (e.textContent || '').trim().toLowerCase()")
            except Exception:
                txt = ''
            if not txt:
                # Also check for any descendant text nodes (safer)
                try:
                    txt = await el.evaluate("e => (e.innerText || '').trim().toLowerCase()")
                except Exception:
                    txt = ''
            if 'load more' in txt or txt == 'more' or 'show more' in txt or 'view more' in txt:
                try:
                    await el.scroll_into_view_if_needed()
                except Exception:
                    pass
                # Try clicking; if it triggers XHR and DOM update, wait for increased headings
                clicked = False
                try:
                    await el.click()
                    clicked = True
                except PlaywrightTimeoutError:
                    try:
                        await el.evaluate("e => e.click()")
                        clicked = True
                    except Exception:
                        clicked = False
                except Exception:
                    # fallback to JS click
                    try:
                        await el.evaluate("e => e.click()")
                        clicked = True
                    except Exception:
                        clicked = False

                if clicked:
                    # Wait for headings count to increase, which indicates new content loaded
                    try:
                        await page.wait_for_function(
                            f"() => document.querySelectorAll('h3.rt-Heading').length > {before}",
                            timeout=10000
                        )
                        return
                    except PlaywrightTimeoutError:
                        # allow fallback attempts below
                        pass
                    except Exception:
                        pass

        # Secondary: look for any anchor with rel=next or a pagination link text
        try:
            next_link = await page.query_selector("a[rel='next']")
            if next_link:
                href = await next_link.get_attribute('href')
                if href:
                    next_url = urllib.parse.urljoin(page.url or base_url, href)
                    try:
                        # navigate and wait for load
                        await page.goto(next_url)
                        return
                    except Exception:
                        pass
        except Exception:
            pass

        # Tertiary: as a last attempt, find any anchor/button containing the word "more" even if not exact
        try:
            possible = await page.query_selector_all("a, button")
            for el in possible:
                try:
                    txt = await el.evaluate("e => (e.textContent || '').trim().toLowerCase()")
                    if not txt:
                        txt = await el.evaluate("e => (e.innerText || '').trim().toLowerCase()")
                    if not txt:
                        continue
                    if 'more' in txt:
                        try:
                            await el.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        try:
                            await el.click()
                        except PlaywrightTimeoutError:
                            try:
                                await el.evaluate("e => e.click()")
                            except Exception:
                                pass
                        except Exception:
                            try:
                                await el.evaluate("e => e.click()")
                            except Exception:
                                pass

                        # wait for DOM update
                        try:
                            await page.wait_for_function(
                                f"() => document.querySelectorAll('h3.rt-Heading').length > {before}",
                                timeout=10000
                            )
                            return
                        except PlaywrightTimeoutError:
                            continue
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            pass

    except Exception:
        # If anything unexpected happens, fall back to infinite scroll below
        pass

    # Fallback: infinite scroll - perform a scroll to bottom and wait for new content
    try:
        before = await get_heading_count()
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)  # wait for lazy-load
        try:
            await page.wait_for_function(
                f"() => document.querySelectorAll('h3.rt-Heading').length > {before}",
                timeout=8000
            )
            return
        except PlaywrightTimeoutError:
            # Try a couple more scroll attempts
            attempts = 3
            for _ in range(attempts):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
                after = await get_heading_count()
                if after > before:
                    return
    except Exception:
        # Nothing else to do
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
                    # create a de-duplication key based on title+url+date (None allowed)
                    key = (item.get('title'), item.get('url'), item.get('date'))
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