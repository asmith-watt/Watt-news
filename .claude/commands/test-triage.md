Run the triage test CLI command for the given publication.

This tests the LLM triage system by sampling existing candidates from the DB (one per source type), sending them through Claude for classification, and displaying the verdicts. No external scrapers or Firecrawl calls are made — only Claude classification.

## Usage

The user may pass a publication ID as an argument, e.g. `/test-triage 1`. If no ID is given, default to publication ID 1.

## Steps

1. Run: `flask test-triage <publication_id>` (display results only)
2. Show the user the output — the verdict table and summary counts
3. Ask if they want to `--save` the results or adjust `--count`

## Available flags

- `--count N` — number of items per source type (default 3)
- `--save` — save triaged items as test candidates (prefixed `[TRIAGE TEST]`)
- `--cleanup` — delete previously saved test candidates
- `--no-save` — display only (default)

## Cleanup

To remove test candidates: `flask test-triage <publication_id> --cleanup`