# API Documentation

This document provides comprehensive documentation for the Watt Automation API endpoints.

## Base URL

```
http://your-domain.com/api
```

For local development:
```
http://localhost:5000/api
```

## Authentication

All API endpoints require authentication via an API key passed in the request headers.

### Header Format

```
X-API-Key: your_api_key_here
```

### Getting an API Key

API keys are configured in your application's environment variables. Contact your system administrator or check the `.env` file for the `N8N_API_KEY` value.

---

## Endpoints

### 1. Create News Content

Create a single news content item.

**Endpoint:** `POST /api/news`

**Authentication:** Required

**Request Body:**

```json
{
  "title": "Article Title",
  "publication_id": 1,
  "content": "Full article content...",
  "summary": "Brief summary of the article",
  "author": "Author Name",
  "source_url": "https://source.com/article",
  "source_name": "Source Name",
  "image_url": "https://example.com/image.jpg",
  "published_date": "2024-11-26T10:30:00",
  "status": "staged",
  "extra_data": {
    "custom_field": "value"
  }
}
```

**Required Fields:**
- `title` (string) - The article title
- `publication_id` (integer) - ID of the publication this content belongs to

**Optional Fields:**
- `content` (string) - Full article content
- `summary` (string) - Article summary
- `author` (string) - Author name
- `source_url` (string) - Original source URL
- `source_name` (string) - Name of the source
- `image_url` (string) - URL to article image
- `published_date` (string) - ISO 8601 format datetime
- `status` (string) - Content status (default: "staged")
- `extra_data` (object) - Additional JSON data

**Success Response (201):**

```json
{
  "success": true,
  "id": 42,
  "message": "News content created successfully"
}
```

**Error Responses:**

- `400 Bad Request` - Missing required fields or invalid data
  ```json
  {
    "error": "Missing required field: title"
  }
  ```

- `401 Unauthorized` - Invalid or missing API key
  ```json
  {
    "error": "Invalid or missing API key"
  }
  ```

- `404 Not Found` - Publication not found
  ```json
  {
    "error": "Publication not found"
  }
  ```

- `500 Internal Server Error` - Server error
  ```json
  {
    "error": "Failed to create news content: [error details]"
  }
  ```

**Example cURL:**

```bash

```

---

### 2. Create News Content (Bulk)

Create multiple news content items in a single request.

**Endpoint:** `POST /api/news/bulk`

**Authentication:** Required

**Request Body:**

Array of news content objects:

```json
[
  {
    "title": "Article 1",
    "publication_id": 1,
    "content": "Content 1...",
    "summary": "Summary 1"
  },
  {
    "title": "Article 2",
    "publication_id": 1,
    "content": "Content 2...",
    "summary": "Summary 2"
  }
]
```

**Success Response (201):**

```json
{
  "success": true,
  "created": 2,
  "errors": []
}
```

**Partial Success Response (201):**

If some items fail but others succeed:

```json
{
  "success": true,
  "created": 1,
  "errors": [
    {
      "index": 1,
      "error": "Missing required field: title"
    }
  ]
}
```

**Error Responses:**

- `400 Bad Request` - Invalid request format
  ```json
  {
    "error": "Expected a list of news items"
  }
  ```

- `401 Unauthorized` - Invalid or missing API key
- `500 Internal Server Error` - Database commit failed

**Example cURL:**

```bash
curl -X POST http://localhost:5000/api/news/bulk \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key_here" \
  -d '[
    {
      "title": "Article 1",
      "publication_id": 1,
      "content": "Content 1"
    },
    {
      "title": "Article 2",
      "publication_id": 1,
      "content": "Content 2"
    }
  ]'
```

---

### 3. Get News Sources

Retrieve all active news sources for a specific publication.

**Endpoint:** `GET /api/sources/<publication_id>`

**Authentication:** Required

**Path Parameters:**
- `publication_id` (integer) - ID of the publication

**Success Response (200):**

```json
{
  "publication_id": 1,
  "publication_name": "Tech News",
  "industry": "Technology and Innovation",
  "sources": [
    {
      "id": 1,
      "name": "TechCrunch Feed",
      "type": "rss",
      "url": "https://techcrunch.com/feed/",
      "config": {
        "refresh_interval": 300,
        "max_items": 50
      }
    },
    {
      "id": 2,
      "name": "Hacker News",
      "type": "api",
      "url": "https://news.ycombinator.com/api",
      "config": {
        "api_version": "v0"
      }
    }
  ]
}
```

**Error Responses:**

- `401 Unauthorized` - Invalid or missing API key
- `404 Not Found` - Publication not found
  ```json
  {
    "error": "Publication not found"
  }
  ```

**Example cURL:**

```bash
curl -X GET http://localhost:5000/api/sources/1 \
  -H "X-API-Key: your_api_key_here"
```

---

### 4. Get Publications

Retrieve all active publications.

**Endpoint:** `GET /api/publications`

**Authentication:** Required

**Success Response (200):**

```json
{
  "publications": [
    {
      "id": 1,
      "name": "Tech News",
      "slug": "tech-news",
      "industry": "Technology and Innovation"
    },
    {
      "id": 2,
      "name": "Business Daily",
      "slug": "business-daily",
      "industry": "Business and Finance"
    }
  ]
}
```

**Error Responses:**

- `401 Unauthorized` - Invalid or missing API key

**Example cURL:**

```bash
curl -X GET http://localhost:5000/api/publications \
  -H "X-API-Key: your_api_key_here"
```

---

## Status Codes

| Code | Description |
|------|-------------|
| 200 | Success (GET requests) |
| 201 | Created (POST requests) |
| 400 | Bad Request - Invalid input data |
| 401 | Unauthorized - Invalid or missing API key |
| 404 | Not Found - Resource not found |
| 500 | Internal Server Error - Server-side error |

---

## Data Models

### NewsContent

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| title | string | Yes | Article title |
| publication_id | integer | Yes | Publication ID |
| content | string | No | Full article content |
| summary | string | No | Article summary |
| author | string | No | Author name |
| source_url | string | No | Original source URL |
| source_name | string | No | Source name |
| image_url | string | No | Image URL |
| published_date | datetime | No | Publication date (ISO 8601) |
| status | string | No | Status (default: "staged") |
| extra_data | object | No | Additional JSON data |

### NewsSource

| Field | Type | Description |
|-------|------|-------------|
| id | integer | Source ID |
| name | string | Source name |
| type | string | Source type (e.g., "rss", "api") |
| url | string | Source URL |
| config | object | Source configuration (JSON) |

### Publication

| Field | Type | Description |
|-------|------|-------------|
| id | integer | Publication ID |
| name | string | Publication name |
| slug | string | URL-friendly identifier |
| industry | string | Industry description |

---

## Common Use Cases

### 1. Import RSS Feed Articles

```bash
# Step 1: Get the publication ID
curl -X GET http://localhost:5000/api/publications \
  -H "X-API-Key: your_api_key"

# Step 2: Create news content
curl -X POST http://localhost:5000/api/news \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{
    "title": "Article from RSS",
    "publication_id": 1,
    "content": "Full content...",
    "source_url": "https://feed.com/article",
    "source_name": "RSS Feed"
  }'
```

### 2. Bulk Import from External API

```bash
curl -X POST http://localhost:5000/api/news/bulk \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '[
    {"title": "Article 1", "publication_id": 1, "content": "..."},
    {"title": "Article 2", "publication_id": 1, "content": "..."},
    {"title": "Article 3", "publication_id": 1, "content": "..."}
  ]'
```

### 3. Get Sources for Automation

```bash
# Get all sources for a publication
curl -X GET http://localhost:5000/api/sources/1 \
  -H "X-API-Key: your_api_key"
```

---

## Error Handling

All error responses follow this format:

```json
{
  "error": "Description of the error"
}
```

For bulk operations, errors are returned per item:

```json
{
  "success": true,
  "created": 2,
  "errors": [
    {
      "index": 1,
      "error": "Missing required field: title"
    }
  ]
}
```

---

## Rate Limiting

Currently, there are no rate limits implemented. This may change in future versions.

---

## Versioning

The API is currently at version 1. Future versions will be indicated in the URL path (e.g., `/api/v2/`).

---

## Support

For issues or questions:
- Check application logs for detailed error messages
- Ensure your API key is valid and properly configured
- Verify publication IDs exist before creating content
- Use ISO 8601 format for dates: `YYYY-MM-DDTHH:MM:SS`