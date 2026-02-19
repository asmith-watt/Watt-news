"""Content enrichment for candidate articles.

Fetches full content at discovery time so LLMs can outline articles
without requiring a second crawl. Enrichment is non-blocking — failures
are logged in metadata but don't prevent candidate creation.
"""
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from flask import current_app

logger = logging.getLogger(__name__)

# Simple rate limiter for Firecrawl API calls.
# Firecrawl's standard plan allows ~20 req/min; we target ~10/min to stay safe.
_FIRECRAWL_MIN_INTERVAL = 6.0  # seconds between requests
_firecrawl_last_call = 0.0
_firecrawl_lock = threading.Lock()


def _firecrawl_rate_limit():
    """Block until enough time has passed since the last Firecrawl call."""
    global _firecrawl_last_call
    with _firecrawl_lock:
        now = time.monotonic()
        elapsed = now - _firecrawl_last_call
        if elapsed < _FIRECRAWL_MIN_INTERVAL:
            time.sleep(_FIRECRAWL_MIN_INTERVAL - elapsed)
        _firecrawl_last_call = time.monotonic()


def enrich_item(url: str, metadata: dict, source_type: str, source_url: str = None) -> dict:
    """Enrich a candidate item with full content.

    Returns the metadata dict (mutated in place) with enrichment keys added.
    Always returns metadata — never raises.
    """
    try:
        # Data sources are already enriched by Claude
        if source_type == 'Data':
            metadata.setdefault('content_format', 'claude_summary')
            metadata.setdefault('content_source', 'data_scraper')
            return metadata

        # RSS content available for free — promote it
        if 'rss_full_content' in metadata:
            return _promote_rss_content(metadata)

        # YouTube videos — fetch transcript
        if source_type == 'YouTube Keywords':
            return _enrich_youtube_video(url, metadata)

        # All other web sources — Firecrawl scrape
        return _enrich_web_article(url, metadata, source_url=source_url)

    except Exception as e:
        logger.error(f"Enrichment failed for {url}: {e}", exc_info=True)
        metadata['enrichment_failed'] = True
        metadata['enrichment_error'] = str(e)
        return metadata


def _promote_rss_content(metadata: dict) -> dict:
    """Promote RSS full content already captured by the scraper."""
    content = metadata.get('rss_full_content', '')
    metadata['full_content'] = content
    metadata['content_format'] = 'rss_html'
    metadata['content_source'] = 'rss_feed'
    metadata['content_length'] = len(content)
    metadata['enriched_at'] = datetime.now(timezone.utc).isoformat()
    metadata['enrichment_failed'] = False
    metadata['enrichment_error'] = None
    return metadata


_youtube_blocked = False  # circuit breaker: skip after first IP ban


def _enrich_youtube_video(url: str, metadata: dict) -> dict:
    """Fetch YouTube transcript using youtube-transcript-api."""
    global _youtube_blocked
    if _youtube_blocked:
        metadata['enrichment_failed'] = True
        metadata['enrichment_error'] = 'YouTube transcript skipped (IP blocked)'
        return metadata

    video_id = _extract_youtube_video_id(url)
    if not video_id:
        metadata['enrichment_failed'] = True
        metadata['enrichment_error'] = 'Could not extract video ID from URL'
        return metadata

    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id)

        # Combine transcript snippets into plain text
        lines = [snippet.text for snippet in transcript.snippets]
        full_text = ' '.join(lines)

        metadata['full_content'] = full_text
        metadata['content_format'] = 'transcript'
        metadata['content_source'] = 'youtube_transcript'
        metadata['content_length'] = len(full_text)
        metadata['enriched_at'] = datetime.now(timezone.utc).isoformat()
        metadata['enrichment_failed'] = False
        metadata['enrichment_error'] = None

    except Exception as e:
        error_str = str(e)
        logger.warning(f"YouTube transcript fetch failed for {video_id}: {e}")
        metadata['enrichment_failed'] = True
        metadata['enrichment_error'] = error_str
        # Trip circuit breaker on IP bans — no point retrying other videos
        if 'blocking' in error_str.lower() or 'ip' in error_str.lower():
            _youtube_blocked = True
            logger.info("YouTube IP block detected, skipping remaining transcripts this run")

    return metadata


# Schemes and domains that should never be sent to Firecrawl
_SKIP_SCHEMES = {'mailto', 'tel', 'javascript', 'data', 'about'}
_SKIP_DOMAINS = {
    'facebook.com', 'www.facebook.com',
    'twitter.com', 'www.twitter.com', 'x.com', 'www.x.com',
    'instagram.com', 'www.instagram.com',
    'linkedin.com', 'www.linkedin.com',
    'tiktok.com', 'www.tiktok.com',
    'youtube.com', 'www.youtube.com',  # handled by transcript enrichment
}
# URL path patterns that indicate non-article pages (listing/nav pages)
_SKIP_PATH_PATTERNS = re.compile(
    r'/page/\d+$'              # pagination: /page/8
    r'|-npage-\d+'             # alt pagination: /-npage-2
    r'|/tag/'                  # tag listing pages
    r'|/category/'             # category listing pages
    r'|/author/'               # author listing pages
    r'|/search'                # search results pages
    r'|/archive/'              # archive listing pages
    r'|/events?(/|$)'          # events section
    r'|/member(/|$)'           # member pages
    r'|/subscribe'             # subscribe pages
    r'|/issue/'                # topic/issue listing pages
    r'|sitemap\.xml',          # sitemaps
    re.IGNORECASE,
)

# Regex for paths that are ONLY a date with no article slug after:
# e.g. /2024/october  /2019/may-6-2019  /2009/jan-8-2009  but NOT /2026/january/actual-article-slug
_DATE_ONLY_PATH = re.compile(
    r'/\d{4}/(?:\d{1,2}|'
    r'january|february|march|april|may|june|'
    r'july|august|september|october|november|december'
    r')(?:-\d{1,2}-\d{4})?/?$',
    re.IGNORECASE,
)


def _is_scrapable_url(url: str, source_url: str = None) -> bool:
    """Check if a URL looks like an article worth sending to Firecrawl.

    Filters out navigation pages, pagination, archive listings, social media,
    and non-HTTP schemes. Optionally checks against the source's own URL
    to avoid scraping the listing page itself.
    """
    parsed = urlparse(url)

    # Scheme checks
    if parsed.scheme in _SKIP_SCHEMES:
        return False
    if not parsed.netloc:
        return False

    # Domain checks
    if parsed.netloc.lower() in _SKIP_DOMAINS:
        return False

    path = parsed.path.rstrip('/')

    # File type checks
    lower_path = path.lower()
    if lower_path.endswith(('.pdf', '.xml', '.json', '.csv')):
        return False

    # Fragment-only or empty path
    if not path or path == '/':
        return False
    if url.endswith('#') or url.endswith('#content'):
        return False

    # Known non-article path patterns
    if _SKIP_PATH_PATTERNS.search(path):
        return False

    # Date-only paths (month archive listings, not articles)
    if _DATE_ONLY_PATH.search(path):
        return False

    # Single-segment paths: only allow if the slug is long enough to be an article
    # Nav pages have short slugs like /publications, /news-events, /press-releases
    # Article slugs are URL-ified titles: /statement-on-stb-decision, /farm-bill-process
    segments = [s for s in path.split('/') if s]
    if len(segments) < 2 and len(segments[-1] if segments else '') < 16:
        return False

    # Skip if URL matches the source listing page itself
    if source_url:
        source_path = urlparse(source_url).path.rstrip('/')
        if path == source_path:
            return False

    return True


def _enrich_web_article(url: str, metadata: dict, source_url: str = None) -> dict:
    """Fetch full article content via Firecrawl /scrape endpoint."""
    if not _is_scrapable_url(url, source_url=source_url):
        metadata['enrichment_failed'] = True
        metadata['enrichment_error'] = f'URL not scrapable: {url}'
        return metadata

    api_key = current_app.config.get('FIRECRAWL_API_KEY')
    if not api_key:
        metadata['enrichment_failed'] = True
        metadata['enrichment_error'] = 'FIRECRAWL_API_KEY not configured'
        return metadata

    scrape_data = _firecrawl_scrape(url, api_key)
    if not scrape_data:
        metadata['enrichment_failed'] = True
        metadata['enrichment_error'] = 'Firecrawl scrape returned no data'
        return metadata

    markdown = scrape_data.get('markdown')
    if markdown:
        metadata['full_content'] = markdown
        metadata['content_format'] = 'markdown'
        metadata['content_source'] = 'firecrawl'
        metadata['content_length'] = len(markdown)
        metadata['enriched_at'] = datetime.now(timezone.utc).isoformat()
        metadata['enrichment_failed'] = False
        metadata['enrichment_error'] = None
    else:
        metadata['enrichment_failed'] = True
        metadata['enrichment_error'] = 'Firecrawl scrape returned no content'

    # Extract publish date from page metadata, falling back to URL pattern
    page_meta = scrape_data.get('metadata', {})
    published_date = _extract_publish_date(page_meta) or _extract_date_from_url(url)
    if published_date:
        metadata['extracted_published_date'] = published_date.isoformat()

    return metadata


def _firecrawl_scrape(url: str, api_key: str) -> Optional[dict]:
    """Call Firecrawl /scrape with markdown format. Returns the data dict. Single retry on 429."""
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'url': url,
        'formats': ['markdown'],
    }

    for attempt in range(2):
        _firecrawl_rate_limit()
        try:
            resp = requests.post(
                'https://api.firecrawl.dev/v1/scrape',
                headers=headers,
                json=payload,
                timeout=60,
            )

            if resp.status_code == 429 and attempt == 0:
                retry_after = int(resp.headers.get('Retry-After', 10))
                logger.info(f"Firecrawl 429 for {url}, retrying after {retry_after}s")
                time.sleep(retry_after)
                continue

            resp.raise_for_status()
            return resp.json().get('data', {})

        except requests.RequestException as e:
            if attempt == 0 and '429' in str(e):
                time.sleep(10)
                continue
            logger.error(f"Firecrawl /scrape failed for {url}: {e}")
            return None

    return None


def _extract_publish_date(page_metadata: dict) -> Optional[datetime]:
    """Extract a publish date from Firecrawl page metadata.

    Firecrawl returns OG/meta tags like publishedTime, modifiedTime,
    ogArticle:published_time, etc.
    """
    from dateutil.parser import parse as parse_date

    # Try fields in priority order — publishedTime is most reliable
    date_fields = [
        'publishedTime',
        'article:published_time',
        'ogArticle:published_time',
        'modifiedTime',
        'article:modified_time',
    ]

    for field in date_fields:
        value = page_metadata.get(field)
        if value:
            try:
                dt = parse_date(str(value))
                # Sanity check: not in the future, not older than 2 years
                now = datetime.now(timezone.utc)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt <= now and (now - dt).days < 730:
                    return dt
            except (ValueError, TypeError):
                continue

    return None


# Patterns for dates embedded in URL paths:
#   /2025/02/19/article-slug  or  /2025/02/article-slug
#   /2025/february/article-slug
#   /2025-02-19-article-slug  (press release style)
_URL_DATE_FULL = re.compile(r'/(\d{4})/(\d{1,2})/(\d{1,2})/')
_URL_DATE_HYPHEN = re.compile(r'/(\d{4})-(\d{2})-(\d{2})-')
_URL_DATE_MONTH = re.compile(r'/(\d{4})/(\d{1,2})/')
_MONTH_NAMES = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
    'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9,
    'oct': 10, 'nov': 11, 'dec': 12,
}
_URL_DATE_NAMED_MONTH = re.compile(
    r'/(\d{4})/(' + '|'.join(_MONTH_NAMES) + r')(?:[/-]|$)',
    re.IGNORECASE,
)
# Year-only pattern: /YYYY/ followed by a slug (not another number segment)
_URL_DATE_YEAR_ONLY = re.compile(r'/(\d{4})/(?![0-9])')


def _extract_date_from_url(url: str) -> Optional[datetime]:
    """Extract a publish date from common URL path patterns like /2025/02/19/.

    Returns dates up to 10 years old — callers decide how to handle old dates
    (e.g. low recency score, max-age filter).
    """
    now = datetime.now(timezone.utc)

    # Try full date: /YYYY/MM/DD/
    m = _URL_DATE_FULL.search(url)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            if dt <= now and (now - dt).days < 3650:
                return dt
        except ValueError:
            pass

    # Try hyphenated date: /YYYY-MM-DD-slug (press releases, investor pages)
    m = _URL_DATE_HYPHEN.search(url)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            if dt <= now and (now - dt).days < 3650:
                return dt
        except ValueError:
            pass

    # Try year/month: /YYYY/MM/
    m = _URL_DATE_MONTH.search(url)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
            if dt <= now and (now - dt).days < 3650:
                return dt
        except ValueError:
            pass

    # Try named month: /YYYY/february/ or /YYYY/jan-13-2014/
    m = _URL_DATE_NAMED_MONTH.search(url)
    if m:
        month_num = _MONTH_NAMES.get(m.group(2).lower())
        if month_num:
            try:
                dt = datetime(int(m.group(1)), month_num, 1, tzinfo=timezone.utc)
                if dt <= now and (now - dt).days < 3650:
                    return dt
            except ValueError:
                pass

    # Try year-only: /YYYY/slug (assume Jan 1 of that year)
    m = _URL_DATE_YEAR_ONLY.search(url)
    if m:
        try:
            year = int(m.group(1))
            if 2000 <= year <= now.year:
                dt = datetime(year, 1, 1, tzinfo=timezone.utc)
                if (now - dt).days < 3650:
                    return dt
        except ValueError:
            pass

    return None


def _extract_youtube_video_id(url: str) -> Optional[str]:
    """Extract video ID from various YouTube URL formats."""
    parsed = urlparse(url)

    if parsed.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com'):
        if parsed.path == '/watch':
            qs = parse_qs(parsed.query)
            return qs.get('v', [None])[0]
        if parsed.path.startswith('/shorts/'):
            return parsed.path.split('/shorts/')[1].split('/')[0]
    elif parsed.hostname == 'youtu.be':
        return parsed.path.lstrip('/')

    return None
