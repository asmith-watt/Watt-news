"""Source-type-specific scraping strategies for candidate article discovery."""
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

import feedparser
import requests
from flask import current_app

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredItem:
    url: str
    title: Optional[str] = None
    snippet: Optional[str] = None
    author: Optional[str] = None
    published_date: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


class BaseScraper:
    """Abstract base class for source scrapers."""

    def _firecrawl_headers(self):
        api_key = current_app.config.get('FIRECRAWL_API_KEY')
        return {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

    def scrape(self, source) -> List[DiscoveredItem]:
        raise NotImplementedError


class RSSFeedScraper(BaseScraper):
    """Parse RSS/Atom feeds using feedparser."""

    def scrape(self, source) -> List[DiscoveredItem]:
        if not source.url:
            return []

        try:
            feed = feedparser.parse(source.url)
        except Exception as e:
            logger.error(f"Failed to parse RSS feed {source.url}: {e}")
            return []

        items = []
        for entry in feed.entries:
            link = entry.get('link')
            if not link:
                continue

            pub_date = None
            for date_field in ('published_parsed', 'updated_parsed'):
                parsed = entry.get(date_field)
                if parsed:
                    try:
                        pub_date = datetime(*parsed[:6])
                    except Exception:
                        pass
                    break

            tags = [t.get('term', '') for t in entry.get('tags', [])]

            metadata = {'tags': tags, 'feed_title': feed.feed.get('title', '')}

            # Extract full content from feedparser (free, no API call)
            content_entries = entry.get('content', [])
            if content_entries and isinstance(content_entries, list):
                # feedparser stores content as list of dicts with 'value' key
                full_parts = [c.get('value', '') for c in content_entries if c.get('value')]
                if full_parts:
                    metadata['rss_full_content'] = '\n'.join(full_parts)

            items.append(DiscoveredItem(
                url=link,
                title=entry.get('title'),
                snippet=entry.get('summary', entry.get('description')),
                author=entry.get('author'),
                published_date=pub_date,
                metadata=metadata,
            ))

        return items


class NewsSiteScraper(BaseScraper):
    """Discover articles on a news site. Tries RSS feed first, falls back to Firecrawl /map."""

    # Common RSS/API paths to probe (relative to site root)
    _FEED_PATHS = [
        '/feed', '/feed/', '/rss', '/rss/', '/feed.xml', '/rss.xml',
        '/atom.xml', '/feeds/posts/default',
        '/news/feed', '/news/rss', '/blog/feed', '/blog/rss',
    ]
    # WordPress REST API posts endpoint
    _WP_API_PATH = '/wp-json/wp/v2/posts?per_page=20&orderby=date&order=desc'

    def scrape(self, source) -> List[DiscoveredItem]:
        if not source.url:
            return []

        # Try RSS feed first — free, includes dates/titles/content
        rss_items = self._try_rss_feed(source)
        if rss_items:
            logger.info(f"RSS feed found for {source.url}, got {len(rss_items)} items")
            return rss_items

        # Fall back to Firecrawl /map
        api_key = current_app.config.get('FIRECRAWL_API_KEY')
        if not api_key:
            logger.warning("FIRECRAWL_API_KEY not configured, skipping NewsSiteScraper")
            return []

        items = self._map_site(source)
        if len(items) < 5:
            items.extend(self._scrape_links_fallback(source))

        return items

    def _try_rss_feed(self, source) -> List[DiscoveredItem]:
        """Probe common RSS feed paths on the site. Returns items if a feed is found."""
        parsed = urlparse(source.url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Try RSS/Atom feeds first
        for feed_path in self._FEED_PATHS:
            feed_url = base_url + feed_path
            items = self._parse_rss_url(feed_url)
            if items:
                return items

        # Try WordPress REST API
        wp_items = self._try_wp_api(base_url)
        if wp_items:
            return wp_items

        return []

    def _parse_rss_url(self, feed_url) -> List[DiscoveredItem]:
        """Try to fetch and parse an RSS/Atom feed URL. Returns items or empty list."""
        try:
            resp = requests.get(feed_url, timeout=5, allow_redirects=True)
            if resp.status_code != 200:
                return []

            content_type = resp.headers.get('Content-Type', '').lower()
            is_feed = any(t in content_type for t in ['xml', 'rss', 'atom', 'feed'])
            body_start = resp.text[:200].strip()
            is_xml = body_start.startswith('<?xml') or body_start.startswith('<rss') or body_start.startswith('<feed')

            if not is_feed and not is_xml:
                return []

            feed = feedparser.parse(resp.text)
            if not feed.entries:
                return []

            logger.info(f"Found RSS feed at {feed_url} with {len(feed.entries)} entries")
            items = []
            for entry in feed.entries:
                link = entry.get('link')
                if not link:
                    continue

                pub_date = None
                for date_field in ('published_parsed', 'updated_parsed'):
                    parsed_date = entry.get(date_field)
                    if parsed_date:
                        try:
                            pub_date = datetime(*parsed_date[:6])
                        except Exception:
                            pass
                        break

                tags = [t.get('term', '') for t in entry.get('tags', [])]
                metadata = {
                    'tags': tags,
                    'feed_title': feed.feed.get('title', ''),
                    'feed_url': feed_url,
                }

                # Extract full content (free enrichment)
                content_entries = entry.get('content', [])
                if content_entries and isinstance(content_entries, list):
                    full_parts = [c.get('value', '') for c in content_entries if c.get('value')]
                    if full_parts:
                        metadata['rss_full_content'] = '\n'.join(full_parts)

                items.append(DiscoveredItem(
                    url=link,
                    title=entry.get('title'),
                    snippet=entry.get('summary', entry.get('description')),
                    author=entry.get('author'),
                    published_date=pub_date,
                    metadata=metadata,
                ))

            return items

        except requests.RequestException:
            return []
        except Exception as e:
            logger.debug(f"Feed probe failed for {feed_url}: {e}")
            return []

    def _try_wp_api(self, base_url) -> List[DiscoveredItem]:
        """Try the WordPress REST API to fetch recent posts."""
        api_url = base_url + self._WP_API_PATH
        try:
            resp = requests.get(api_url, timeout=5, allow_redirects=True)
            if resp.status_code != 200:
                return []

            content_type = resp.headers.get('Content-Type', '').lower()
            if 'json' not in content_type:
                return []

            posts = resp.json()
            if not isinstance(posts, list) or not posts:
                return []

            logger.info(f"Found WordPress API at {base_url} with {len(posts)} posts")
            items = []
            for post in posts:
                link = post.get('link', '')
                if not link:
                    continue

                pub_date = None
                date_str = post.get('date_gmt') or post.get('date')
                if date_str:
                    try:
                        from dateutil.parser import parse as parse_date
                        pub_date = parse_date(date_str)
                    except (ValueError, TypeError):
                        pass

                title = ''
                title_obj = post.get('title', {})
                if isinstance(title_obj, dict):
                    title = title_obj.get('rendered', '')
                elif isinstance(title_obj, str):
                    title = title_obj

                snippet = ''
                excerpt_obj = post.get('excerpt', {})
                if isinstance(excerpt_obj, dict):
                    snippet = excerpt_obj.get('rendered', '')
                elif isinstance(excerpt_obj, str):
                    snippet = excerpt_obj

                # Strip HTML tags from title and snippet
                import re as _re
                title = _re.sub(r'<[^>]+>', '', title).strip()
                snippet = _re.sub(r'<[^>]+>', '', snippet).strip()

                # Full content from WP API (free enrichment)
                metadata = {'content_source': 'wp_api'}
                content_obj = post.get('content', {})
                if isinstance(content_obj, dict) and content_obj.get('rendered'):
                    metadata['rss_full_content'] = content_obj['rendered']

                items.append(DiscoveredItem(
                    url=link,
                    title=title,
                    snippet=snippet[:500],
                    published_date=pub_date,
                    metadata=metadata,
                ))

            return items

        except requests.RequestException:
            return []
        except Exception as e:
            logger.debug(f"WordPress API probe failed for {base_url}: {e}")
            return []

    def _map_site(self, source) -> List[DiscoveredItem]:
        payload = {
            'url': source.url,
            'limit': 50,
        }
        if source.keywords:
            payload['search'] = source.keywords

        try:
            resp = requests.post(
                'https://api.firecrawl.dev/v1/map',
                headers=self._firecrawl_headers(),
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Firecrawl /map failed for {source.url}: {e}")
            return []

        items = []
        for link_data in data.get('links', []):
            if isinstance(link_data, str):
                items.append(DiscoveredItem(url=link_data))
            elif isinstance(link_data, dict):
                items.append(DiscoveredItem(
                    url=link_data.get('url', ''),
                    title=link_data.get('title'),
                    snippet=link_data.get('description'),
                ))
        return [i for i in items if i.url]

    def _scrape_links_fallback(self, source) -> List[DiscoveredItem]:
        try:
            resp = requests.post(
                'https://api.firecrawl.dev/v1/scrape',
                headers=self._firecrawl_headers(),
                json={'url': source.url, 'formats': ['links']},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Firecrawl /scrape fallback failed for {source.url}: {e}")
            return []

        items = []
        for link in data.get('data', {}).get('links', []):
            if isinstance(link, str):
                items.append(DiscoveredItem(url=link))
        return items


class KeywordSearchScraper(BaseScraper):
    """Use SerpAPI Google News to find articles by keyword."""

    def scrape(self, source) -> List[DiscoveredItem]:
        keywords = source.keywords
        if not keywords:
            return []

        api_key = current_app.config.get('SERPAPI_API_KEY')
        if not api_key:
            logger.warning("SERPAPI_API_KEY not configured, skipping KeywordSearchScraper")
            return []

        try:
            from serpapi import GoogleSearch
            params = {
                'engine': 'google_news',
                'q': keywords,
                'gl': 'us',
                'hl': 'en',
                'api_key': api_key,
            }
            search = GoogleSearch(params)
            results = search.get_dict()
        except Exception as e:
            logger.error(f"SerpAPI search failed for keywords '{keywords}': {e}")
            return []

        items = []
        for article in results.get('news_results', []):
            pub_date = None
            iso_date = article.get('date')
            if iso_date:
                try:
                    pub_date = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass

            items.append(DiscoveredItem(
                url=article.get('link', ''),
                title=article.get('title'),
                snippet=article.get('snippet'),
                author=article.get('source', {}).get('name'),
                published_date=pub_date,
                metadata={
                    'source_name': article.get('source', {}).get('name'),
                    'thumbnail': article.get('thumbnail'),
                },
            ))

        return [i for i in items if i.url]


class YouTubeSearchScraper(BaseScraper):
    """Use SerpAPI YouTube engine to find videos by keyword."""

    def scrape(self, source) -> List[DiscoveredItem]:
        keywords = source.keywords
        if not keywords:
            return []

        api_key = current_app.config.get('SERPAPI_API_KEY')
        if not api_key:
            logger.warning("SERPAPI_API_KEY not configured, skipping YouTubeSearchScraper")
            return []

        try:
            from serpapi import GoogleSearch
            params = {
                'engine': 'youtube',
                'search_query': keywords,
                'gl': 'us',
                'hl': 'en',
                'api_key': api_key,
            }
            search = GoogleSearch(params)
            results = search.get_dict()
        except Exception as e:
            logger.error(f"SerpAPI YouTube search failed for keywords '{keywords}': {e}")
            return []

        items = []
        for video in results.get('video_results', []):
            link = video.get('link', '')
            if not link:
                continue

            pub_date = None
            published_str = video.get('published_date')
            if published_str:
                try:
                    from dateutil.parser import parse as parse_date
                    pub_date = parse_date(published_str, fuzzy=True)
                except (ValueError, TypeError):
                    pass

            channel = video.get('channel', {})
            views = video.get('views')

            items.append(DiscoveredItem(
                url=link,
                title=video.get('title'),
                snippet=video.get('description'),
                author=channel.get('name'),
                published_date=pub_date,
                metadata={
                    'content_type': 'youtube_video',
                    'channel_name': channel.get('name'),
                    'channel_link': channel.get('link'),
                    'channel_verified': channel.get('verified', False),
                    'views': views,
                    'length': video.get('length'),
                    'thumbnail': video.get('thumbnail', {}).get('static') if isinstance(video.get('thumbnail'), dict) else video.get('thumbnail'),
                    'video_id': video.get('video_id'),
                },
            ))

        return items


class CompetitorScraper(NewsSiteScraper):
    """Same as NewsSiteScraper but marks metadata with is_competitor flag."""

    def scrape(self, source) -> List[DiscoveredItem]:
        items = super().scrape(source)
        for item in items:
            item.metadata['is_competitor'] = True
        return items


class DataScraper(BaseScraper):
    """Scrape government PDF reports, extract text, and use Claude to identify story angles."""

    REQUIRED_CONFIG_FIELDS = {'discovery_mode', 'document_type', 'report_name', 'publisher', 'cadence', 'analysis_prompt'}

    def scrape(self, source) -> List[DiscoveredItem]:
        config = source.config
        if not config or not self._validate_config(config):
            logger.warning(f"DataScraper: source '{source.name}' (id={source.id}) has invalid or missing config")
            return []

        api_key = current_app.config.get('ANTHROPIC_API_KEY')
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY not configured, skipping DataScraper")
            return []

        report_urls = self._discover_report_urls(source)
        if not report_urls:
            logger.info(f"DataScraper: no report URLs discovered for source '{source.name}'")
            return []

        all_items = []
        for report_info in report_urls:
            try:
                pdf_bytes = self._download_pdf(report_info['url'])
                if not pdf_bytes:
                    continue

                text = self._extract_text(pdf_bytes)
                if not text or len(text.strip()) < 100:
                    logger.warning(f"DataScraper: insufficient text extracted from {report_info['url']}")
                    continue

                analysis = self._analyze_with_claude(text, config, api_key)
                if not analysis:
                    continue

                items = self._parse_angles_to_items(
                    analysis=analysis,
                    pdf_url=report_info['url'],
                    report_date=report_info.get('date'),
                    config=config,
                    source=source,
                )
                all_items.extend(items)

                self._update_previous_report_data(source, analysis, report_info.get('date'))

            except Exception as e:
                logger.error(f"DataScraper: error processing {report_info['url']}: {e}", exc_info=True)
                continue

        return all_items

    def _validate_config(self, config: dict) -> bool:
        missing = self.REQUIRED_CONFIG_FIELDS - set(config.keys())
        if missing:
            logger.warning(f"DataScraper: missing config fields: {missing}")
            return False

        mode = config.get('discovery_mode')
        if mode == 'url_pattern' and 'url_pattern' not in config:
            logger.warning("DataScraper: url_pattern mode requires 'url_pattern' in config")
            return False
        if mode == 'landing_page' and not config.get('landing_page_url'):
            logger.warning("DataScraper: landing_page mode requires 'landing_page_url' in config")
            return False
        if mode == 'api' and not config.get('api_url'):
            logger.warning("DataScraper: api mode requires 'api_url' in config")
            return False
        if mode == 'api' and not config.get('pdf_json_path'):
            logger.warning("DataScraper: api mode requires 'pdf_json_path' in config")
            return False

        return True

    def _discover_report_urls(self, source) -> List[dict]:
        config = source.config
        mode = config.get('discovery_mode')

        if mode == 'url_pattern':
            return self._discover_via_pattern(config)
        elif mode == 'landing_page':
            return self._discover_via_landing_page(source)
        elif mode == 'api':
            return self._discover_via_api(config)

        logger.warning(f"DataScraper: unknown discovery_mode '{mode}'")
        return []

    def _discover_via_pattern(self, config: dict) -> List[dict]:
        from dateutil.relativedelta import relativedelta

        pattern = config['url_pattern']
        lookback = config.get('lookback_months', 2)
        now = datetime.utcnow()
        urls = []

        for months_back in range(lookback):
            target_date = now - relativedelta(months=months_back)
            mmyy = target_date.strftime('%m%y')
            url = pattern.replace('{MMYY}', mmyy)

            try:
                resp = requests.head(url, timeout=10, allow_redirects=True)
                if resp.status_code == 200:
                    content_type = resp.headers.get('Content-Type', '')
                    if 'pdf' in content_type.lower() or url.lower().endswith('.pdf'):
                        urls.append({
                            'url': url,
                            'date': target_date.strftime('%Y-%m'),
                        })
                        logger.info(f"DataScraper: found report at {url}")
                    else:
                        logger.debug(f"DataScraper: {url} returned 200 but Content-Type is '{content_type}'")
                else:
                    logger.debug(f"DataScraper: HEAD {url} returned {resp.status_code}")
            except requests.RequestException as e:
                logger.debug(f"DataScraper: HEAD check failed for {url}: {e}")

        return urls

    def _discover_via_landing_page(self, source) -> List[dict]:
        config = source.config
        landing_url = config.get('landing_page_url') or source.url
        if not landing_url:
            return []

        api_key = current_app.config.get('FIRECRAWL_API_KEY')
        if not api_key:
            logger.warning("FIRECRAWL_API_KEY not configured, cannot scrape landing page for PDFs")
            return []

        try:
            resp = requests.post(
                'https://api.firecrawl.dev/v1/scrape',
                headers=self._firecrawl_headers(),
                json={'url': landing_url, 'formats': ['links']},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"DataScraper: Firecrawl scrape failed for {landing_url}: {e}")
            return []

        urls = []
        for link in data.get('data', {}).get('links', []):
            if isinstance(link, str) and link.lower().endswith('.pdf'):
                urls.append({'url': link, 'date': None})

        return urls

    @staticmethod
    def _resolve_json_path(data, path: str) -> list:
        """Resolve a simple dot/bracket JSON path like 'rows[].outlookReport' into a list of values.

        Supports:
          - 'field'           → data['field']
          - 'field[]'         → iterate over data['field']
          - 'field[].child'   → [item['child'] for item in data['field']]
          - 'a.b[].c.d'      → nested traversal
        """
        parts = []
        for segment in path.split('.'):
            if segment.endswith('[]'):
                parts.append(('iter', segment[:-2]))
            else:
                parts.append(('key', segment))

        def _walk(obj, remaining_parts):
            if not remaining_parts:
                return [obj] if obj is not None else []

            kind, key = remaining_parts[0]
            rest = remaining_parts[1:]

            if kind == 'iter':
                collection = obj.get(key, []) if isinstance(obj, dict) else []
                results = []
                for item in collection:
                    results.extend(_walk(item, rest))
                return results
            else:
                child = obj.get(key) if isinstance(obj, dict) else None
                if child is None:
                    return []
                return _walk(child, rest)

        return _walk(data, parts)

    def _discover_via_api(self, config: dict) -> List[dict]:
        """Fetch a JSON API endpoint and extract PDF URLs using configured JSON paths."""
        from dateutil.parser import parse as parse_date
        from dateutil.relativedelta import relativedelta

        api_url = config['api_url']
        pdf_path = config['pdf_json_path']
        date_path = config.get('date_json_path')
        lookback = config.get('lookback_months', 2)

        try:
            resp = requests.get(api_url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"DataScraper: API request failed for {api_url}: {e}")
            return []

        pdf_urls = self._resolve_json_path(data, pdf_path)

        # Extract dates if a date path is configured
        dates = []
        if date_path:
            dates = self._resolve_json_path(data, date_path)

        # Pair URLs with dates (pad dates list if shorter)
        urls = []
        cutoff = datetime.utcnow() - relativedelta(months=lookback)

        for idx, pdf_url in enumerate(pdf_urls):
            if not pdf_url or not isinstance(pdf_url, str):
                continue

            # Parse the corresponding date if available
            report_date = None
            date_str = None
            if idx < len(dates) and dates[idx]:
                try:
                    parsed_dt = parse_date(str(dates[idx]))
                    report_date = parsed_dt.strftime('%Y-%m')
                    # Skip reports older than lookback window
                    if parsed_dt.replace(tzinfo=None) < cutoff:
                        logger.debug(f"DataScraper: skipping {pdf_url} — older than {lookback} months")
                        continue
                except (ValueError, TypeError):
                    pass

            urls.append({
                'url': pdf_url,
                'date': report_date,
            })
            logger.info(f"DataScraper: found report via API at {pdf_url} (date={report_date})")

        return urls

    def _download_pdf(self, url: str) -> Optional[bytes]:
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as e:
            logger.error(f"DataScraper: failed to download PDF {url}: {e}")
            return None

    def _extract_text(self, pdf_bytes: bytes) -> str:
        import pdfplumber

        text_parts = []
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    # Extract tables first
                    tables = page.extract_tables()
                    for table in tables:
                        for row in table:
                            if row:
                                cells = [str(cell) if cell else '' for cell in row]
                                text_parts.append(' | '.join(cells))

                    # Extract remaining text
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
        except Exception as e:
            logger.error(f"DataScraper: pdfplumber extraction failed: {e}")
            return ''

        return '\n\n'.join(text_parts)

    def _analyze_with_claude(self, text: str, config: dict, api_key: str) -> Optional[dict]:
        import anthropic

        max_angles = config.get('max_angles', 5)
        model = config.get('claude_model', 'claude-sonnet-4-20250514')
        analysis_prompt = config['analysis_prompt']

        # Include previous report data for month-over-month context
        previous_context = ''
        prev_data = config.get('previous_report_data')
        if prev_data:
            previous_context = (
                f"\n\n## Previous Report Data (for month-over-month comparison)\n"
                f"Report date: {prev_data.get('report_date', 'unknown')}\n"
                f"Summary: {prev_data.get('report_summary', 'N/A')}\n"
                f"Key figures: {json.dumps(prev_data.get('key_figures', {}), indent=2)}\n"
            )

        # Truncate text to stay within reasonable token limits
        max_chars = 150000
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[... document truncated ...]"

        user_message = (
            f"{analysis_prompt}\n"
            f"{previous_context}\n\n"
            f"## Document Text\n\n{text}\n\n"
            f"## Response Instructions\n\n"
            f"Respond with valid JSON only (no markdown fencing). Use this exact structure:\n"
            f'{{\n'
            f'  "report_summary": "Brief 2-3 sentence overview of the report",\n'
            f'  "key_figures": {{"metric_name": "value", ...}},\n'
            f'  "story_angles": [\n'
            f'    {{\n'
            f'      "headline": "Compelling news headline",\n'
            f'      "summary": "2-3 paragraph summary suitable for an article",\n'
            f'      "commodity": "relevant commodity or sector",\n'
            f'      "data_points": [{{"metric": "...", "value": "...", "previous": "...", "change": "..."}}],\n'
            f'      "significance": "high|medium|low",\n'
            f'      "angle_type": "supply_shift|demand_change|price_impact|trade_flow|policy_change|weather_impact|other"\n'
            f'    }}\n'
            f'  ]\n'
            f'}}\n\n'
            f'Return up to {max_angles} story angles, ordered by significance.'
        )

        try:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{'role': 'user', 'content': user_message}],
            )

            response_text = response.content[0].text.strip()
            # Strip markdown code fencing if present
            if response_text.startswith('```'):
                lines = response_text.split('\n')
                # Remove first line (```json or ```) and last line (```)
                lines = [l for l in lines if not l.strip().startswith('```')]
                response_text = '\n'.join(lines)

            analysis = json.loads(response_text)
            return analysis

        except json.JSONDecodeError as e:
            logger.error(f"DataScraper: Claude returned invalid JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"DataScraper: Claude API call failed: {e}", exc_info=True)
            return None

    def _parse_angles_to_items(self, analysis: dict, pdf_url: str, report_date: Optional[str],
                                config: dict, source) -> List[DiscoveredItem]:
        items = []
        angles = analysis.get('story_angles', [])

        # Use actual report date if available, otherwise fall back to now
        pub_date = None
        if report_date:
            try:
                from dateutil.parser import parse as parse_date
                pub_date = parse_date(report_date)
            except (ValueError, TypeError):
                pass
        if not pub_date:
            pub_date = datetime.utcnow()

        for idx, angle in enumerate(angles, start=1):
            # Create unique URL per angle using query param
            parsed = urlparse(pdf_url)
            params = parse_qs(parsed.query)
            params['angle'] = [str(idx)]
            unique_url = urlunparse((
                parsed.scheme, parsed.netloc, parsed.path,
                parsed.params, urlencode(params, doseq=True), '',
            ))

            items.append(DiscoveredItem(
                url=unique_url,
                title=angle.get('headline'),
                snippet=angle.get('summary'),
                author=config.get('publisher'),
                published_date=pub_date,
                metadata={
                    'source_type': 'Data',
                    'report_name': config.get('report_name'),
                    'publisher': config.get('publisher'),
                    'report_date': report_date,
                    'commodity': angle.get('commodity'),
                    'data_points': angle.get('data_points', []),
                    'significance': angle.get('significance'),
                    'angle_type': angle.get('angle_type'),
                    'angle_index': idx,
                    'pdf_url': pdf_url,
                },
            ))

        return items

    def _update_previous_report_data(self, source, analysis: dict, report_date: Optional[str]):
        try:
            from app import db

            if source.config is None:
                source.config = {}

            # SQLAlchemy needs a new dict reference to detect JSON changes
            updated_config = dict(source.config)
            updated_config['previous_report_data'] = {
                'report_date': report_date,
                'report_summary': analysis.get('report_summary'),
                'key_figures': analysis.get('key_figures', {}),
            }
            source.config = updated_config
            db.session.commit()
        except Exception as e:
            logger.error(f"DataScraper: failed to update previous_report_data: {e}")


SCRAPER_REGISTRY = {
    'RSS Feed': RSSFeedScraper,
    'News Site': NewsSiteScraper,
    'News': NewsSiteScraper,
    'Keyword Search': KeywordSearchScraper,
    'YouTube Keywords': YouTubeSearchScraper,
    'Data': DataScraper,
}


def get_scraper(source_type: str) -> Optional[BaseScraper]:
    """Factory function to get a scraper instance for the given source type."""
    scraper_class = SCRAPER_REGISTRY.get(source_type)
    if scraper_class:
        return scraper_class()
    return None
