"""LLM-based triage for candidate articles.

Sends batches of discovered items to Claude Haiku to classify them as
relevant_news, maybe, or not_news before spending Firecrawl credits on
enrichment.  Falls back gracefully — any failure results in 'maybe'
verdicts so the existing heuristic pipeline still runs.
"""
import json
import logging
import re
import time
from typing import Optional

from flask import current_app

logger = logging.getLogger(__name__)

# Source types that skip triage (already curated or always relevant)
_SKIP_TRIAGE_SOURCE_TYPES = {'Data', 'House Content'}

# Claude tool definition for page fetching
_FETCH_PAGE_TOOL = {
    "name": "fetch_page",
    "description": (
        "Fetch the content of a web page to determine if it's a news article. "
        "Use when the title and snippet alone are not enough to classify the item."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"}
        },
        "required": ["url"],
    },
}

# Triage verdict multipliers applied after heuristic scoring
TRIAGE_MULTIPLIERS = {
    'relevant_news': 1.15,
    'maybe': 1.0,
    'not_news': 0.0,
}


def triage_items(items, source_types, industry_description, reader_personas):
    """Classify a list of discovered items using Claude.

    Parameters
    ----------
    items : list[DiscoveredItem]
        Items to triage.
    source_types : list[str]
        Parallel list of source_type strings for each item.
    industry_description : str
        Publication's industry description for context.
    reader_personas : str
        Publication's reader personas for context.

    Returns
    -------
    list[dict]
        One dict per input item: {url, verdict, reasoning}.
        verdict is 'relevant_news', 'maybe', or 'not_news'.
    """
    if not items:
        return []

    api_key = current_app.config.get('ANTHROPIC_API_KEY')
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not configured, skipping triage")
        return [{'url': item.url, 'verdict': 'maybe', 'reasoning': 'triage skipped (no API key)'} for item in items]

    model = current_app.config.get('TRIAGE_MODEL', 'claude-haiku-4-5-20251001')
    batch_size = current_app.config.get('TRIAGE_MAX_BATCH_SIZE', 40)
    fetch_budget = current_app.config.get('TRIAGE_FETCH_BUDGET', 5)

    # Split into batches
    results = []
    total_fetches = 0
    api_calls = 0

    for start in range(0, len(items), batch_size):
        batch_items = items[start:start + batch_size]
        batch_source_types = source_types[start:start + batch_size]
        remaining_fetch_budget = max(0, fetch_budget - total_fetches)

        batch_results, fetches = _triage_batch(
            batch_items,
            batch_source_types,
            industry_description,
            reader_personas,
            api_key,
            model,
            remaining_fetch_budget,
        )
        results.extend(batch_results)
        total_fetches += fetches
        api_calls += 1

    logger.info(
        f"Triage complete: {len(results)} items, "
        f"{api_calls} API calls, {total_fetches} page fetches"
    )
    return results


def _triage_batch(items, source_types, industry_description, reader_personas,
                  api_key, model, fetch_budget):
    """Triage a single batch via one Claude API call.

    Returns (list[dict], int) — verdicts and number of page fetches used.
    """
    import anthropic

    fallback = [
        {'url': item.url, 'verdict': 'maybe', 'reasoning': 'triage fallback'}
        for item in items
    ]

    system_prompt = _build_system_prompt(industry_description, reader_personas)
    user_message = _build_user_message(items, source_types)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        messages = [{'role': 'user', 'content': user_message}]
        fetches_used = 0

        # Conversation loop to handle tool use
        for _ in range(fetch_budget + 2):  # +2: initial call + final no-tools call
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=messages,
                tools=[_FETCH_PAGE_TOOL],
            )

            # Check if Claude wants to use a tool
            if response.stop_reason == 'tool_use':
                tool_results = []
                for block in response.content:
                    if block.type == 'tool_use' and block.name == 'fetch_page':
                        url = block.input.get('url', '')
                        if fetches_used < fetch_budget:
                            content = _fetch_page_for_triage(url)
                            fetches_used += 1
                        else:
                            content = '[Fetch budget exceeded — classify based on available information]'
                        tool_results.append({
                            'type': 'tool_result',
                            'tool_use_id': block.id,
                            'content': content,
                        })

                # Add assistant response and tool results to continue conversation
                messages.append({'role': 'assistant', 'content': response.content})
                messages.append({'role': 'user', 'content': tool_results})
                continue

            # Final response — extract text and parse
            return _parse_response(response, items), fetches_used

        # Loop exhausted (Claude kept requesting tools). Make one final call
        # without tools to force a JSON response.
        logger.info("Triage tool-use loop exhausted, forcing final JSON-only response")
        messages.append({'role': 'assistant', 'content': response.content})
        # Provide tool results for any pending tool_use blocks to avoid API error
        tool_results = []
        for block in response.content:
            if block.type == 'tool_use':
                tool_results.append({
                    'type': 'tool_result',
                    'tool_use_id': block.id,
                    'content': '[No more fetches available — output your JSON verdicts now]',
                })
        if tool_results:
            messages.append({'role': 'user', 'content': tool_results})
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
        )
        return _parse_response(response, items), fetches_used

    except anthropic.RateLimitError:
        logger.warning("Triage API rate limited, retrying once after 10s")
        time.sleep(10)
        try:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{'role': 'user', 'content': user_message}],
                tools=[_FETCH_PAGE_TOOL],
            )
            return _parse_response(response, items), 0
        except Exception as e:
            logger.error(f"Triage retry failed: {e}")
            return fallback, 0

    except Exception as e:
        logger.error(f"Triage batch failed: {e}", exc_info=True)
        return fallback, 0


def _build_system_prompt(industry_description, reader_personas):
    """Build the system prompt for the triage agent."""
    return (
        "You are a news triage agent for a trade publication. Your job is to classify "
        "discovered URLs as relevant news articles or not, and to extract publish dates.\n\n"
        f"## Publication Industry\n{industry_description or 'Not specified'}\n\n"
        f"## Reader Personas\n{reader_personas or 'Not specified'}\n\n"
        "## Classification Rules\n"
        "For each item, assign one verdict:\n"
        "- **relevant_news**: Clearly a news article, report, or analysis relevant to the publication's industry and readers, published within the last 90 days.\n"
        "- **maybe**: Could be relevant but unclear from title/snippet alone. Includes opinion pieces, tangentially related topics, or ambiguous titles.\n"
        "- **not_news**: Navigation pages, event listings, subscription pages, author bios, category archives, "
        "tag pages, search results, login pages, content clearly unrelated to the industry, "
        "OR articles/press releases older than 90 days.\n\n"
        "## Date Extraction\n"
        "For each item, extract the publish date if you can determine it from:\n"
        "- The URL path (e.g. /2025/02/19/ or /2025-02-19-)\n"
        "- The title or snippet (e.g. 'January 15, 2025')\n"
        "- Page content if you fetched it\n"
        "Return the date as an ISO string (YYYY-MM-DD) or null if unknown.\n"
        "Items with dates older than 90 days should be classified as not_news.\n\n"
        "## Instructions\n"
        "- Analyze the title, snippet, URL pattern, and source type for each item.\n"
        "- If a title/snippet is missing or too vague to classify, use the fetch_page tool to retrieve the page content (use sparingly).\n"
        "- Respond with a JSON array of objects, one per item, in the same order as the input.\n"
        "- Each object must have: index (int), verdict (string), reasoning (brief string), published_date (string YYYY-MM-DD or null).\n"
        "- Output ONLY the JSON array, no markdown fencing or extra text."
    )


def _build_user_message(items, source_types):
    """Build the user message containing items to classify."""
    payload = []
    for i, (item, st) in enumerate(zip(items, source_types)):
        entry = {
            'index': i,
            'url': item.url,
            'title': item.title or '',
            'snippet': (item.snippet or '')[:300],  # truncate long snippets
            'source_type': st,
        }
        payload.append(entry)
    return json.dumps(payload, indent=None)


def _parse_response(response, items):
    """Parse Claude's response into verdict dicts."""
    fallback = [
        {'url': item.url, 'verdict': 'maybe', 'reasoning': 'parse fallback'}
        for item in items
    ]

    # Concatenate all text blocks from the response
    text_parts = []
    for block in response.content:
        if hasattr(block, 'text') and block.text:
            text_parts.append(block.text)

    text = '\n'.join(text_parts).strip()

    if not text:
        logger.warning(
            "Triage response contained no text. "
            f"stop_reason={response.stop_reason}, "
            f"content_types={[b.type for b in response.content]}"
        )
        return fallback

    # Strip markdown fencing if present
    if '```' in text:
        # Extract content between first ``` and last ```
        fenced = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()

    if not text:
        logger.warning("Triage response was empty after stripping markdown fences")
        return fallback

    try:
        verdicts_raw = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract a JSON array from within surrounding text
        array_match = re.search(r'\[.*\]', text, re.DOTALL)
        if array_match:
            try:
                verdicts_raw = json.loads(array_match.group(0))
            except json.JSONDecodeError as e2:
                logger.warning(f"Triage response: could not extract JSON array: {e2}\nRaw text (first 500 chars): {text[:500]}")
                return fallback
        else:
            logger.warning(f"Triage response contains no JSON array.\nRaw text (first 500 chars): {text[:500]}")
            return fallback

    if not isinstance(verdicts_raw, list):
        logger.warning("Triage response is not a JSON array")
        return fallback

    # Build index-keyed lookup
    verdict_map = {}
    for v in verdicts_raw:
        if isinstance(v, dict) and 'index' in v:
            verdict_map[v['index']] = v

    # Map back to items, filling in missing entries
    results = []
    valid_verdicts = {'relevant_news', 'maybe', 'not_news'}
    for i, item in enumerate(items):
        v = verdict_map.get(i, {})
        verdict = v.get('verdict', 'maybe')
        if verdict not in valid_verdicts:
            verdict = 'maybe'

        # Parse published_date from triage response
        published_date = None
        raw_date = v.get('published_date')
        if raw_date and isinstance(raw_date, str):
            try:
                from dateutil.parser import parse as parse_date
                published_date = parse_date(raw_date)
            except (ValueError, TypeError):
                pass

        results.append({
            'url': item.url,
            'verdict': verdict,
            'reasoning': v.get('reasoning', 'no reasoning provided'),
            'published_date': published_date,
        })

    return results


def _fetch_page_for_triage(url: str) -> str:
    """Fetch a page via Firecrawl for triage classification.

    Returns markdown content (truncated) or an error message.
    """
    from app.research.enrichment import _firecrawl_scrape, _firecrawl_rate_limit

    api_key = current_app.config.get('FIRECRAWL_API_KEY')
    if not api_key:
        return '[Page fetch unavailable — no Firecrawl API key]'

    try:
        data = _firecrawl_scrape(url, api_key)
        if not data:
            return f'[Failed to fetch {url}]'

        markdown = data.get('markdown', '')
        # Truncate to keep token usage reasonable
        if len(markdown) > 3000:
            markdown = markdown[:3000] + '\n\n[... content truncated ...]'
        return markdown or f'[No content extracted from {url}]'

    except Exception as e:
        logger.warning(f"Triage page fetch failed for {url}: {e}")
        return f'[Fetch error: {e}]'
