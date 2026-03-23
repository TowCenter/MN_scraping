"""
Articles Scraper for Breitbart Minneapolis

Target URL: https://www.breitbart.com/search/?s=minneapolis
Content type: articles
Fields: title, date, url

Uses Breitbart's tag RSS feed for reliable article discovery.
"""

import json
import os
import asyncio
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

base_url = 'https://www.breitbart.com/search/?s=minneapolis'

SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

RSS_URLS = [
    "https://www.breitbart.com/tag/minneapolis/feed/",
    "https://www.breitbart.com/tag/minnesota/feed/",
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


def _fetch_rss(url):
    """Fetch RSS feed and return parsed XML root."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return ET.fromstring(resp.read())


def _parse_items(root):
    """Parse RSS XML into article dicts."""
    items = []

    for item in root.iter("item"):
        try:
            title_el = item.find("title")
            link_el = item.find("link")
            pub_date_el = item.find("pubDate")

            if title_el is None or link_el is None:
                continue

            title = (title_el.text or "").strip()
            url = (link_el.text or "").strip()
            if not title or not url:
                continue

            date_str = None
            if pub_date_el is not None and pub_date_el.text:
                try:
                    dt = parsedate_to_datetime(pub_date_el.text.strip())
                    date_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    pass

            items.append({
                "title": title,
                "date": date_str,
                "url": url,
                "scraper": SCRAPER_MODULE_PATH,
            })
        except Exception:
            continue

    return items


async def get_first_page(base_url=base_url):
    """Fetch articles from Breitbart tag RSS feeds."""
    all_items = []
    seen_urls = set()

    for rss_url in RSS_URLS:
        try:
            root = _fetch_rss(rss_url)
            for item in _parse_items(root):
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    all_items.append(item)
        except Exception:
            continue

    return all_items


async def get_all_articles(base_url=base_url, max_pages=100):
    """Breitbart tag feeds return ~48 items each. No pagination available."""
    return await get_first_page(base_url=base_url)


async def main():
    """Main execution function."""
    all_items = await get_all_articles()

    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    with open(result_path, 'w') as f:
        json.dump(all_items, f, indent=2)
    print(f"Results saved to {result_path} ({len(all_items)} articles)")


if __name__ == "__main__":
    asyncio.run(main())
