# Candidate Article Research Pipeline

The research pipeline automatically discovers, scores, deduplicates, and queues candidate articles from multiple source types. This document covers the full system end-to-end.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [How Research Gets Triggered](#how-research-gets-triggered)
- [Source Types & Scrapers](#source-types--scrapers)
  - [RSS Feed](#rss-feed)
  - [News Site](#news-site)
  - [Keyword Search](#keyword-search)
  - [Competitor](#competitor)
  - [Data](#data)
- [Scoring](#scoring)
- [Deduplication](#deduplication)
- [Candidate Lifecycle](#candidate-lifecycle)
- [API Access](#api-access)
- [Configuration Reference](#configuration-reference)

---

## Architecture Overview

```
TRIGGER (Admin UI / Candidates page / API / Celery Beat schedule)
  │
  ▼
research_publication_sources(publication_id)        ← Celery task
  │
  ├─ For each ACTIVE NewsSource:
  │     │
  │     ▼
  │   SCRAPER_REGISTRY[source_type].scrape(source)  ← Returns List[DiscoveredItem]
  │     │
  │     ▼
  │   For each DiscoveredItem:
  │     ├─ normalize_url() → url_hash()             ← Dedup module
  │     ├─ is_duplicate_candidate()?  → skip
  │     ├─ is_already_content()?      → skip
  │     ├─ score_candidate()                         ← Scoring module
  │     └─ INSERT CandidateArticle (status='new')
  │
  ▼
UPDATE publication.last_research_run
```

### Key files

| File | Role |
|------|------|
| `app/research/scrapers.py` | Source-specific discovery logic |
| `app/research/scoring.py` | Relevance scoring |
| `app/research/dedup.py` | URL normalization & duplicate detection |
| `app/tasks.py` | Celery task orchestration |
| `app/models.py` | `NewsSource`, `CandidateArticle`, `SourceType` |

---

## How Research Gets Triggered

There are four ways to start a research run:

### 1. Admin UI
POST `/admin/publications/<id>/trigger-research` — button on the publications list page. Admin-only.

### 2. Candidates Page
POST `/publication/<id>/trigger-research` — button on the candidates page. Requires publication access.

### 3. API
POST `/api/research/trigger/<publication_id>` — requires the publication's `access_api_key` as a Bearer token.

### 4. Automatic Schedule (Celery Beat)
The `check_research_schedules` task runs every 15 minutes. It finds publications whose `last_research_run` is either NULL or more than 6 hours old and dispatches `research_publication_sources` for each one that has active sources.

All four paths call the same Celery task: `research_publication_sources.delay(publication_id)`.

---

## Source Types & Scrapers

Each `NewsSource` has a `source_type` that determines which scraper processes it. The scraper registry maps type names to classes:

```python
SCRAPER_REGISTRY = {
    'RSS Feed':        RSSFeedScraper,
    'News Site':       NewsSiteScraper,
    'Keyword Search':  KeywordSearchScraper,
    'Data':            DataScraper,
}
```

`CompetitorScraper` exists as a subclass of `NewsSiteScraper` but is not currently registered (competitors use `NewsSiteScraper` logic with an `is_competitor` metadata flag).

Every scraper returns a `List[DiscoveredItem]`:

```python
@dataclass
class DiscoveredItem:
    url: str
    title: Optional[str]
    snippet: Optional[str]
    author: Optional[str]
    published_date: Optional[datetime]
    metadata: dict
```

---

### RSS Feed

**Class:** `RSSFeedScraper`
**Requires:** `source.url` pointing to an RSS or Atom feed
**External dependency:** `feedparser` library (no API key needed)

**How it works:**
1. Parses the feed URL with `feedparser.parse()`
2. Iterates over `feed.entries`
3. Extracts link, title, summary/description, author, published date, and tags
4. Returns one `DiscoveredItem` per feed entry

**Best for:** Sites that publish a well-maintained RSS feed (blogs, news outlets, trade publications).

**Source weight:** 1.0 (highest — RSS feeds are curated by publishers)

---

### News Site

**Class:** `NewsSiteScraper`
**Requires:** `source.url` pointing to a news site, `FIRECRAWL_API_KEY` env var
**External dependency:** Firecrawl API

**How it works:**
1. Calls Firecrawl `/map` endpoint with the site URL (limit: 50 links)
2. If `source.keywords` is set, passes it as a search filter
3. If fewer than 5 results come back, falls back to the `/scrape` endpoint to extract links from the page HTML
4. Returns one `DiscoveredItem` per discovered URL

**Best for:** News sites without RSS feeds, or when you want broader coverage of a site's content.

**Source weight:** 0.9

---

### Keyword Search

**Class:** `KeywordSearchScraper`
**Requires:** `source.keywords` (comma-separated), `SERPAPI_API_KEY` env var
**External dependency:** SerpAPI (Google News engine)

**How it works:**
1. Sends `source.keywords` to SerpAPI's Google News engine
2. Parses `news_results` from the response
3. Extracts link, title, snippet, source name, date, and thumbnail
4. Returns one `DiscoveredItem` per news result

**Best for:** Broad topic monitoring across the entire web (e.g., "corn prices", "renewable energy policy").

**Source weight:** 0.8

---

### Competitor

**Class:** `CompetitorScraper` (extends `NewsSiteScraper`)
**Requires:** Same as News Site
**External dependency:** Firecrawl API

**How it works:** Identical to News Site, but adds `is_competitor: true` to each item's metadata. This flag can be used downstream for filtering or labeling.

**Source weight:** 0.7

---

### Data

**Class:** `DataScraper`
**Requires:** `source.config` JSON (see below), `ANTHROPIC_API_KEY` env var
**External dependencies:** `pdfplumber`, `anthropic`, `python-dateutil`, optionally Firecrawl (for landing page mode)

This is the most complex scraper. It downloads government/institutional PDF reports, extracts the text, sends it to Claude for analysis, and returns multiple story angles as individual candidates.

#### Pipeline

```
1. Validate source.config JSON
2. Discover report URLs (three modes):
   ├─ url_pattern mode: generate URLs with {MMYY} placeholders, HEAD-check each
   ├─ landing_page mode: use Firecrawl to find PDF links on a page
   └─ api mode: fetch a JSON API, extract PDF URLs via configurable JSON path
3. For each discovered PDF:
   a. Download PDF bytes
   b. Extract text with pdfplumber (tables + body text)
   c. Send text + analysis_prompt to Claude API
   d. Parse Claude's JSON response into story angles
   e. Create one DiscoveredItem per angle (unique URL via ?angle=N query param)
   f. Write report_summary + key_figures back to source.config for next run
```

#### Discovery modes

| Mode | Use when | How it works |
|------|----------|--------------|
| `url_pattern` | PDF URLs follow a date-based pattern | Generates URLs with `{MMYY}` placeholders, HEAD-checks each to verify existence |
| `landing_page` | PDFs are linked directly from a web page | Uses Firecrawl to scrape the page and find `.pdf` links |
| `api` | A public JSON API lists publications with PDF URLs | Fetches the API endpoint, extracts PDF URLs and dates using configurable dot-notation JSON paths (e.g. `rows[].outlookReport`). Filters by `lookback_months`. Ideal for government data portals like USDA ERS. |

#### One PDF produces many candidates

Each story angle gets a unique URL by appending `?angle=1`, `?angle=2`, etc. to the PDF URL. The dedup module preserves non-tracking query params, so each angle gets its own hash and won't collide.

#### Month-over-month context

After analyzing a report, Claude's `report_summary` and `key_figures` are saved to `source.config['previous_report_data']`. On the next run, this data is included in the prompt so Claude can highlight changes and trends.

#### Claude response format

The scraper instructs Claude to return JSON in this structure:

```json
{
  "report_summary": "Brief 2-3 sentence overview of the report",
  "key_figures": {
    "corn_ending_stocks": "1.540B bu",
    "soybean_exports": "1.825B bu"
  },
  "story_angles": [
    {
      "headline": "USDA Slashes Corn Ending Stocks Below Market Expectations",
      "summary": "2-3 paragraph article-ready summary...",
      "commodity": "corn",
      "data_points": [
        {"metric": "Ending Stocks", "value": "1.540B bu", "previous": "1.738B bu", "change": "-11.4%"}
      ],
      "significance": "high",
      "angle_type": "supply_shift"
    }
  ]
}
```

Supported `angle_type` values: `supply_shift`, `demand_change`, `price_impact`, `trade_flow`, `policy_change`, `weather_impact`, `other`.

#### Source config JSON schema

Each Data source must have its `config` JSON configured in the admin UI. See the [Configuration Reference](#data-source-config-json) section below.

**Source weight:** 0.85

---

## Scoring

Every discovered item is scored before being saved. The scoring module (`app/research/scoring.py`) computes four values:

### Keyword Score (0-100)

Measures how well the candidate matches the publication's topic area.

- Extracts meaningful words (3+ letters, excluding stop words) from `publication.industry_description` and `source.keywords`
- Counts how many of those terms appear in the candidate's title + snippet
- Formula: `(matches / unique_terms) * 100`
- If no keywords are configured, defaults to 50 (neutral)

### Recency Score (0-100)

Rewards freshly published content.

| Age | Score |
|-----|-------|
| < 1 day | 100 |
| 1-2 days | 85 |
| 2-4 days | 70 |
| 4-8 days | 50 |
| 8-15 days | 30 |
| 15-29 days | 15 |
| > 29 days | 5 |
| No date available | 40 |

### Source Weight (0-1)

Fixed weight based on the source type, reflecting the typical quality/trust level.

| Source Type | Weight |
|-------------|--------|
| RSS Feed | 1.0 |
| News Site | 0.9 |
| Data | 0.85 |
| Keyword Search | 0.8 |
| Competitor | 0.7 |
| House Content | 0.3 |

### Relevance Score (0-100, composite)

The final ranking score, used for sorting candidates in the UI.

```
relevance_score = (keyword_score * 0.50) + (recency_score * 0.30) + (source_weight * 100 * 0.20)
```

In the UI, scores are color-coded:
- **Green** (70+): Strong match
- **Yellow** (40-70): Moderate match
- **Red** (< 40): Weak match

Hovering over the score badge shows the individual breakdown (Keyword | Recency | Source).

---

## Deduplication

The dedup module (`app/research/dedup.py`) prevents the same article from appearing twice.

### URL Normalization

Before hashing, URLs go through normalization:
1. Lowercase scheme and host
2. Strip trailing slashes from path
3. Remove fragment (`#...`)
4. Remove tracking query parameters: `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `fbclid`, `gclid`, `ref`, `mc_cid`, `mc_eid`
5. Preserve all other query parameters (including `?angle=N` used by the Data scraper)

### Two-tier duplicate check

For each discovered item, the task runs two checks:

1. **`is_duplicate_candidate(hash, publication_id)`** — Has this exact URL (by hash) already been discovered for this publication? Uses the `uq_candidate_pub_url` unique constraint on `(publication_id, url_hash)`.

2. **`is_already_content(url, publication_id)`** — Has this URL already been turned into a published `NewsContent` article? Checks `NewsContent.source_url` with a case-insensitive LIKE match.

If either check passes, the item is skipped.

---

## Candidate Lifecycle

```
                    ┌──────────┐
                    │   NEW    │  ← Created by research pipeline
                    └────┬─────┘
                         │
              ┌──────────┼──────────┐
              ▼                     ▼
        ┌──────────┐         ┌──────────┐
        │ SELECTED │ ◄─────► │ REJECTED │
        └────┬─────┘         └──────────┘
             │                     ▲
             ▼                     │
        ┌──────────┐               │
        │PROCESSED │  (can also reset back to NEW)
        └──────────┘
```

- **New**: Just discovered, awaiting review
- **Selected**: Approved for content generation
- **Rejected**: Dismissed (with optional rejection reason)
- **Processed**: Content has been generated from this candidate (links to `NewsContent`)

### Review modes

Publications have a `require_candidate_review` flag:

- **Off (default):** The API returns candidates with status `new` directly to n8n for content generation. Fully automated.
- **On:** The API returns only `selected` candidates. A human must review and select candidates in the UI before they're picked up.

---

## API Access

External systems (like n8n workflows) interact with candidates through the API:

### GET `/api/candidates/<publication_id>`
Returns scored candidates as JSON.

Query parameters:
- `status` — Filter by status (default: `selected` if review required, `new` otherwise)
- `min_score` — Minimum relevance score
- `limit` — Max results (default 20, max 100)
- `source_id` — Filter by specific news source

### POST `/api/candidates/<candidate_id>/status`
Update a single candidate's status.

Payload: `{"status": "selected|rejected|processed", "news_content_id": 123}`

### POST `/api/candidates/bulk-status`
Batch update multiple candidates.

Payload: `{"updates": [{"id": 1, "status": "processed", "news_content_id": 123}, ...]}`

### POST `/api/research/trigger/<publication_id>`
Trigger a research run via API. Requires Bearer token matching the publication's `access_api_key`.

---

## Configuration Reference

### Environment Variables

| Variable | Required By | Description |
|----------|-------------|-------------|
| `FIRECRAWL_API_KEY` | News Site, Competitor, Data (landing_page mode) | Firecrawl API key |
| `SERPAPI_API_KEY` | Keyword Search | SerpAPI key for Google News |
| `ANTHROPIC_API_KEY` | Data | Anthropic API key for Claude |

### Data Source Config JSON

Each Data source requires a `config` JSON object configured via the admin UI (Admin > Publication > Sources > Edit Source > Configuration JSON). Click "Show/Hide Documentation" on the form for an inline reference.

#### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `discovery_mode` | string | `"url_pattern"`, `"landing_page"`, or `"api"` |
| `document_type` | string | `"pdf"` (only supported type currently) |
| `report_name` | string | Human-readable report name (e.g. `"WASDE"`) |
| `publisher` | string | Organization name (e.g. `"USDA"`) |
| `cadence` | string | `"monthly"`, `"weekly"`, or `"quarterly"` |
| `analysis_prompt` | string | Full Claude system prompt for analyzing the document |

#### Mode-specific fields

| Field | Required When | Description |
|-------|---------------|-------------|
| `url_pattern` | `discovery_mode` = `"url_pattern"` | URL template with `{MMYY}` placeholder (e.g. `https://example.gov/report{MMYY}.pdf`) |
| `landing_page_url` | `discovery_mode` = `"landing_page"` | URL to scrape for PDF links. Falls back to `source.url` if omitted. |
| `api_url` | `discovery_mode` = `"api"` | JSON API endpoint that returns publication listings |
| `pdf_json_path` | `discovery_mode` = `"api"` | Dot-notation path to PDF URLs in the API response (e.g. `rows[].outlookReport`). Supports `[]` for array iteration. |
| `date_json_path` | `discovery_mode` = `"api"` (optional) | Dot-notation path to release dates (e.g. `rows[].releaseDate`). Used with `lookback_months` to filter old reports. |

#### Optional fields

| Field | Default | Description |
|-------|---------|-------------|
| `lookback_months` | `2` | How many past months to check for reports |
| `max_angles` | `5` | Maximum story angles Claude should extract |
| `claude_model` | `claude-sonnet-4-20250514` | Anthropic model ID |
| `previous_report_data` | `null` | Auto-populated after each run with `report_summary` and `key_figures` for month-over-month context |

#### Example: USDA WASDE Monthly Report (url_pattern mode)

```json
{
  "discovery_mode": "url_pattern",
  "url_pattern": "https://www.usda.gov/oce/commodity/wasde/wasde{MMYY}.pdf",
  "document_type": "pdf",
  "report_name": "WASDE",
  "publisher": "USDA",
  "cadence": "monthly",
  "lookback_months": 2,
  "analysis_prompt": "You are a senior agricultural commodities analyst. Analyze this USDA WASDE report and identify the most newsworthy story angles for agricultural trade publication readers. Focus on significant changes in supply/demand balances, ending stocks revisions, trade flow adjustments, and any data that diverges from market expectations.",
  "max_angles": 5
}
```

#### Example: USDA ERS Feed Outlook (api mode)

For reports where URLs don't follow a date pattern (e.g. `pub-details?pubid=113811`), use `api` mode to query the publisher's JSON API directly.

```json
{
  "discovery_mode": "api",
  "api_url": "https://www.ers.usda.gov/api/publications/v1.0?series=FDS&pageSize=5",
  "pdf_json_path": "rows[].outlookReport",
  "date_json_path": "rows[].releaseDate",
  "document_type": "pdf",
  "report_name": "Feed Outlook",
  "publisher": "USDA ERS",
  "cadence": "monthly",
  "lookback_months": 2,
  "analysis_prompt": "You are a senior agricultural commodities analyst specializing in feed grains. Analyze this USDA ERS Feed Outlook report and identify the most newsworthy story angles for agricultural trade publication readers. Focus on feed grain supply/demand changes, price forecasts, livestock feeding trends, and any data that diverges from market expectations.",
  "max_angles": 5
}
```

The `pdf_json_path` and `date_json_path` use dot-notation with `[]` to traverse arrays. For example, `rows[].outlookReport` means "iterate over the `rows` array and extract the `outlookReport` field from each item." Nested paths like `data.publications[].files[].url` are also supported.
