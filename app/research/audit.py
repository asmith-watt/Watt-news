"""Read-only, side-effect-free source auditing.

This module powers the user-triggered "source audit" feature. It answers two
kinds of question about a publication's news sources:

  * technical health  — is each source configured correctly and reachable?
  * yield             — is each active source actually producing candidates?

It deliberately does NOT run the real scrapers. The real scrapers hit paid
APIs and, in the case of DataScraper, write ``previous_report_data`` back onto
the source's config (see scrapers.py). An audit must be repeatable and free of
side effects, so instead it uses lightweight probes:

  * RSS / News Site   — a single cheap HTTP fetch (no Firecrawl, no discovery)
  * Data              — pure config validation, no network, no write-back
  * Keyword / YouTube — one representative SerpAPI query (opt-in via ``live``)

The strategic "are they watching the right places?" coverage assessment lives
in :func:`run_coverage_audit` (LLM-backed) and is orchestrated alongside these
checks in ``app.tasks.run_source_audit``.

Every check emits zero or more *finding* dicts with a stable shape so the UI
can render per-source status and offer one-click "apply" actions. See
:func:`_finding`.
"""
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

import feedparser
import requests
from flask import current_app

from app import db
from app.models import CandidateArticle, SourceType

logger = logging.getLogger(__name__)


# --- Finding vocabulary -----------------------------------------------------

SEVERITY_CRITICAL = 'critical'  # source is broken / cannot work as configured
SEVERITY_WARNING = 'warning'    # source works but is degraded or suspect
SEVERITY_INFO = 'info'          # informational, no action required

CATEGORY_TECHNICAL = 'technical'  # reachability / feed validity
CATEGORY_CONFIG = 'config'        # static configuration correctness
CATEGORY_YIELD = 'yield'          # candidate production over time
CATEGORY_COVERAGE = 'coverage'    # strategic: right places being watched

# Action types the findings may attach. Mirrored by the whitelist in the
# admin apply endpoint — keep the two in sync.
ACTION_UPDATE_URL = 'update_url'
ACTION_DEACTIVATE_SOURCE = 'deactivate_source'
ACTION_FIX_SOURCE_TYPE = 'fix_source_type'
ACTION_ADD_SOURCE = 'add_source'
ACTION_DELETE_DUPLICATE = 'delete_duplicate'


# --- Tunables ---------------------------------------------------------------

PROBE_TIMEOUT = 12          # seconds per HTTP probe
STALE_FEED_DAYS = 90        # a feed with nothing newer than this is "stale"
YIELD_WINDOW_DAYS = 90      # window for the "produced no candidates" check

# Send browser-like headers. A bare/custom User-Agent gets 401/403'd by
# Cloudflare/Akamai-fronted sites that a real browser (and the real research
# pipeline via feedparser/Firecrawl) loads fine — those are false positives.
_PROBE_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/125.0.0.0 Safari/537.36'),
    'Accept': ('text/html,application/xhtml+xml,application/xml,'
               'application/rss+xml,application/atom+xml;q=0.9,*/*;q=0.8'),
    'Accept-Language': 'en-US,en;q=0.9',
}

# Statuses that usually mean bot protection blocked the audit probe rather than
# the source being gone — the real scraper may still succeed.
_BLOCKED_STATUSES = {401, 403, 429}

# Source types that have a real scraper registered (discover content). Types
# outside this set never produce candidates. Kept as a literal rather than
# importing the registry so the audit stays decoupled from scraper internals.
DISCOVERING_TYPES = {
    SourceType.RSS_FEED.value,
    SourceType.NEWS_SITE.value,
    'News',  # legacy alias of "News Site" honoured by the scraper registry
    SourceType.KEYWORD_SEARCH.value,
    SourceType.YOUTUBE_KEYWORDS.value,
    SourceType.DATA.value,
}

# Valid enum values (for typo detection).
VALID_SOURCE_TYPES = set(SourceType.values())

# Data-source required config, mirrored from DataScraper.REQUIRED_CONFIG_FIELDS.
_DATA_REQUIRED_FIELDS = {
    'discovery_mode', 'document_type', 'report_name',
    'publisher', 'cadence', 'analysis_prompt',
}


def _finding(code, severity, category, title, detail, recommendation,
             source=None, action=None):
    """Build a finding dict with the canonical shape.

    ``action`` is either None (advisory only) or a dict
    ``{'type': <ACTION_*>, 'params': {...}}`` describing a safe, reversible
    fix the apply endpoint can execute.
    """
    return {
        'code': code,
        'severity': severity,
        'category': category,
        'title': title,
        'detail': detail,
        'recommendation': recommendation,
        'source_id': source.id if source is not None else None,
        'source_name': source.name if source is not None else None,
        'source_type': source.source_type if source is not None else None,
        'action': action,
    }


# --- HTTP helper ------------------------------------------------------------

def _fetch(url, method='GET'):
    """Fetch a URL for probing. Never raises.

    Returns ``(response, error)`` — exactly one is non-None. ``error`` is a
    short human-readable reason on failure.
    """
    try:
        resp = requests.request(
            method, url,
            timeout=PROBE_TIMEOUT,
            allow_redirects=True,
            headers=_PROBE_HEADERS,
            stream=True,  # avoid pulling large bodies we don't need
        )
        return resp, None
    except requests.Timeout:
        return None, f'timed out after {PROBE_TIMEOUT}s'
    except requests.ConnectionError as e:
        return None, f'connection failed ({type(e).__name__})'
    except requests.RequestException as e:
        return None, f'request error: {e}'


def _has_scheme(url):
    try:
        return bool(urlparse(url).scheme in ('http', 'https'))
    except Exception:
        return False


def _redirect_target(resp):
    """Return the final URL if the response was permanently redirected, else None."""
    if resp.history and any(r.status_code in (301, 308) for r in resp.history):
        if resp.url and resp.url.rstrip('/') != resp.history[0].url.rstrip('/'):
            return resp.url
    return None


# --- Per-type technical probes ---------------------------------------------

def _probe_rss(source):
    findings = []
    url = (source.url or '').strip()
    if not url:
        findings.append(_finding(
            'rss_no_url', SEVERITY_CRITICAL, CATEGORY_CONFIG,
            'RSS source has no URL',
            'An RSS Feed source needs a feed URL, but this one is empty. It '
            'discovers nothing on every run.',
            'Add the feed URL, or deactivate the source if it is no longer used.',
            source=source,
            action={'type': ACTION_DEACTIVATE_SOURCE, 'params': {'source_id': source.id}},
        ))
        return findings

    if not _has_scheme(url):
        findings.append(_finding(
            'rss_bad_scheme', SEVERITY_CRITICAL, CATEGORY_CONFIG,
            'Feed URL is missing http(s)://',
            f'The URL "{url}" has no valid scheme, so it cannot be fetched.',
            'Prefix the URL with https:// (or http://).',
            source=source,
        ))
        return findings

    resp, error = _fetch(url)
    if error:
        # Our HTTP client failed. feedparser uses its own client/UA (what the
        # real scraper runs), so try it directly before declaring the feed dead.
        entries = _feedparser_entries(url)
        if entries:
            findings.extend(_rss_content_findings(source, url, entries))
            return findings
        findings.append(_finding(
            'rss_unreachable', SEVERITY_CRITICAL, CATEGORY_TECHNICAL,
            'Feed is unreachable',
            f'Fetching {url} failed: {error}, and feedparser could not read it '
            'either. This feed silently yields nothing during research.',
            'Verify the feed still exists; update the URL or deactivate the source.',
            source=source,
        ))
        return findings

    status = resp.status_code
    redirect = _redirect_target(resp)
    content_type = (resp.headers.get('Content-Type') or '').lower()
    try:
        body = resp.text  # streamed; small feeds only
    except Exception:
        body = ''
    finally:
        resp.close()

    if status in _BLOCKED_STATUSES:
        # Likely bot protection. feedparser (different client) may still get in,
        # exactly as the real scraper would — check before flagging.
        entries = _feedparser_entries(url)
        if entries:
            findings.extend(_rss_content_findings(source, url, entries))
        else:
            findings.append(_finding(
                'rss_blocked', SEVERITY_WARNING, CATEGORY_TECHNICAL,
                f'Feed blocked the health check (HTTP {status})',
                f'{url} returned HTTP {status} to the audit probe — usually bot '
                'protection (Cloudflare/Akamai). The real research pipeline uses a '
                'different client and may still succeed, so this is not proof the '
                'feed is broken.',
                'Open the URL in a browser: if it loads, no action is needed; if it '
                'is genuinely gone, update or deactivate the source.',
                source=source,
            ))
        _append_rss_redirect(findings, source, url, redirect)
        return findings

    if status >= 400:
        findings.append(_finding(
            'rss_http_error', SEVERITY_CRITICAL, CATEGORY_TECHNICAL,
            f'Feed returned HTTP {status}',
            f'{url} responded with status {status}.',
            'The feed may have moved or been removed. Update the URL or deactivate.',
            source=source,
        ))
        return findings

    findings.extend(_rss_content_findings(source, url, feedparser.parse(body).entries or [],
                                          content_type))
    _append_rss_redirect(findings, source, url, redirect)
    return findings


def _feedparser_entries(url):
    """Parse a feed via feedparser's own HTTP client. Returns entries or []."""
    try:
        return feedparser.parse(url).entries or []
    except Exception:
        return []


def _rss_content_findings(source, url, entries, content_type=''):
    """No-entries / staleness findings given a set of parsed feed entries."""
    out = []
    if not entries:
        looks_html = 'html' in content_type and 'xml' not in content_type
        detail = (
            f'{url} is reachable but no feed entries were parsed.'
            + (' The URL appears to serve an HTML page, not a feed.' if looks_html else '')
        )
        out.append(_finding(
            'rss_no_entries', SEVERITY_WARNING, CATEGORY_TECHNICAL,
            'Feed parsed but has no items',
            detail,
            'Confirm this is a real RSS/Atom feed URL (often /feed or /rss). '
            'A genuinely empty feed can be ignored.',
            source=source,
        ))
        return out

    newest = _newest_entry_date(entries)
    if newest is not None:
        age_days = (datetime.utcnow() - newest).days
        if age_days > STALE_FEED_DAYS:
            out.append(_finding(
                'rss_stale', SEVERITY_WARNING, CATEGORY_TECHNICAL,
                'Feed looks stale',
                f'The newest item in {url} is {age_days} days old '
                f'(threshold {STALE_FEED_DAYS}).',
                'Check whether the publisher still updates this feed; consider '
                'replacing it.',
                source=source,
            ))
    return out


def _append_rss_redirect(findings, source, url, redirect):
    """Advisory redirect finding, appended even when the feed is otherwise healthy."""
    if redirect:
        findings.append(_finding(
            'rss_redirect', SEVERITY_INFO, CATEGORY_CONFIG,
            'Feed URL permanently redirects',
            f'{url} permanently redirects to {redirect}.',
            'Update the stored URL to the destination to avoid the extra hop.',
            source=source,
            action={'type': ACTION_UPDATE_URL,
                    'params': {'source_id': source.id, 'url': redirect}},
        ))


def _newest_entry_date(entries):
    """Return the most recent entry datetime (UTC, naive) or None."""
    newest = None
    for e in entries:
        tm = getattr(e, 'published_parsed', None) or getattr(e, 'updated_parsed', None)
        if not tm:
            continue
        try:
            dt = datetime(*tm[:6])
        except (TypeError, ValueError):
            continue
        if newest is None or dt > newest:
            newest = dt
    return newest


def _probe_news_site(source):
    findings = []
    url = (source.url or '').strip()
    if not url:
        findings.append(_finding(
            'news_no_url', SEVERITY_CRITICAL, CATEGORY_CONFIG,
            'News Site source has no URL',
            'A News Site source needs a site URL to discover content from.',
            'Add the site URL, or deactivate the source.',
            source=source,
            action={'type': ACTION_DEACTIVATE_SOURCE, 'params': {'source_id': source.id}},
        ))
        return findings

    if not _has_scheme(url):
        findings.append(_finding(
            'news_bad_scheme', SEVERITY_CRITICAL, CATEGORY_CONFIG,
            'Site URL is missing http(s)://',
            f'The URL "{url}" has no valid scheme, so it cannot be fetched.',
            'Prefix the URL with https:// (or http://).',
            source=source,
        ))
        return findings

    resp, error = _fetch(url)
    if error:
        findings.append(_finding(
            'news_unreachable', SEVERITY_CRITICAL, CATEGORY_TECHNICAL,
            'Site is unreachable',
            f'Fetching {url} failed: {error}.',
            'Verify the site is up and the URL is correct, or deactivate the source.',
            source=source,
        ))
        return findings

    status = resp.status_code
    redirect = _redirect_target(resp)
    resp.close()

    if status in _BLOCKED_STATUSES:
        # Bot protection blocked the simple probe. News Site discovery runs
        # through Firecrawl (browser-rendered), so escalate to a Firecrawl fetch
        # — the real pipeline's own path — before flagging anything.
        has_fc = bool(current_app.config.get('FIRECRAWL_API_KEY'))
        if has_fc and _firecrawl_reachable(url):
            # Firecrawl gets in fine → the real pipeline works → false positive.
            _append_news_redirect(findings, source, url, redirect)
            return findings
        if has_fc:
            detail = (f'{url} returned HTTP {status} to the audit probe, and a '
                      'browser-grade Firecrawl fetch (the real pipeline path) also '
                      'could not retrieve it — so discovery likely fails here too.')
        else:
            detail = (f'{url} returned HTTP {status} to the audit probe — usually bot '
                      'protection (Cloudflare/Akamai). The real pipeline fetches this '
                      'site via Firecrawl, which may still succeed; set '
                      'FIRECRAWL_API_KEY to let the audit verify.')
        findings.append(_finding(
            'news_blocked', SEVERITY_WARNING, CATEGORY_TECHNICAL,
            f'Site blocked the health check (HTTP {status})',
            detail,
            'Open the URL in a browser: if it loads, it may still be scrapable; if it '
            'is genuinely gone, update or deactivate the source.',
            source=source,
        ))
        _append_news_redirect(findings, source, url, redirect)
        return findings

    if status >= 400:
        findings.append(_finding(
            'news_http_error', SEVERITY_CRITICAL, CATEGORY_TECHNICAL,
            f'Site returned HTTP {status}',
            f'{url} responded with status {status}.',
            'Update the URL or deactivate the source.',
            source=source,
        ))
        return findings

    # News Site discovery leans on Firecrawl for its /map and /scrape fallbacks
    # when no RSS/WP feed is found. Without a key those fallbacks silently no-op.
    if not current_app.config.get('FIRECRAWL_API_KEY'):
        findings.append(_finding(
            'news_no_firecrawl', SEVERITY_WARNING, CATEGORY_CONFIG,
            'Firecrawl not configured',
            'News Site sources fall back to Firecrawl when no RSS/WordPress feed '
            'is auto-discovered. FIRECRAWL_API_KEY is not set, so those sites may '
            'yield nothing.',
            'Set FIRECRAWL_API_KEY, or prefer a direct RSS Feed source for this site.',
            source=source,
        ))

    _append_news_redirect(findings, source, url, redirect)
    return findings


def _append_news_redirect(findings, source, url, redirect):
    """Advisory redirect finding for a News Site source."""
    if redirect:
        findings.append(_finding(
            'news_redirect', SEVERITY_INFO, CATEGORY_CONFIG,
            'Site URL permanently redirects',
            f'{url} permanently redirects to {redirect}.',
            'Update the stored URL to the destination.',
            source=source,
            action={'type': ACTION_UPDATE_URL,
                    'params': {'source_id': source.id, 'url': redirect}},
        ))


def _firecrawl_reachable(url):
    """True if Firecrawl can fetch real content from the URL.

    Mirrors the News Site scraper's own fetch path, so it clears bot-protection
    false positives: if the browser-rendered Firecrawl fetch gets in, the real
    pipeline will too. Only called for the handful of sources that already
    returned a blocked status, so credit spend stays bounded. Never raises.
    """
    api_key = current_app.config.get('FIRECRAWL_API_KEY')
    if not api_key:
        return False
    try:
        from app.research.enrichment import _firecrawl_scrape
        data = _firecrawl_scrape(url, api_key)
    except Exception as e:
        logger.warning(f'Audit Firecrawl reachability check failed for {url}: {e}')
        return False
    if not data:
        return False
    markdown = (data.get('markdown') or '').strip()
    return len(markdown) > 50  # got real page content, not an empty/blocked shell


def _parse_terms(keywords):
    """Split the keywords blob the same way the scrapers do; dedup, preserve order."""
    terms = [t.strip() for t in re.split(r'[,\n\r]+', keywords or '') if t.strip()]
    seen, unique = set(), []
    for t in terms:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            unique.append(t)
    return terms, unique


def _probe_keyword_common(source, engine_label):
    """Config checks shared by Keyword Search and YouTube sources.

    Returns ``(findings, unique_terms, blocked)``. ``blocked`` is True when a
    live query would be pointless (no terms or no API key).
    """
    findings = []
    raw_terms, unique_terms = _parse_terms(source.keywords)

    if not unique_terms:
        findings.append(_finding(
            'kw_no_terms', SEVERITY_CRITICAL, CATEGORY_CONFIG,
            f'{engine_label} source has no keywords',
            'This source parses to zero search terms, so it queries nothing.',
            'Add one search term per line (or comma-separated).',
            source=source,
            action={'type': ACTION_DEACTIVATE_SOURCE, 'params': {'source_id': source.id}},
        ))

    if len(raw_terms) != len(unique_terms):
        findings.append(_finding(
            'kw_dup_terms', SEVERITY_INFO, CATEGORY_CONFIG,
            'Duplicate keywords',
            f'{len(raw_terms) - len(unique_terms)} duplicate term(s) will be '
            'de-duplicated at query time.',
            'Remove the duplicates to keep the list tidy.',
            source=source,
        ))

    if source.url and source.url.strip():
        findings.append(_finding(
            'kw_url_ignored', SEVERITY_INFO, CATEGORY_CONFIG,
            'URL is ignored for this source type',
            f'{engine_label} sources search by keyword; the URL field '
            f'("{source.url}") is not used.',
            'Clear the URL to avoid confusion, or convert the source to an RSS/News type.',
            source=source,
        ))

    blocked = not unique_terms
    if not current_app.config.get('SERPAPI_API_KEY'):
        findings.append(_finding(
            'kw_no_serpapi', SEVERITY_CRITICAL, CATEGORY_CONFIG,
            'SERPAPI_API_KEY not configured',
            f'{engine_label} sources require SerpAPI. Without the key they yield '
            'nothing on every run.',
            'Set SERPAPI_API_KEY in the environment.',
            source=source,
        ))
        blocked = True

    return findings, unique_terms, blocked


def _serpapi_probe(params):
    """Run one minimal SerpAPI query. Returns ``(ok, detail)``. Never raises."""
    try:
        from serpapi import GoogleSearch
    except Exception as e:
        return False, f'serpapi client unavailable: {e}'
    try:
        results = GoogleSearch(params).get_dict()
    except Exception as e:
        return False, f'query failed: {e}'
    if results.get('error'):
        return False, str(results.get('error'))
    return True, ''


def _probe_keyword_search(source, live):
    findings, terms, blocked = _probe_keyword_common(source, 'Keyword Search')
    if live and not blocked:
        params = {
            'engine': 'google_news', 'q': terms[0],
            'gl': 'us', 'hl': 'en',
            'api_key': current_app.config.get('SERPAPI_API_KEY'),
        }
        ok, detail = _serpapi_probe(params)
        if not ok:
            findings.append(_finding(
                'kw_live_failed', SEVERITY_WARNING, CATEGORY_TECHNICAL,
                'Live keyword test returned no results',
                f'A test Google News query for "{terms[0]}" did not succeed: {detail}',
                'Check the SerpAPI key/quota and that the term is searchable.',
                source=source,
            ))
    return findings


def _probe_youtube(source, live):
    findings, terms, blocked = _probe_keyword_common(source, 'YouTube Keywords')
    if live and not blocked:
        params = {
            'engine': 'youtube', 'search_query': terms[0],
            'gl': 'us', 'hl': 'en',
            'api_key': current_app.config.get('SERPAPI_API_KEY'),
        }
        ok, detail = _serpapi_probe(params)
        if not ok:
            findings.append(_finding(
                'yt_live_failed', SEVERITY_WARNING, CATEGORY_TECHNICAL,
                'Live YouTube test returned no results',
                f'A test YouTube query for "{terms[0]}" did not succeed: {detail}',
                'Check the SerpAPI key/quota and that the term is searchable.',
                source=source,
            ))
    return findings


def _probe_data(source):
    """Validate a Data source's config. Pure — no network, no write-back."""
    findings = []
    config = source.config
    if not config or not isinstance(config, dict):
        findings.append(_finding(
            'data_no_config', SEVERITY_CRITICAL, CATEGORY_CONFIG,
            'Data source has no configuration',
            'Data sources are driven entirely by their JSON config, which is empty.',
            'Add the required config (discovery_mode, document_type, report_name, '
            'publisher, cadence, analysis_prompt) or deactivate the source.',
            source=source,
            action={'type': ACTION_DEACTIVATE_SOURCE, 'params': {'source_id': source.id}},
        ))
        return findings

    missing = sorted(_DATA_REQUIRED_FIELDS - set(config.keys()))
    if missing:
        findings.append(_finding(
            'data_missing_fields', SEVERITY_CRITICAL, CATEGORY_CONFIG,
            'Data config is missing required fields',
            f'Missing: {", ".join(missing)}. DataScraper rejects this config at '
            'run time, so the source yields nothing.',
            'Add the missing fields (see the Data Source Configuration Reference '
            'on the source form).',
            source=source,
        ))

    mode = config.get('discovery_mode')
    mode_problems = {
        'url_pattern': ('url_pattern' not in config, "url_pattern mode needs a 'url_pattern' with a {MMYY} placeholder"),
        'landing_page': (not config.get('landing_page_url') and not source.url, "landing_page mode needs 'landing_page_url' (or a source URL)"),
    }
    if mode == 'api':
        if not config.get('api_url'):
            findings.append(_finding(
                'data_api_no_url', SEVERITY_CRITICAL, CATEGORY_CONFIG,
                "Data 'api' mode is missing api_url",
                "discovery_mode is 'api' but no 'api_url' is set.",
                "Add 'api_url' (and 'pdf_json_path') to the config.",
                source=source,
            ))
        if not config.get('pdf_json_path'):
            findings.append(_finding(
                'data_api_no_path', SEVERITY_CRITICAL, CATEGORY_CONFIG,
                "Data 'api' mode is missing pdf_json_path",
                "discovery_mode is 'api' but no 'pdf_json_path' is set.",
                "Add 'pdf_json_path' so the PDF URL can be extracted from the API response.",
                source=source,
            ))
    elif mode in mode_problems:
        bad, msg = mode_problems[mode]
        if bad:
            findings.append(_finding(
                f'data_{mode}_incomplete', SEVERITY_CRITICAL, CATEGORY_CONFIG,
                f"Data '{mode}' mode is incomplete",
                msg + '.',
                'Complete the mode-specific config fields.',
                source=source,
            ))
    elif mode is not None and 'discovery_mode' not in missing:
        findings.append(_finding(
            'data_bad_mode', SEVERITY_WARNING, CATEGORY_CONFIG,
            'Unknown discovery_mode',
            f"discovery_mode '{mode}' is not one of url_pattern, landing_page, api.",
            'Set discovery_mode to a supported value.',
            source=source,
        ))

    if not current_app.config.get('ANTHROPIC_API_KEY'):
        findings.append(_finding(
            'data_no_anthropic', SEVERITY_WARNING, CATEGORY_CONFIG,
            'ANTHROPIC_API_KEY not configured',
            'Data sources analyse reports with Claude; without the key they yield nothing.',
            'Set ANTHROPIC_API_KEY in the environment.',
            source=source,
        ))

    return findings


# Dispatch table for per-type technical probes. Keyword/YouTube take ``live``.
_TYPE_PROBES = {
    SourceType.RSS_FEED.value: lambda s, live: _probe_rss(s),
    SourceType.NEWS_SITE.value: lambda s, live: _probe_news_site(s),
    'News': lambda s, live: _probe_news_site(s),
    SourceType.KEYWORD_SEARCH.value: lambda s, live: _probe_keyword_search(s, live),
    SourceType.YOUTUBE_KEYWORDS.value: lambda s, live: _probe_youtube(s, live),
    SourceType.DATA.value: lambda s, live: _probe_data(s),
}


def _check_source_type(source):
    """Config-only checks about the source_type value itself."""
    findings = []
    stype = (source.source_type or '').strip()

    if not stype:
        findings.append(_finding(
            'type_blank', SEVERITY_CRITICAL, CATEGORY_CONFIG,
            'Source has no type',
            'The source_type is blank, so no scraper handles it — it discovers nothing.',
            'Set a source type on the source.',
            source=source,
        ))
        return findings

    if stype not in VALID_SOURCE_TYPES and stype != 'News':
        findings.append(_finding(
            'type_unknown', SEVERITY_CRITICAL, CATEGORY_CONFIG,
            'Unrecognised source type',
            f'source_type "{stype}" is not a known type. It gets the default '
            'scoring weight (0.5) and, unless it matches a scraper alias, '
            'discovers nothing.',
            'Change the type to a supported value.',
            source=source,
        ))
        return findings

    if stype in (SourceType.COMPETITOR.value, SourceType.HOUSE_CONTENT.value):
        findings.append(_finding(
            'type_non_discovering', SEVERITY_INFO, CATEGORY_CONFIG,
            f'"{stype}" sources do not discover content',
            f'{stype} sources have no scraper; they are used only to shape '
            'domain exclusion during research. This is expected, not a fault.',
            'No action needed unless you expected this source to bring in articles.',
            source=source,
        ))

    return findings


# --- Cross-source checks ----------------------------------------------------

def _check_duplicates(sources):
    """Flag active sources that share a normalised URL."""
    findings = []
    by_url = {}
    for s in sources:
        if not s.is_active or not (s.url or '').strip():
            continue
        key = s.url.strip().rstrip('/').lower()
        by_url.setdefault(key, []).append(s)

    for url_key, group in by_url.items():
        if len(group) < 2:
            continue
        keep, *dupes = sorted(group, key=lambda s: s.id)
        for dupe in dupes:
            findings.append(_finding(
                'dup_url', SEVERITY_WARNING, CATEGORY_CONFIG,
                'Duplicate source URL',
                f'"{dupe.name}" (#{dupe.id}) shares URL {dupe.url} with '
                f'"{keep.name}" (#{keep.id}).',
                'Remove the duplicate to avoid redundant fetches and double-counting.',
                source=dupe,
                action={'type': ACTION_DELETE_DUPLICATE,
                        'params': {'source_id': dupe.id, 'kept_source_id': keep.id}},
            ))
    return findings


# --- Yield check ------------------------------------------------------------

def check_yield(publication_id, sources):
    """Flag active, content-discovering sources that produced no candidates recently.

    Uses the CandidateArticle history — the same signal the admin source-stats
    view aggregates — cross-referencing type so we only flag sources that are
    *supposed* to yield content.
    """
    findings = []
    cutoff = datetime.utcnow() - timedelta(days=YIELD_WINDOW_DAYS)

    active_discovering = [
        s for s in sources
        if s.is_active and (s.source_type or '') in DISCOVERING_TYPES
    ]
    if not active_discovering:
        return findings

    counts = dict(
        db.session.query(
            CandidateArticle.news_source_id,
            db.func.count(CandidateArticle.id),
        )
        .filter(
            CandidateArticle.publication_id == publication_id,
            CandidateArticle.news_source_id.in_([s.id for s in active_discovering]),
            CandidateArticle.discovered_at >= cutoff,
        )
        .group_by(CandidateArticle.news_source_id)
        .all()
    )

    for s in active_discovering:
        if counts.get(s.id, 0) == 0:
            findings.append(_finding(
                'yield_zero', SEVERITY_WARNING, CATEGORY_YIELD,
                'No candidates produced recently',
                f'"{s.name}" has produced no candidate articles in the last '
                f'{YIELD_WINDOW_DAYS} days.',
                'Cross-check the technical findings for this source: a passing '
                'probe means it is on-topic but quiet; a failing probe means it '
                'is broken. Consider replacing persistently silent sources.',
                source=s,
            ))
    return findings


# --- Scoring ----------------------------------------------------------------

def compute_health_score(findings, sources):
    """0-100 technical-health score over active sources.

    Each active source is penalised by its worst finding: a critical finding
    counts as fully broken, a warning as partially degraded. Info findings and
    non-active sources do not affect the score. Returns None with no active
    sources.
    """
    active = [s for s in sources if s.is_active]
    if not active:
        return None

    worst = {}  # source_id -> penalty weight
    weight = {SEVERITY_CRITICAL: 1.0, SEVERITY_WARNING: 0.4, SEVERITY_INFO: 0.0}
    for f in findings:
        if f['category'] == CATEGORY_COVERAGE or f['source_id'] is None:
            continue
        w = weight.get(f['severity'], 0.0)
        if w > worst.get(f['source_id'], 0.0):
            worst[f['source_id']] = w

    penalty = sum(worst.get(s.id, 0.0) for s in active)
    return int(round(max(0.0, 100.0 * (1.0 - penalty / len(active)))))


# --- Entry point ------------------------------------------------------------

def run_technical_audit(publication, sources, live=True):
    """Run all deterministic (technical + config + yield) checks.

    Args:
        publication: the Publication being audited.
        sources: list of its NewsSource rows.
        live: when True, keyword/YouTube sources get one minimal SerpAPI query.
              Set False for offline/unit runs — no external calls are made.

    Returns a flat list of finding dicts.
    """
    findings = []
    for source in sources:
        findings.extend(_check_source_type(source))

        if not source.is_active:
            # Dormant sources get a note but are not probed over the network.
            findings.append(_finding(
                'source_inactive', SEVERITY_INFO, CATEGORY_CONFIG,
                'Source is inactive',
                f'"{source.name}" is disabled and is skipped during research.',
                'Reactivate it if it should be contributing, or delete it if obsolete.',
                source=source,
            ))
            continue

        probe = _TYPE_PROBES.get((source.source_type or '').strip())
        if probe is None:
            # Non-discovering / unknown types already reported by _check_source_type.
            continue
        try:
            findings.extend(probe(source, live))
        except Exception as e:  # a probe must never break the whole audit
            logger.error(f'Source audit probe failed for source {source.id}: {e}',
                         exc_info=True)
            findings.append(_finding(
                'probe_error', SEVERITY_INFO, CATEGORY_TECHNICAL,
                'Could not fully probe this source',
                f'An unexpected error occurred while checking "{source.name}": {e}',
                'This is an audit-side error, not necessarily a source fault.',
                source=source,
            ))

    findings.extend(_check_duplicates(sources))
    findings.extend(check_yield(publication.id, sources))
    return findings
