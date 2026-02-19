"""Heuristic scoring for candidate articles."""
import re
from datetime import datetime


# Source type weights (0-1)
SOURCE_WEIGHTS = {
    'RSS Feed': 1.0,
    'News Site': 0.9,
    'News': 0.9,
    'Keyword Search': 0.8,
    'YouTube Keywords': 0.75,
    'Competitor': 0.7,
    'Data': 0.85,
    'House Content': 0.3,
}


def _extract_terms(text: str) -> list:
    """Extract meaningful words from text, lowercased and deduplicated."""
    if not text:
        return []
    words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    # Filter common stop words
    stop_words = {
        'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can',
        'her', 'was', 'one', 'our', 'out', 'has', 'have', 'been', 'from',
        'with', 'they', 'this', 'that', 'will', 'each', 'which', 'their',
        'about', 'would', 'there', 'these', 'other', 'into', 'more', 'some',
    }
    return [w for w in words if w not in stop_words]


def compute_keyword_score(title: str, snippet: str, industry_description: str, source_keywords: str) -> float:
    """Score 0-100 based on keyword matches between candidate text and publication/source terms."""
    terms = _extract_terms(industry_description) + _extract_terms(source_keywords)
    if not terms:
        return 50.0  # No keywords configured, neutral score

    unique_terms = list(set(terms))
    candidate_text = f"{title or ''} {snippet or ''}".lower()

    matches = sum(1 for term in unique_terms if term in candidate_text)
    return min((matches / len(unique_terms)) * 100, 100.0)


def compute_recency_score(published_date: datetime) -> float:
    """Score 0-100 based on how recent the article is."""
    if not published_date:
        return 40.0

    now = datetime.utcnow()
    # Handle timezone-aware dates
    if published_date.tzinfo is not None:
        published_date = published_date.replace(tzinfo=None)

    delta = now - published_date
    days = delta.total_seconds() / 86400

    if days < 1:
        return 100.0
    elif days < 2:
        return 85.0
    elif days < 4:
        return 70.0
    elif days < 8:
        return 50.0
    elif days < 15:
        return 30.0
    elif days < 29:
        return 15.0
    else:
        return 5.0


def get_source_weight(source_type: str) -> float:
    """Get the weight for a source type (0-1)."""
    return SOURCE_WEIGHTS.get(source_type, 0.5)


def score_candidate(title: str, snippet: str, published_date: datetime,
                    source_type: str, industry_description: str, source_keywords: str) -> dict:
    """Compute all scores for a candidate article.

    Returns dict with keyword_score, recency_score, source_weight, relevance_score.
    """
    kw_score = compute_keyword_score(title, snippet, industry_description, source_keywords)
    rec_score = compute_recency_score(published_date)
    sw = get_source_weight(source_type)

    if published_date:
        # Standard weights: keyword 50%, recency 30%, source 20%
        relevance = (kw_score * 0.50) + (rec_score * 0.30) + (sw * 100 * 0.20)
    else:
        # No date: redistribute recency weight to keyword and source (keep ratio)
        # keyword 71.4% (0.50/0.70), source 28.6% (0.20/0.70)
        relevance = (kw_score * 0.714) + (sw * 100 * 0.286)

    return {
        'keyword_score': round(kw_score, 2),
        'recency_score': round(rec_score, 2),
        'source_weight': round(sw, 2),
        'relevance_score': round(relevance, 2),
    }
