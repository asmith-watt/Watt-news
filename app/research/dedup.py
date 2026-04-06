"""URL normalization and deduplication for candidate articles."""
import hashlib
import re
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from app.models import CandidateArticle, NewsContent

TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'fbclid', 'gclid', 'ref', 'mc_cid', 'mc_eid',
}


def sanitize_url(url: str) -> str:
    """Fix common URL malformations from broken feeds/scrapers.

    Handles:
    - Missing double slash in scheme (https:/example.com → https://example.com)
    - Doubled domain in path (https://a.com/a.com/page → https://a.com/page)
    """
    if not url:
        return url

    # Fix scheme with single slash: https:/example.com → https://example.com
    url = re.sub(r'^(https?):/?([^/])', r'\1://\2', url)

    # Fix doubled domain in path: https://a.com/a.com/page → https://a.com/page
    parsed = urlparse(url)
    if parsed.netloc and parsed.path.startswith('/' + parsed.netloc):
        fixed_path = parsed.path[len(parsed.netloc) + 1:]
        url = urlunparse((parsed.scheme, parsed.netloc, fixed_path,
                          parsed.params, parsed.query, parsed.fragment))

    return url


def normalize_url(url: str) -> str:
    """Normalize a URL: lowercase scheme/host, strip fragments, remove tracking params, strip trailing slashes."""
    url = sanitize_url(url)
    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip('/')

    # Remove tracking query parameters
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    filtered_params = {
        k: v for k, v in query_params.items()
        if k.lower() not in TRACKING_PARAMS
    }
    query = urlencode(filtered_params, doseq=True)

    return urlunparse((scheme, netloc, path, parsed.params, query, ''))


def url_hash(url: str) -> str:
    """SHA-256 hash of the normalized URL."""
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def is_duplicate_candidate(hash_value: str, publication_id: int) -> bool:
    """Check if a candidate with this url_hash already exists for the publication."""
    return CandidateArticle.query.filter_by(
        publication_id=publication_id,
        url_hash=hash_value,
    ).first() is not None


def is_already_content(url: str, publication_id: int) -> bool:
    """Check if this URL already exists as a generated NewsContent source_url."""
    normalized = normalize_url(url)
    return NewsContent.query.filter(
        NewsContent.publication_id == publication_id,
        NewsContent.source_url.ilike(f'%{normalized}%'),
    ).first() is not None
