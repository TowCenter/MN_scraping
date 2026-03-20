import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API (optional; failure handled)
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.thegatewaypundit.com/?s=minneapolis'

# Scraper module path for tracking the source of scraped data
try:
    SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])
except Exception:
    SCRAPER_MODULE_PATH = "gatewaypundit.search"

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Use headless to be CI-friendly; allow overriding via environment if needed.
        headless = os.environ.get("HEADLESS", "1") != "0"
        self.browser = await self.playwright.chromium.launch(headless=headless)
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            await self.playwright.stop()
        except Exception:
            pass

async def _safe_text(element):
    """Return element text_content() stripped or None if element is falsy."""
    if not element:
        return None
    try:
        txt = await element.text_content()
        if txt:
            return txt.strip()
    except Exception:
        return None
    return None

async def _safe_attr(element, name):
    """Return attribute value or None safely."""
    if not element:
        return None
    try:
        return await element.get_attribute(name)
    except Exception:
        return None

async def scrape_page(page):
    """
    Extract article data from the current page.
    """
    items = []
    seen_hrefs = set()

    # Wait briefly for likely result containers to appear
    try:
        await page.wait_for_load_state('networkidle', timeout=5000)
    except Exception:
        # continue even if networkidle doesn't occur
        pass

    # Try a prioritized list of selectors that match Gateway Pundit search results
    container_selectors = [
        "article",
        ".td_module_wrap",
        ".td-module",
        ".td_block_inner .td-module-container",
        ".search-result",
        ".type-post",
        ".post",
        ".td-block-span6",
        ".td-block-span4",
    ]

    containers = []
    for sel in container_selectors:
        try:
            found = await page.query_selector_all(sel)
        except Exception:
            found = []
        for f in found:
            if f not in containers:
                containers.append(f)

    # Common title selectors to try within containers
    title_selectors = [
        ".td-module-title a",
        ".entry-title a",
        "h1 a",
        "h2 a",
        "h3 a",
        "h4 a",
        ".post-title a",
        "a"
    ]

    # If we found container elements, parse them
    if containers:
        for container in containers:
            try:
                title = None
                url = None
                # find title anchor
                title_el = None
                for ts in title_selectors:
                    try:
                        title_el = await container.query_selector(ts)
                    except Exception:
                        title_el = None
                    if title_el:
                        t = await _safe_text(title_el)
                        if t and len(t) > 2:
                            title = t
                        # get href
                        href = await _safe_attr(title_el, "href")
                        if href:
                            url = urllib.parse.urljoin(page.url, href)
                        # if we have a reasonable title and url, stop
                        if title and url:
                            break
                        # otherwise continue searching
                # additional fallback: anchor inside thumbnail
                if not url:
                    try:
                        thumb_a = await container.query_selector(".td-module-thumb a, .thumb a")
                        if thumb_a:
                            href = await _safe_attr(thumb_a, "href")
                            if href:
                                url = urllib.parse.urljoin(page.url, href)
                            if not title:
                                title = await _safe_text(thumb_a)
                    except Exception:
                        pass

                # Date detection
                date = None
                date_selectors = [
                    "time[datetime]",
                    "time",
                    ".entry-date",
                    ".td-module-date",
                    ".post-date",
                    ".date",
                    ".meta time",
                ]
                date_found = None
                for ds in date_selectors:
                    try:
                        d_el = await container.query_selector(ds)
                    except Exception:
                        d_el = None
                    if d_el:
                        dt_attr = await _safe_attr(d_el, "datetime")
                        if dt_attr:
                            date_found = dt_attr
                            break
                        txt = await _safe_text(d_el)
                        if txt:
                            date_found = txt
                            break
                if date_found:
                    try:
                        parsed = parse(date_found, fuzzy=True)
                        date = parsed.date().isoformat()
                    except Exception:
                        date = None

                # If title missing but url exists, try deriving title from the linked page anchor text or image alt
                if not title and url:
                    # try to extract last path segment as fallback label
                    parsed_url = urllib.parse.urlparse(url)
                    fallback = os.path.basename(parsed_url.path).replace('-', ' ').replace('_', ' ').strip()
                    if fallback:
                        title = fallback

                if url:
                    # normalize and deduplicate
                    if url in seen_hrefs:
                        continue
                    seen_hrefs.add(url)
                else:
                    continue
                items.append({
                    "title": title if title else None,
                    "date": date,
                    "url": url,
                    "scraper": SCRAPER_MODULE_PATH
                })
            except Exception:
                continue
        return items

    # If no containers, try broad anchor-based extraction on the page
    anchors = []
    try:
        anchors = await page.query_selector_all("a[href*='thegatewaypundit.com'], a[href*='/20'], a[href*='/202'], a[href*='/2019'], a[href*='/2020'], a[href*='/2021'], a[href*='/2022'], a[href*='/2023'], a[href*='/2024']")
    except Exception:
        anchors = []

    for a in anchors:
        try:
            href = await _safe_attr(a, "href")
            if not href:
                continue
            url = urllib.parse.urljoin(page.url, href)
            if url in seen_hrefs:
                continue
            title = await _safe_text(a)
            if not title or len(title) < 3:
                # try img alt
                try:
                    img = await a.query_selector("img")
                    if img:
                        alt = await _safe_attr(img, "alt")
                        if alt and len(alt) > 2:
                            title = alt
                except Exception:
                    pass
            if not title:
                # try to get surrounding text (parent)
                try:
                    parent = await a.evaluate_handle("el => el.parentElement")
                    if parent:
                        pt = await _safe_text(parent)
                        if pt and len(pt) > 3:
                            title = pt
                except Exception:
                    pass
            if not title:
                # fallback to path basename
                parsed_url = urllib.parse.urlparse(url)
                fallback = os.path.basename(parsed_url.path).replace('-', ' ').replace('_', ' ').strip()
                if fallback:
                    title = fallback
            if not title:
                continue
            seen_hrefs.add(url)
            items.append({
                "title": title,
                "date": None,
                "url": url,
                "scraper": SCRAPER_MODULE_PATH
            })
        except Exception:
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    """
    next_selectors = [
        'a[rel="next"]',
        'a.next',
        '.pagination a.next',
        '.nav-previous a',
        '.nav-next a',
        'a.load-more',
        'button.load-more',
        'button[aria-label*="load"]',
        'a[aria-label*="Next"]',
        'a[aria-label*="next"]'
    ]

    for sel in next_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                href = await _safe_attr(el, "href")
                if href:
                    next_url = urllib.parse.urljoin(page.url, href)
                    try:
                        await page.goto(next_url)
                        await page.wait_for_load_state('networkidle')
                        await page.wait_for_timeout(800)
                        return
                    except Exception:
                        pass
                try:
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    await page.wait_for_load_state('networkidle', timeout=5000)
                    await page.wait_for_timeout(1000)
                    return
                except Exception:
                    continue
        except Exception:
            continue

    # Fallback: infinite scroll approach
    try:
        prev_height = await page.evaluate("() => document.body.scrollHeight")
    except Exception:
        prev_height = 0
    for _ in range(6):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height > prev_height:
                await page.wait_for_timeout(1200)
                return
            prev_height = new_height
        except Exception:
            await page.wait_for_timeout(1000)
    await page.wait_for_timeout(2000)
    return

async def get_first_page(base_url=base_url):
    """Fetch only the first page of articles."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
        # Apply stealth if available; swallow errors so scraper still runs
        try:
            stealth = Stealth()
            if hasattr(stealth, "apply_stealth_async"):
                try:
                    await stealth.apply_stealth_async(page)
                except Exception:
                    # older/newer APIs or failures are ignored
                    pass
            elif hasattr(stealth, "apply"):
                try:
                    await stealth.apply(page)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            await page.goto(base_url, timeout=15000)
        except Exception:
            try:
                await page.goto(base_url)
            except Exception:
                pass

        try:
            await page.wait_for_load_state('networkidle', timeout=5000)
        except Exception:
            await page.wait_for_timeout(1000)

        items = await scrape_page(page)
        await page.close()
        return items

async def get_all_articles(base_url=base_url, max_pages=100):
    """Fetch all articles from all pages."""
    async with PlaywrightContext() as context:
        items = []
        seen = set()
        page = await context.new_page()
        # stealth best-effort
        try:
            stealth = Stealth()
            if hasattr(stealth, "apply_stealth_async"):
                try:
                    await stealth.apply_stealth_async(page)
                except Exception:
                    pass
            elif hasattr(stealth, "apply"):
                try:
                    await stealth.apply(page)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            await page.goto(base_url, timeout=15000)
        except Exception:
            try:
                await page.goto(base_url)
            except Exception:
                pass

        try:
            await page.wait_for_load_state('networkidle', timeout=5000)
        except Exception:
            await page.wait_for_timeout(1000)

        page_count = 0
        item_count = 0

        try:
            while page_count < max_pages:
                page_items = await scrape_page(page)
                for item in page_items:
                    # use URL as primary dedupe key
                    key = item.get("url")
                    if not key:
                        # fallback to tuple of values
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

        except Exception:
            # swallow and return what we have
            pass

        await page.close()
        return items

async def main():
    """Main execution function."""
    all_items = await get_all_articles()

    # Save results to JSON
    try:
        result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    except Exception:
        result_path = os.path.join(os.getcwd(), 'result.json')
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {result_path}")

if __name__ == "__main__":
    asyncio.run(main())