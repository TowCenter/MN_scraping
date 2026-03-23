"""
Articles Scraper for Newsweek Minneapolis

Target URL: https://search.newsweek.com/?q=minneapolis&sort=date
Content type: articles
Fields: title, date, url

Uses Google News RSS to reliably discover articles about Minneapolis.
"""

import json
import os
import asyncio
import re
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

base_url = 'https://search.newsweek.com/?q=minneapolis&sort=date'

SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

GOOGLE_NEWS_RSS_URL = (
    "https://news.google.com/rss/search?"
    "q=site:newsweek.com+minneapolis&hl=en-US&gl=US&ceid=US:en"
)

SITE_DOMAIN = "newsweek.com"

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


def _resolve_google_news_url(google_url):
    """Follow Google News redirect to get the real article URL."""
    try:
        req = urllib.request.Request(google_url, headers={"User-Agent": USER_AGENT}, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as resp:
            final_url = resp.url
            if SITE_DOMAIN in final_url:
                return final_url
    except Exception:
        pass
    return google_url


def _parse_items(root):
    """Parse RSS XML into article dicts."""
    items = []
    seen = set()

    # Pattern to strip " - <source name>" suffix from Google News titles
    suffix_pattern = re.compile(r'\s*-\s*(?:' + re.escape(SITE_DOMAIN).replace(r'\.',  r'[.]?') + r'|[A-Za-z]{2,30})\s*$', re.I)

    for item in root.iter("item"):
        try:
            title_el = item.find("title")
            link_el = item.find("link")
            pub_date_el = item.find("pubDate")

            if title_el is None or link_el is None:
                continue

            title = (title_el.text or "").strip()
            # Remove Google News source suffix
            title = suffix_pattern.sub('', title).strip()

            if not title:
                continue

            google_url = (link_el.text or "").strip()
            if not google_url:
                continue

            url = _resolve_google_news_url(google_url)

            if url in seen:
                continue
            seen.add(url)

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
    """Fetch articles from Google News RSS feed."""
    root = _fetch_rss(GOOGLE_NEWS_RSS_URL)
    return _parse_items(root)


async def get_all_articles(base_url=base_url, max_pages=100):
    """Google News RSS returns up to ~100 results in a single feed."""
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
