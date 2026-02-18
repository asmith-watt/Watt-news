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

            items.append(DiscoveredItem(
                url=link,
                title=entry.get('title'),
                snippet=entry.get('summary', entry.get('description')),
                author=entry.get('author'),
                published_date=pub_date,
                metadata={'tags': tags, 'feed_title': feed.feed.get('title', '')},
            ))

        return items


class NewsSiteScraper(BaseScraper):
    """Use Firecrawl /map endpoint to discover article URLs on a news site."""

    def scrape(self, source) -> List[DiscoveredItem]:
        if not source.url:
            return []

        api_key = current_app.config.get('FIRECRAWL_API_KEY')
        if not api_key:
            logger.warning("FIRECRAWL_API_KEY not configured, skipping NewsSiteScraper")
            return []

        items = self._map_site(source)
        if len(items) < 5:
            items.extend(self._scrape_links_fallback(source))

        return items

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
                timeout=30,
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
                timeout=30,
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

        return True

    def _discover_report_urls(self, source) -> List[dict]:
        config = source.config
        mode = config.get('discovery_mode')

        if mode == 'url_pattern':
            return self._discover_via_pattern(config)
        elif mode == 'landing_page':
            return self._discover_via_landing_page(source)

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
                published_date=datetime.utcnow(),
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
    'Keyword Search': KeywordSearchScraper,
    'Data': DataScraper,
}


def get_scraper(source_type: str) -> Optional[BaseScraper]:
    """Factory function to get a scraper instance for the given source type."""
    scraper_class = SCRAPER_REGISTRY.get(source_type)
    if scraper_class:
        return scraper_class()
    return None
