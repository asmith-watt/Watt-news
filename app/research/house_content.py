"""Index of what the publication (and its sister sites) already published.

Triage compares each candidate against this index so stories we've already
run are rejected before they reach an editor or burn enrichment credits.

The index is built once per research run from two places:

1. The Mindful CMS Query API (GraphQL). One request lists every NEWS post
   published in the namespace during the lookback window, across all of the
   namespace's websites. Posts on the House Content source's own hostname are
   "own"; posts on other hostnames are "sister".
2. This app's own recent NewsContent rows, which include drafts and pieces
   scheduled but not yet live in the CMS. These count as "own".

The House Content source switches the check on. Its `config` may set:

    {
      "lookback_days": 14,              # how far back to compare
      "include_sister_sites": true,     # also flag stories run on sister sites
      "mindful_namespace": "watt/default"
    }

Any failure (network, schema change, auth) degrades to whatever was fetched,
and the research run carries on without the check rather than failing.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import urlparse

import requests
from flask import current_app

logger = logging.getLogger(__name__)

HOUSE_CONTENT_TYPE = 'House Content'
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_NAMESPACE = 'watt/default'
# Hard cap on posts pulled from the CMS. A two-week window across all WATT
# sites is ~200 news posts; this only guards against a misconfigured window.
MAX_CMS_POSTS = 600
_PAGE_SIZE = 200

_MINDFUL_QUERY = """
query HouseContent($dates: [PostInterfaceSearchDatesInput!], $pagination: SearchPaginationInput) {
  postInterfaceSearchConnection(
    dates: $dates,
    displayTypeIds: ["NEWS"],
    omitScheduledAndExpiredContent: true,
    sort: {field: PUBLISHED_DATE, order: DESC},
    pagination: $pagination
  ) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        nameOrTitle { default }
        deck
        publishedDateTime
        canonicalWebsiteUrl
        canonicalWebsiteChannel { embeddedHostname { root } }
      }
    }
  }
}
"""


def _bare_host(host: Optional[str]) -> str:
    host = (host or '').lower().strip()
    return host[4:] if host.startswith('www.') else host


def _normalize_title(title: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (title or '').lower()).strip()


def house_sources(sources) -> list:
    """Active House Content sources from a list of NewsSource rows."""
    return [s for s in sources if s.source_type == HOUSE_CONTENT_TYPE and s.is_active]


def build_house_index(publication, sources) -> List[dict]:
    """Build the list of recently published house articles for triage.

    Returns a list of dicts, newest first:
        {id, title, deck, url, site, published, scope}
    where scope is 'own' or 'sister' and id is a short label ('H1', 'H2', ...)
    the triage model cites back. Returns [] when the publication has no active
    House Content source.
    """
    hc_sources = house_sources(sources)
    if not hc_sources:
        return []

    # One House Content source per publication is the norm; if there are
    # several, merge their own-hostnames and take the widest settings.
    own_hosts = set()
    if publication.publication_domain:
        own_hosts.add(_bare_host(publication.publication_domain))
    lookback_days = DEFAULT_LOOKBACK_DAYS
    include_sisters = False
    namespaces = set()
    for s in hc_sources:
        cfg = s.config if isinstance(s.config, dict) else {}
        if s.url:
            own_hosts.add(_bare_host(urlparse(s.url).netloc or s.url))
        try:
            lookback_days = max(lookback_days, int(cfg.get('lookback_days', DEFAULT_LOOKBACK_DAYS)))
        except (TypeError, ValueError):
            pass
        include_sisters = include_sisters or bool(cfg.get('include_sister_sites', True))
        namespaces.add(cfg.get('mindful_namespace') or DEFAULT_NAMESPACE)
    own_hosts.discard('')

    since = datetime.utcnow() - timedelta(days=lookback_days)

    entries = []
    for ns in sorted(namespaces):
        entries.extend(_fetch_mindful_posts(ns, since, publication))

    for e in entries:
        e['scope'] = 'own' if _bare_host(e['site']) in own_hosts else 'sister'
    if not include_sisters:
        entries = [e for e in entries if e['scope'] == 'own']

    # Our own pipeline's recent articles, minus any already seen via the CMS.
    seen_titles = {_normalize_title(e['title']) for e in entries}
    for e in _recent_pipeline_content(publication.id, since):
        key = _normalize_title(e['title'])
        if key and key not in seen_titles:
            seen_titles.add(key)
            entries.append(e)

    entries.sort(key=lambda e: e['published'] or '', reverse=True)
    for i, e in enumerate(entries, start=1):
        e['id'] = f'H{i}'

    logger.info(
        f"House index for pub {publication.id}: {len(entries)} articles "
        f"({sum(1 for e in entries if e['scope'] == 'own')} own, "
        f"{sum(1 for e in entries if e['scope'] == 'sister')} sister) "
        f"over {lookback_days} days, own hosts={sorted(own_hosts)}"
    )
    return entries


def _fetch_mindful_posts(namespace: str, since: datetime, publication) -> List[dict]:
    """List NEWS posts published since `since` across the namespace's websites."""
    url = current_app.config.get('MINDFUL_QUERY_URL', 'https://graphql.mindfulcms.com/query')
    headers = {'Content-Type': 'application/json', 'x-namespace': namespace}

    variables = {
        'dates': [{'field': 'PUBLISHED_DATE', 'after': since.strftime('%Y-%m-%dT%H:%M:%SZ')}],
        'pagination': {'limit': _PAGE_SIZE},
    }

    posts = []
    tried_auth = False
    while len(posts) < MAX_CMS_POSTS:
        try:
            resp = requests.post(url, json={'query': _MINDFUL_QUERY, 'variables': variables},
                                 headers=headers, timeout=30)
            # The Query API currently answers without a token. If that changes,
            # retry once with the publication's CMS key (the one used to push).
            if resp.status_code in (401, 403) and not tried_auth and publication.cms_api_key:
                tried_auth = True
                key = publication.cms_api_key
                headers['Authorization'] = key if key.lower().startswith('bearer ') else f'Bearer {key}'
                continue
            resp.raise_for_status()
            body = resp.json()
        except Exception as e:
            logger.warning(f"Mindful house-content query failed (namespace {namespace}): {e}")
            break

        if body.get('errors'):
            logger.warning(f"Mindful house-content query errors (namespace {namespace}): {body['errors']}")
            break

        conn = (body.get('data') or {}).get('postInterfaceSearchConnection') or {}
        for edge in conn.get('edges') or []:
            node = edge.get('node') or {}
            title = ((node.get('nameOrTitle') or {}).get('default') or '').strip()
            if not title:
                continue
            channel = node.get('canonicalWebsiteChannel') or {}
            site = ((channel.get('embeddedHostname') or {}).get('root') or '').lower()
            page_url = node.get('canonicalWebsiteUrl') or ''
            if not site and page_url:
                site = urlparse(page_url).netloc.lower()
            posts.append({
                'title': title,
                'deck': _clean_deck(node.get('deck')),
                'url': page_url,
                'site': site,
                'published': (node.get('publishedDateTime') or '')[:10],
            })

        page_info = conn.get('pageInfo') or {}
        if not page_info.get('hasNextPage') or not page_info.get('endCursor'):
            break
        variables['pagination'] = {
            'limit': _PAGE_SIZE,
            'cursor': {'value': page_info['endCursor'], 'direction': 'AFTER'},
        }

    return posts[:MAX_CMS_POSTS]


def _clean_deck(deck) -> str:
    """Decks can carry HTML; keep a short plain-text version for the prompt."""
    if not deck or not isinstance(deck, str):
        return ''
    text = re.sub(r'<[^>]+>', ' ', deck)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:200]


def _recent_pipeline_content(publication_id: int, since: datetime) -> List[dict]:
    """Recent articles generated in this app, including unpublished drafts."""
    from sqlalchemy import or_
    from app.models import NewsContent

    rows = (
        NewsContent.query
        .with_entities(NewsContent.title, NewsContent.deck, NewsContent.created_at,
                       NewsContent.status)
        .filter(
            NewsContent.publication_id == publication_id,
            NewsContent.created_at >= since,
            or_(NewsContent.status.is_(None), NewsContent.status != 'rejected'),
        )
        .all()
    )
    return [
        {
            'title': r.title,
            'deck': _clean_deck(r.deck),
            'url': '',
            'site': 'draft in WATT Automation',
            'published': r.created_at.strftime('%Y-%m-%d') if r.created_at else '',
            'scope': 'own',
        }
        for r in rows if r.title
    ]


def format_house_index(entries: List[dict]) -> str:
    """Render the index as compact lines for the triage system prompt."""
    lines = []
    for e in entries:
        where = 'OWN' if e['scope'] == 'own' else f"SISTER {_bare_host(e['site'])}"
        line = f"[{e['id']}] {e['published'] or '????-??-??'} ({where}) {e['title']}"
        if e.get('deck'):
            line += f" — {e['deck']}"
        lines.append(line)
    return '\n'.join(lines)
