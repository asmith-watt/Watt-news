"""LLM-backed strategic coverage assessment for the source audit.

Answers the question the deterministic probes can't: *given what this
publication is about, is it watching the right places?* Uses Claude with the
native server-side web search tool so suggested new sources are grounded in
real, current URLs rather than recalled from training data. Every suggested
URL is then probed (via :func:`app.research.audit._fetch`) before it can become
a one-click "add source" action, so the report never ships a dead link.

Degrades gracefully at every step: no API key → empty coverage (technical audit
still runs); model/search failure → whatever was parsed, or empty; a suggestion
whose URL fails the probe → advisory "unverified" note instead of an add action.
"""
import json
import logging
import re

from flask import current_app

from app.models import SourceType
from app.research import audit
from app.research.audit import (
    _finding, _fetch, _has_scheme,
    SEVERITY_WARNING, SEVERITY_INFO, CATEGORY_COVERAGE, ACTION_ADD_SOURCE,
)

logger = logging.getLogger(__name__)

# Web search tool — the _20260209 variant (dynamic filtering) needs Opus 4.8/4.7/4.6
# or Sonnet 5/4.6, which is why the default model below is Opus 4.8.
_WEB_SEARCH_TOOL = {'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 8}

# Source types a suggestion may propose (must be addable via the source form).
_SUGGESTABLE_TYPES = {
    SourceType.RSS_FEED.value,
    SourceType.NEWS_SITE.value,
    SourceType.KEYWORD_SEARCH.value,
    SourceType.YOUTUBE_KEYWORDS.value,
}

_MAX_CONTINUATIONS = 6  # bound the server-tool pause_turn loop


def run_coverage_audit(publication, sources, live=True):
    """Assess strategic coverage. Returns ``{coverage_score, summary, findings}``.

    ``coverage_score`` is 0-100 or None. ``findings`` is a list in the same
    shape the deterministic audit produces. When ``live`` is False (offline /
    unit runs) this makes no network calls and returns an empty result.
    """
    empty = {'coverage_score': None, 'summary': None, 'findings': []}
    if not live:
        return empty

    api_key = current_app.config.get('ANTHROPIC_API_KEY')
    if not api_key:
        logger.info('Coverage audit skipped: ANTHROPIC_API_KEY not configured')
        return empty

    if not (publication.industry_description or '').strip():
        # Without an industry description there's nothing to judge coverage against.
        return {
            'coverage_score': None,
            'summary': 'Coverage not assessed: the publication has no industry '
                       'description to judge sources against.',
            'findings': [_finding(
                'coverage_no_industry', SEVERITY_INFO, CATEGORY_COVERAGE,
                'No industry description set',
                'The publication has no industry_description, so the coverage '
                'assessment cannot determine whether the right places are watched.',
                'Add an industry description on the publication to enable coverage auditing.',
            )],
        }

    analysis = _analyze(publication, sources, api_key)
    if analysis is None:
        return {
            'coverage_score': None,
            'summary': 'Coverage assessment could not be completed.',
            'findings': [],
        }

    findings = _analysis_to_findings(analysis, sources)
    score = _clamp_score(analysis.get('coverage_score'))
    return {
        'coverage_score': score,
        'summary': (analysis.get('summary') or '').strip() or None,
        'findings': findings,
    }


# --- LLM call ---------------------------------------------------------------

def _analyze(publication, sources, api_key):
    """Run the Claude web-search analysis. Returns the parsed dict or None."""
    import anthropic

    model = current_app.config.get('AUDIT_COVERAGE_MODEL', 'claude-opus-4-8')
    system = _system_prompt()
    user = _user_message(publication, sources)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        messages = [{'role': 'user', 'content': user}]
        response = None
        for _ in range(_MAX_CONTINUATIONS):
            response = client.messages.create(
                model=model,
                max_tokens=8000,
                system=system,
                messages=messages,
                tools=[_WEB_SEARCH_TOOL],
                thinking={'type': 'adaptive'},
                output_config={'effort': 'medium'},
            )
            # The web search tool runs server-side; a long search loop can stop
            # with pause_turn. Echo the content back verbatim to resume.
            if response.stop_reason == 'pause_turn':
                messages.append({'role': 'assistant', 'content': response.content})
                continue
            break
        return _parse_response(response)
    except Exception as e:
        logger.error(f'Coverage audit LLM call failed: {e}', exc_info=True)
        return None


def _system_prompt():
    return (
        "You are a source-strategy analyst for a trade publication. Your job is "
        "to assess whether the publication's configured news sources give it the "
        "right coverage for its stated industry and readers, and to recommend "
        "specific, real sources to fill gaps.\n\n"
        "Use the web_search tool to find CURRENT, REAL sources with working URLs "
        "— regulators, trade bodies, primary journals, key competitors, "
        "conference/industry feeds, and leading outlets in the space. Only "
        "recommend sources you have grounds to believe exist; prefer a source's "
        "RSS/feed URL when you can identify one.\n\n"
        "Assess three things:\n"
        "1. Drift — configured sources that are off-topic for the stated industry.\n"
        "2. Imbalance — the mix over-indexes on one publisher or one source type.\n"
        "3. Gaps — categories of coverage that are missing, each with concrete, "
        "real source suggestions.\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no prose) of shape:\n"
        "{\n"
        '  "coverage_score": <int 0-100, higher = better coverage of the industry>,\n'
        '  "summary": "<2-3 sentence overview>",\n'
        '  "drift": [{"source_name": "<exact configured source name>", "reason": "<why off-topic>"}],\n'
        '  "imbalance": "<one sentence, or null>",\n'
        '  "gaps": [\n'
        '    {"category": "<e.g. Regulators>", "rationale": "<why it matters>",\n'
        '     "suggestions": [\n'
        '       {"name": "<source name>", "url": "<https URL>",\n'
        '        "source_type": "RSS Feed|News Site|Keyword Search|YouTube Keywords",\n'
        '        "keywords": "<terms, only for Keyword Search / YouTube Keywords, else omit>",\n'
        '        "reason": "<why this source>"}\n'
        "     ]}\n"
        "  ]\n"
        "}\n"
        "Use the exact source_type strings shown. For Keyword Search / YouTube "
        "Keywords suggestions the url may be omitted. Output only the JSON object."
    )


def _user_message(publication, sources):
    lines = [
        f"## Publication\nName: {publication.name}",
        f"\n## Industry description\n{publication.industry_description or 'Not specified'}",
        f"\n## Reader personas\n{publication.reader_personas or 'Not specified'}",
        f"\n## Reader pain points\n{publication.reader_pain_points or 'Not specified'}",
        "\n## Currently configured sources",
    ]
    if not sources:
        lines.append("(none)")
    for s in sources:
        detail = s.url or (s.keywords or '').replace('\n', ', ') or '—'
        state = '' if s.is_active else ' [inactive]'
        lines.append(f"- {s.name} ({s.source_type or 'no type'}){state}: {detail}")
    lines.append(
        "\nAssess coverage for this publication and recommend real sources to "
        "fill gaps. Respond with only the JSON object."
    )
    return '\n'.join(lines)


def _parse_response(response):
    """Extract and parse the JSON object from the model's text blocks."""
    if response is None:
        return None
    text = '\n'.join(
        b.text for b in response.content
        if getattr(b, 'type', None) == 'text' and getattr(b, 'text', None)
    ).strip()
    if not text:
        logger.warning(f'Coverage audit: empty response text (stop_reason={response.stop_reason})')
        return None

    # Strip markdown fences if the model added them despite instructions.
    if '```' in text:
        fenced = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        obj = re.search(r'\{.*\}', text, re.DOTALL)
        if not obj:
            logger.warning('Coverage audit: no JSON object in response')
            return None
        try:
            data = json.loads(obj.group(0))
        except json.JSONDecodeError as e:
            logger.warning(f'Coverage audit: could not parse JSON: {e}')
            return None

    return data if isinstance(data, dict) else None


# --- Findings assembly ------------------------------------------------------

def _analysis_to_findings(analysis, sources):
    findings = []
    name_to_id = {(s.name or '').strip().lower(): s.id for s in sources}

    # Drift — configured sources judged off-topic.
    for item in analysis.get('drift') or []:
        if not isinstance(item, dict):
            continue
        sname = (item.get('source_name') or '').strip()
        sid = name_to_id.get(sname.lower())
        findings.append(_finding(
            'coverage_drift', SEVERITY_WARNING, CATEGORY_COVERAGE,
            f'Possible off-topic source: {sname or "unnamed"}',
            item.get('reason') or 'Flagged as not fitting the stated industry.',
            'Review whether this source still fits the publication; retire it if not.',
        ) | {'source_id': sid, 'source_name': sname or None})

    # Imbalance — a single advisory note.
    imbalance = analysis.get('imbalance')
    if isinstance(imbalance, str) and imbalance.strip():
        findings.append(_finding(
            'coverage_imbalance', SEVERITY_INFO, CATEGORY_COVERAGE,
            'Source mix imbalance',
            imbalance.strip(),
            'Diversify sources across publishers and source types to reduce blind spots.',
        ))

    # Gaps + concrete suggestions.
    for gap in analysis.get('gaps') or []:
        if not isinstance(gap, dict):
            continue
        category = (gap.get('category') or 'Coverage gap').strip()
        findings.append(_finding(
            'coverage_gap', SEVERITY_WARNING, CATEGORY_COVERAGE,
            f'Coverage gap: {category}',
            gap.get('rationale') or 'This category is not currently covered.',
            'Add one or more sources for this category (see suggestions below).',
        ))
        for sug in gap.get('suggestions') or []:
            f = _suggestion_finding(sug, category)
            if f:
                findings.append(f)

    return findings


def _suggestion_finding(sug, category):
    """Turn one suggestion into a finding, probing its URL first."""
    if not isinstance(sug, dict):
        return None
    name = (sug.get('name') or '').strip()
    if not name:
        return None
    url = (sug.get('url') or '').strip()
    stype = (sug.get('source_type') or '').strip()
    keywords = (sug.get('keywords') or '').strip()
    reason = (sug.get('reason') or '').strip()

    if stype not in _SUGGESTABLE_TYPES:
        stype = SourceType.NEWS_SITE.value if url else SourceType.KEYWORD_SEARCH.value

    keyword_type = stype in (SourceType.KEYWORD_SEARCH.value, SourceType.YOUTUBE_KEYWORDS.value)

    # Keyword/YouTube suggestions don't need a URL — they're addable as-is.
    if keyword_type and not url:
        if not keywords:
            return None
        return _finding(
            'coverage_suggestion', SEVERITY_INFO, CATEGORY_COVERAGE,
            f'Suggested source: {name}',
            f'[{category}] {reason}'.strip(),
            'Add this keyword source to broaden coverage.',
            action={'type': ACTION_ADD_SOURCE, 'params': {
                'name': name, 'source_type': stype, 'keywords': keywords, 'url': None,
            }},
        )

    # URL-bearing suggestion: verify it's reachable before offering to add it.
    verified = url and _has_scheme(url) and _probe_reachable(url)
    if verified:
        return _finding(
            'coverage_suggestion', SEVERITY_INFO, CATEGORY_COVERAGE,
            f'Suggested source: {name}',
            f'[{category}] {reason} ({url})'.strip(),
            'Add this source to fill the coverage gap.',
            action={'type': ACTION_ADD_SOURCE, 'params': {
                'name': name, 'source_type': stype, 'url': url,
                'keywords': keywords or None,
            }},
        )

    # Unverifiable URL — surface the idea but don't offer a one-click add.
    return _finding(
        'coverage_suggestion_unverified', SEVERITY_INFO, CATEGORY_COVERAGE,
        f'Suggested source (unverified): {name}',
        f'[{category}] {reason}'.strip()
        + (f' Proposed URL {url} could not be reached — verify it before adding.'
           if url else ' No URL was proposed.'),
        'Confirm the source and add it manually if it fits.',
    )


def _clamp_score(value):
    """Coerce a model-supplied score to an int in [0, 100], or None."""
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return None


def _probe_reachable(url):
    """True if the URL responds with a non-error status. Never raises."""
    resp, error = _fetch(url)
    if error or resp is None:
        return False
    try:
        return resp.status_code < 400
    finally:
        resp.close()
