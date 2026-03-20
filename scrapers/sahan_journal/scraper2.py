"""
Articles Scraper for Sahan Journal

Generated at: 2026-03-20 13:17:41
Target URL: https://sahanjournal.com/archive/
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

base_url = 'https://sahanjournal.com/archive/'

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

    # Use a general article container selector that matches posts
    article_selectors = ['article.post', 'article.post.type-post', 'article.hentry']
    articles = []
    for sel in article_selectors:
        try:
            nodes = await page.query_selector_all(sel)
            if nodes:
                # Prefer the most specific selector result set; but collect all unique nodes
                articles.extend(nodes)
        except Exception:
            continue

    # De-duplicate handles by their outerHTML (some selectors may overlap)
    seen_html = set()
    unique_articles = []
    for a in articles:
        try:
            html = await a.get_attribute('outerHTML')
        except Exception:
            html = None
        if html and html not in seen_html:
            seen_html.add(html)
            unique_articles.append(a)

    # If nothing found with the above, try a broad fallback of article elements
    if not unique_articles:
        unique_articles = await page.query_selector_all('article')

    for a in unique_articles:
        title = None
        url = None
        date_str = None
        date_val = None

        # Title and url: prefer h2.entry-title a (headline link)
        try:
            title_anchor = await a.query_selector('h2.entry-title a')
            if title_anchor:
                title_txt = await title_anchor.text_content()
                title = title_txt.strip() if title_txt else None
                href = await title_anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(page.url, href)
        except Exception:
            title = title or None
            url = url or None

        # If title not found, try other anchors inside the article (thumbnail link)
        if not title:
            try:
                thumb_anchor = await a.query_selector('a.post-thumbnail-inner')
                if thumb_anchor:
                    # Title may be in the alt/title of image or link text
                    href = await thumb_anchor.get_attribute('href')
                    if href and not url:
                        url = urllib.parse.urljoin(page.url, href)
                    # try to get alt or image title
                    img = await thumb_anchor.query_selector('img')
                    if img:
                        alt = await img.get_attribute('alt')
                        if alt:
                            title = alt.strip()
                        else:
                            img_title = await img.get_attribute('data-image-title')
                            if img_title:
                                title = img_title.strip()
            except Exception:
                pass

        # Date: try time.entry-date.published (datetime attr) then .posted-on time
        try:
            time_el = await a.query_selector('time.entry-date.published')
            if not time_el:
                # some markup includes time inside .posted-on
                time_el = await a.query_selector('.posted-on time[datetime]')
            if not time_el:
                # fallback to any time element inside the article
                time_el = await a.query_selector('time[datetime], time')
            if time_el:
                dt_attr = await time_el.get_attribute('datetime')
                if dt_attr:
                    # Parse ISO or partial ISO datetime
                    try:
                        parsed = parse(dt_attr)
                        date_val = parsed.date().isoformat()
                    except Exception:
                        # fallback to parsing visible text
                        txt = await time_el.text_content()
                        if txt:
                            try:
                                parsed = parse(txt, fuzzy=True)
                                date_val = parsed.date().isoformat()
                            except Exception:
                                date_val = None
                else:
                    # no datetime attribute: try to parse visible text
                    txt = await time_el.text_content()
                    if txt:
                        try:
                            parsed = parse(txt, fuzzy=True)
                            date_val = parsed.date().isoformat()
                        except Exception:
                            date_val = None
        except Exception:
            date_val = None

        # Ensure required fields: title and url; if url missing but title anchor existed earlier maybe not
        # As another fallback for URL, if still missing try to find any first anchor in article with href
        if not url:
            try:
                any_anchor = await a.query_selector('a[href]')
                if any_anchor:
                    href = await any_anchor.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin(page.url, href)
            except Exception:
                url = None

        # Normalize empty strings to None
        if title:
            title = title.strip() if isinstance(title, str) else title
            if title == '':
                title = None
        if url:
            url = url.strip()
            if url == '':
                url = None

        items.append({
            'title': title,
            'date': date_val if date_val else None,
            'url': url,
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
    # Strategy:
    # 1. Look for explicit "next" pagination link (common WP class: a.next.page-numbers)
    # 2. If found, navigate to its href (prefer navigation over click to avoid JS issues)
    # 3. If not found, look for generic pagination nav and try to find the link labeled "Older posts" or a link after current
    # 4. If no pagination links found, perform infinite scroll fallback: scroll to bottom and wait for new content

    try:
        # Primary next link selector (given in examples)
        next_link = await page.query_selector('a.next.page-numbers')
        if next_link:
            href = await next_link.get_attribute('href')
            if href:
                target = urllib.parse.urljoin(page.url, href)
                try:
                    await page.goto(target)
                    # wait for main content to load; use networkidle/load
                    await page.wait_for_load_state('load')
                except Exception:
                    # fallback to clicking if navigation fails
                    try:
                        await next_link.click()
                        await page.wait_for_load_state('load')
                    except Exception:
                        # best-effort: wait briefly
                        await page.wait_for_timeout(2000)
                return

        # Secondary: pagination nav - try to find an 'a.page-numbers' that is not the current one and appears after current
        nav = await page.query_selector('nav.navigation.pagination, .navigation.pagination, nav[aria-label="Posts pagination"]')
        if nav:
            # try to find anchor with class page-numbers and aria-label containing "Page" or that has text "Older posts"
            candidate = await nav.query_selector('a.page-numbers[href].next, a.page-numbers[href][aria-label*="Page"], a.page-numbers[href] .nav-next-text')
            if candidate:
                # candidate might be an inner element; get nearest anchor
                # ensure we get an anchor element
                candidate_anchor = candidate
                # if the matched node is not anchor, try to find the ancestor anchor
                name = await candidate_anchor.evaluate("(node) => node.tagName.toLowerCase()")
                if name != 'a':
                    candidate_anchor = await candidate_anchor.query_selector('xpath=ancestor::a[1]')
                if candidate_anchor:
                    href = await candidate_anchor.get_attribute('href')
                    if href:
                        target = urllib.parse.urljoin(page.url, href)
                        try:
                            await page.goto(target)
                            await page.wait_for_load_state('load')
                        except Exception:
                            try:
                                await candidate_anchor.click()
                                await page.wait_for_load_state('load')
                            except Exception:
                                await page.wait_for_timeout(1500)
                        return

        # If we reach here, no explicit next page found -> infinite scroll fallback
        # Scroll to bottom and wait for potential lazy-load or JS appended items
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Give time for content to load
        await page.wait_for_timeout(3000)

    except Exception as e:
        # On any unexpected error, perform safe fallback: scroll and wait
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)
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
                    # create a stable key for deduplication (title + url + date)
                    key = (item.get('title'), item.get('url'), item.get('date'))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                if new_item_count <= item_count:
                    # no new items found on this iteration -> stop to avoid infinite loop
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