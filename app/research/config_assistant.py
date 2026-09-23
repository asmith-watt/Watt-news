"""Conversational helper for authoring a NewsSource's `config` JSON.

The Data source type needs a hand-written JSON config whose fiddliest fields
(`url_pattern` with its {MMYY} placeholder, `pdf_json_path` dot-notation) can't
realistically be guessed without looking at the live page or API endpoint.

This module drives a Claude conversation that can do exactly that: the editor
describes what they want to scrape, Claude inspects the source with a small set
of read-only fetch tools, and proposes a finished config the admin UI can drop
straight into the form.  A separate dry-run helper runs the real discovery step
against a drafted config so the editor can prove it works before saving.
"""
import ipaddress
import json
import logging
import socket
from types import SimpleNamespace
from urllib.parse import urlparse

import requests
from flask import current_app

logger = logging.getLogger(__name__)

# Conversation guardrails
MAX_TURNS = 8              # model round-trips per user message
MAX_FETCHES = 6            # tool calls per user message
MAX_HISTORY_MESSAGES = 40  # client-supplied history cap
MAX_MESSAGE_CHARS = 8000


# --- Schema reference handed to the model -----------------------------------

_DATA_SCHEMA_DOC = """
## Data source config schema

Required in every Data config:
- discovery_mode (string): "url_pattern", "landing_page", or "api"
- document_type (string): "pdf" (the only supported value)
- report_name (string): human-readable report name, e.g. "WASDE"
- publisher (string): organisation name, e.g. "USDA"
- cadence (string): "monthly", "weekly", or "quarterly"
- analysis_prompt (string): the full prompt Claude uses to pull story angles out
  of each report. Write it for a senior analyst in the publication's industry:
  say what kind of angles matter to these readers and what to ignore.

Mode-specific:
- url_pattern mode → url_pattern (string): the PDF URL with a {MMYY} placeholder
  for the two-digit month and year, e.g.
  "https://www.usda.gov/oce/commodity/wasde/wasde{MMYY}.pdf".
  {MMYY} is the ONLY supported placeholder. If the real URLs don't follow a
  month/year pattern, use landing_page or api mode instead.
- landing_page mode → landing_page_url (string): page to scrape for PDF links.
  Falls back to the source's own URL if omitted. Only links ending in ".pdf"
  are picked up.
- api mode → api_url (string): JSON endpoint, and pdf_json_path (string):
  dot-notation path to the PDF URLs, e.g. "rows[].outlookReport".
  Optional date_json_path (string): path to release dates, e.g.
  "rows[].releaseDate" — needed for the lookback window to filter old reports.

Path syntax for pdf_json_path / date_json_path:
  "field"            → data["field"]
  "field[]"          → iterate data["field"]
  "field[].child"    → child of each item
  "a.b[].c.d"        → nested traversal
Only dicts and lists are traversed; there is no wildcard or index selector.

Optional in any mode:
- lookback_months (int, default 2): how many past months to check
- max_angles (int, default 5): max story angles Claude returns per report
- claude_model (string, optional; omit to use the app default model)

Never include previous_report_data — the scraper writes that itself after each run.

## How discovery actually behaves (be honest with the editor about this)
- url_pattern: sends a HEAD request per month in the lookback window and only
  accepts a 200 whose Content-Type mentions pdf, or whose URL ends in .pdf.
- landing_page: scrapes the page via Firecrawl and takes every link ending
  in ".pdf". No date filtering, so a page listing years of archives will pick
  up all of them.
- api: GETs the endpoint with no auth headers and no query params beyond what's
  in api_url. Reports older than lookback_months are skipped when
  date_json_path is set.

## Other source types
Only the Data source type reads this config. For RSS Feed, News Site, Keyword
Search, YouTube Keywords, House Content, and Competitor sources the config
field is ignored entirely — the URL and Keywords fields drive those. If the
editor is configuring one of those, say so plainly and don't invent a config.
"""


def _build_system_prompt(publication, source_ctx):
    """System prompt: schema reference plus what we know about this source."""
    return f"""You are helping an editor write the JSON `config` for a news source in \
WATT Automation's admin UI. You are talking to a publishing professional, not a \
developer — they know their industry and their sources, not JSON.

## The publication
Name: {publication.name}
Industry: {publication.industry_description or 'Not specified'}
Readers: {publication.reader_personas or 'Not specified'}

## The source being configured (as currently typed into the form)
Name: {source_ctx.get('name') or '(blank)'}
Source type: {source_ctx.get('source_type') or '(blank)'}
URL: {source_ctx.get('url') or '(blank)'}
Keywords: {source_ctx.get('keywords') or '(blank)'}
Current config JSON:
{source_ctx.get('current_config') or '(empty)'}

{_DATA_SCHEMA_DOC}

## How to work
- Investigate before you ask. You have fetch_page, fetch_json, and check_url —
  use them on the source URL to work out the real discovery mode, URL pattern,
  or JSON path rather than asking the editor to look it up for you.
- Prefer url_pattern when the PDFs follow a predictable monthly URL, api when a
  JSON endpoint lists them, and landing_page otherwise.
- Verify what you propose. Confirm a url_pattern with check_url on a real recent
  month, and confirm a pdf_json_path against the actual JSON you fetched.
- Ask at most one or two questions at a time, in plain language, and only when
  the answer genuinely changes the config (which report, what angle the readers
  care about). Never ask them for something you can look up.
- Write the analysis_prompt yourself, tailored to this publication's industry
  and readers. Don't hand back a placeholder.
- When you have a complete config, call propose_config. Include every required
  field — the editor applies it verbatim. Alongside it, say in one short
  paragraph what you found and what you're unsure about.
- If you can't make a source work (no PDFs, JS-rendered listing, endpoint needs
  auth), say so directly and suggest the closest alternative.
- Be concise. No preamble, no restating the schema back at them."""


# --- Tools ------------------------------------------------------------------

_TOOLS = [
    {
        "name": "fetch_page",
        "description": (
            "Fetch a web page as markdown plus the list of links on it. Use this to "
            "inspect a landing page for PDF links, or to read documentation about a "
            "report's URL scheme."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The page URL to fetch"}},
            "required": ["url"],
        },
    },
    {
        "name": "fetch_json",
        "description": (
            "GET a JSON API endpoint and return its structure (arrays truncated to a "
            "couple of entries). Use this to work out pdf_json_path and date_json_path "
            "for api discovery mode."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The JSON endpoint URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "check_url",
        "description": (
            "Check whether a specific URL exists and what content type it returns. Use "
            "this to verify a candidate url_pattern by testing a real recent month, "
            "before proposing it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The exact URL to test"}},
            "required": ["url"],
        },
    },
    {
        "name": "propose_config",
        "description": (
            "Propose a finished config JSON for the editor to apply to the form. Call "
            "this only when the config is complete and you have verified the discovery "
            "settings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "description": "The complete config object, exactly as it should be saved",
                },
            },
            "required": ["config"],
        },
    },
]


def _is_safe_url(url):
    """Reject anything that isn't public http(s) — these URLs come from an LLM."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "Malformed URL"

    if parsed.scheme not in ('http', 'https'):
        return False, "Only http and https URLs can be fetched"
    if not parsed.hostname:
        return False, "URL has no hostname"

    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        return False, f"Could not resolve {parsed.hostname}: {e}"

    for info in addrinfo:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False, f"{parsed.hostname} resolves to a non-public address"

    return True, None


def _shrink_json(obj, max_items=2, max_depth=6, depth=0):
    """Truncate arrays and deep nesting so a large API response fits in context."""
    if depth >= max_depth:
        return '...'
    if isinstance(obj, dict):
        return {k: _shrink_json(v, max_items, max_depth, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        shrunk = [_shrink_json(v, max_items, max_depth, depth + 1) for v in obj[:max_items]]
        if len(obj) > max_items:
            shrunk.append(f'... ({len(obj)} items total)')
        return shrunk
    if isinstance(obj, str) and len(obj) > 300:
        return obj[:300] + '...'
    return obj


def _tool_fetch_page(url):
    from app.research.scrapers import DataScraper

    api_key = current_app.config.get('FIRECRAWL_API_KEY')
    if api_key:
        data = DataScraper()._firecrawl_request(
            'scrape', {'url': url, 'formats': ['markdown', 'links']}, timeout=45
        )
        if data is not None:
            payload = data.get('data', {})
            markdown = (payload.get('markdown') or '')[:5000]
            links = [l for l in payload.get('links', []) if isinstance(l, str)]
            pdf_links = [l for l in links if l.lower().endswith('.pdf')]
            other_links = [l for l in links if not l.lower().endswith('.pdf')][:40]
            return (
                f"PDF links found ({len(pdf_links)}):\n" + '\n'.join(pdf_links[:40]) +
                f"\n\nOther links (first {len(other_links)} of {len(links)}):\n" +
                '\n'.join(other_links) +
                f"\n\nPage content:\n{markdown}"
            )
        logger.warning(f"config_assistant: Firecrawl scrape failed for {url}, falling back to requests")

    # Fallback: raw fetch, pull out hrefs so PDF discovery still works
    import re
    resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
    resp.raise_for_status()
    html = resp.text
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    pdf_links = [h for h in hrefs if h.lower().endswith('.pdf')]
    text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()[:4000]
    return (
        "[Fetched without Firecrawl — links may be relative]\n"
        f"PDF links found ({len(pdf_links)}):\n" + '\n'.join(pdf_links[:40]) +
        f"\n\nOther links (first 40 of {len(hrefs)}):\n" + '\n'.join(hrefs[:40]) +
        f"\n\nPage text:\n{text}"
    )


def _tool_fetch_json(url):
    resp = requests.get(url, timeout=30, headers={'Accept': 'application/json'})
    resp.raise_for_status()
    data = resp.json()
    shrunk = json.dumps(_shrink_json(data), indent=2)
    if len(shrunk) > 8000:
        shrunk = shrunk[:8000] + '\n... (truncated)'
    return f"HTTP {resp.status_code} from {resp.url}\n\n{shrunk}"


def _tool_check_url(url):
    resp = requests.head(url, timeout=15, allow_redirects=True)
    if resp.status_code >= 400:
        # Some servers reject HEAD outright — retry with a ranged GET
        resp = requests.get(url, timeout=20, allow_redirects=True, stream=True,
                            headers={'Range': 'bytes=0-1023'})
        resp.close()
    return (
        f"status={resp.status_code} "
        f"content_type={resp.headers.get('Content-Type', 'unknown')} "
        f"final_url={resp.url}"
    )


_TOOL_FUNCS = {
    'fetch_page': _tool_fetch_page,
    'fetch_json': _tool_fetch_json,
    'check_url': _tool_check_url,
}


def _run_fetch_tool(name, tool_input):
    url = (tool_input or {}).get('url', '')
    if not url:
        return "[No URL provided]"

    safe, reason = _is_safe_url(url)
    if not safe:
        return f"[Refused to fetch {url}: {reason}]"

    try:
        return _TOOL_FUNCS[name](url)
    except requests.RequestException as e:
        return f"[Request failed for {url}: {e}]"
    except ValueError as e:  # JSON decode
        return f"[{url} did not return valid JSON: {e}]"
    except Exception as e:
        logger.warning(f"config_assistant: tool {name} failed for {url}: {e}", exc_info=True)
        return f"[Tool error: {e}]"


# --- Conversation -----------------------------------------------------------

def sanitize_history(raw_messages):
    """Coerce client-supplied history into a valid Anthropic messages array.

    The conversation lives in the browser, so everything here is untrusted: we
    keep only well-formed user/assistant text turns, drop leading assistant
    turns, and collapse consecutive same-role messages by dropping duplicates.
    """
    cleaned = []
    for msg in (raw_messages or [])[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(msg, dict):
            continue
        role = msg.get('role')
        content = msg.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str):
            continue
        content = content.strip()[:MAX_MESSAGE_CHARS]
        if not content:
            continue
        if not cleaned and role != 'user':
            continue
        if cleaned and cleaned[-1]['role'] == role:
            cleaned[-1]['content'] = f"{cleaned[-1]['content']}\n\n{content}"
            continue
        cleaned.append({'role': role, 'content': content})

    if cleaned and cleaned[-1]['role'] != 'user':
        cleaned.pop()
    return cleaned


def run_turn(messages, source_ctx, publication):
    """Run one assistant turn (including any tool use) and return the result.

    Returns {'reply': str, 'config': dict|None, 'actions': list[str]}.
    `actions` is a short human-readable trace of what the assistant looked at,
    so the editor can see it checked the real source.
    """
    import anthropic

    api_key = current_app.config.get('ANTHROPIC_API_KEY')
    if not api_key:
        return {
            'reply': 'The config assistant needs ANTHROPIC_API_KEY to be configured.',
            'config': None,
            'actions': [],
        }

    model = current_app.config.get('CONFIG_ASSISTANT_MODEL', 'claude-opus-5')
    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = _build_system_prompt(publication, source_ctx)

    convo = list(messages)
    actions = []
    fetches_used = 0

    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=model,
            max_tokens=8000,
            system=system_prompt,
            messages=convo,
            tools=_TOOLS,
        )

        text = '\n\n'.join(
            b.text for b in response.content if b.type == 'text' and b.text
        ).strip()
        tool_uses = [b for b in response.content if b.type == 'tool_use']

        if not tool_uses:
            return {'reply': text, 'config': None, 'actions': actions}

        proposed = next((b for b in tool_uses if b.name == 'propose_config'), None)
        if proposed:
            config = (proposed.input or {}).get('config')
            if isinstance(config, str):
                try:
                    config = json.loads(config)
                except json.JSONDecodeError:
                    config = None
            if not isinstance(config, dict):
                config = None
            return {'reply': text, 'config': config, 'actions': actions}

        tool_results = []
        for block in tool_uses:
            if fetches_used >= MAX_FETCHES:
                content = '[Fetch budget for this turn is used up — work with what you have.]'
            else:
                fetches_used += 1
                content = _run_fetch_tool(block.name, block.input)
                actions.append(f"{block.name}: {(block.input or {}).get('url', '')}")
            tool_results.append({
                'type': 'tool_result',
                'tool_use_id': block.id,
                'content': content,
            })

        convo.append({'role': 'assistant', 'content': response.content})
        convo.append({'role': 'user', 'content': tool_results})

    return {
        'reply': "I ran out of investigation steps for this message. Send another message "
                 "and I'll carry on from what I found.",
        'config': None,
        'actions': actions,
    }


# --- Dry run ----------------------------------------------------------------

class _LogCapture(logging.Handler):
    """Collects research-pipeline log records so the dry run can explain itself."""

    MAX_LINES = 200

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []

    def emit(self, record):
        if len(self.lines) >= self.MAX_LINES:
            return
        try:
            self.lines.append(f"{record.levelname}: {record.getMessage()}")
        except Exception:
            pass


def dry_run_discovery(config, source_url, source_name='(unsaved source)'):
    """Run only the discovery step of DataScraper against a drafted config.

    No PDFs are downloaded, no LLM analysis runs, and nothing is written to the
    database — this just answers "would this config find any reports?", with the
    scraper's own debug logging as the explanation.
    """
    from app.research.scrapers import DataScraper

    if not isinstance(config, dict) or not config:
        return {'ok': False, 'error': 'Config is empty or not a JSON object',
                'mode': None, 'urls': [], 'log': []}

    scraper = DataScraper()
    capture = _LogCapture()
    research_logger = logging.getLogger('app.research')
    previous_level = research_logger.level
    research_logger.addHandler(capture)
    research_logger.setLevel(logging.DEBUG)

    try:
        if not scraper._validate_config(config):
            return {
                'ok': False,
                'error': 'Config failed validation — see the log below for the missing fields.',
                'mode': config.get('discovery_mode'),
                'urls': [],
                'log': capture.lines,
            }

        shim = SimpleNamespace(id=None, name=source_name, url=source_url, config=config)
        urls = scraper._discover_report_urls(shim)
        return {
            'ok': bool(urls),
            'error': None if urls else 'Config is valid but discovery found no reports.',
            'mode': config.get('discovery_mode'),
            'urls': urls,
            'log': capture.lines,
        }
    except Exception as e:
        logger.error(f"config_assistant: dry run failed: {e}", exc_info=True)
        return {
            'ok': False,
            'error': f'Discovery raised an error: {e}',
            'mode': config.get('discovery_mode'),
            'urls': [],
            'log': capture.lines,
        }
    finally:
        research_logger.removeHandler(capture)
        research_logger.setLevel(previous_level)