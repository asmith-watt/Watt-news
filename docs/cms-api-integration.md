# Parameter1 CMS API Integration

Documentation for integrating with the Parameter1 Mindful CMS Entity Command API.

## Overview

The CMS uses an Entity Command API where entities (like articles, images) are created via POST requests with a specific payload structure.

**Base Endpoint**: `https://api.mindfulcms.com/commands`

**Required Headers**:
```
Authorization: Bearer <API_KEY>
Content-Type: application/json
X-Namespace: watt/default
```

## Metadata Discovery

Query available entities and their properties:

```bash
curl -s -X GET "https://api.mindfulcms.com/commands" -H "Authorization: Bearer YOUR_API_KEY" -H "X-Namespace: watt/default" -H "Content-Type: application/json" | jq '.entities[] | select(._id == "POST")'
```

## Creating Articles

Articles are created as `POST` entities with form `POST` and type `News`.

### Payload Structure

```json
{
  "command": {
    "input": [
      {
        "form": "POST",
        "props": {
          "title": "Article Title",
          "type": "News",
          "body": "Article body content...",
          "deck": "Short summary/subheadline",
          "excerpt": "Teaser text (what we call 'teaser' internally)"
        }
      }
    ]
  },
  "view": "main"
}
```

### Available Props for Articles

**From POST entity (base level):**
| Prop | Required | Description |
|------|----------|-------------|
| `body` | No | Main article content |
| `deck` | No | Subheadline/summary |
| `excerpt` | No | Teaser text (maps to our `teaser` field) |
| `notes` | No | Internal notes |
| `slug` | No | URL slug |
| `publishedDateTime` | No | Publication date |
| `archived` | No | Archive flag |
| `paused` | No | Pause flag |
| `labels` | No | Labels/tags |

**From POST form (variant level):**
| Prop | Required | Description |
|------|----------|-------------|
| `title` | **Yes** | Article headline |
| `type` | **Yes** | Must be `"News"` for news articles |
| `byline` | No | Author byline |
| `creditOrSource` | No | Source attribution |

### Field Mapping (Our System → CMS)

| Our Field | CMS Field |
|-----------|-----------|
| `title` | `title` |
| `content` / `body` | `body` |
| `deck` | `deck` |
| `teaser` | `excerpt` |

## Images

Images are separate entities that must be created/referenced via the `featuredImage` edge.

### IMAGE Entity Required Props

| Prop | Required | Description |
|------|----------|-------------|
| `hostname` | **Yes** | CDN hostname (e.g., `img.wattglobal.com`) |
| `key` | **Yes** | Path to image (e.g., `files/base/path/to/image.jpg`) |
| `extension` | **Yes** | File extension (`jpg`, `png`, etc.) |
| `originalName` | **Yes** | Original filename |

### IMAGE Entity Optional Props

| Prop | Description |
|------|-------------|
| `alt` | Alt text for accessibility |
| `caption` | Image caption |
| `credit` | Photo credit |
| `title` | Image title |

### Attaching Images to Articles

Images are attached via the `featuredImage` edge. The exact workflow needs confirmation from Parameter1:

**Option A: Reference existing IMAGE by ID**
```json
{
  "form": "POST",
  "props": { ... },
  "edges": {
    "featuredImage": { "_id": "existing-image-entity-id" }
  }
}
```

**Option B: Inline creation (needs confirmation)**
```json
{
  "form": "POST",
  "props": { ... },
  "edges": {
    "featuredImage": {
      "props": {
        "hostname": "img.wattglobal.com",
        "key": "files/path/to/image.jpg",
        "extension": "jpg",
        "originalName": "image.jpg",
        "alt": "Description of image"
      }
    }
  }
}
```

**TODO**: Confirm with Parameter1:
1. Can images be created inline when creating an article?
2. If not, what's the endpoint/process to create an IMAGE entity first?
3. How should we handle images from external URLs (our n8n-generated images)?

## Entity Structure Reference

The API uses these relationship types:

| Type | Description |
|------|-------------|
| `props` | Scalar values (strings, numbers, booleans) |
| `edges` | Single reference to another entity |
| `connections` | Multiple references to other entities |
| `embedOnes` | Single embedded entity fragment |
| `embedManies` | Multiple embedded entity fragments |

### POST Entity Relationships

**Edges (base level):**
- `featuredImage` → references `IMAGE` entity

**Connections (base level):**
- `image` → multiple images
- `linkedPost` → related posts
- `vocabTerm` → taxonomy terms

**Connections (POST form level):**
- `attachment` → file attachments
- `author` → author references

## Current Implementation

Located in `app/main/routes.py`:

- `push_to_cms()` - Legacy push (content without versions)
- `push_version_to_cms()` - Push specific version to CMS

Both functions:
1. Build props with `title`, `type: 'News'`, `body`
2. Add `deck` if available
3. Add `excerpt` (from our `teaser`) if available
4. POST to CMS endpoint

## Example cURL Request

```bash
curl -X POST "https://api.mindfulcms.com/commands/post/create" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-Namespace: watt/default" \
  -d '{
    "command": {
      "input": [
        {
          "form": "POST",
          "props": {
            "title": "Test Article",
            "type": "News",
            "body": "Article content here...",
            "deck": "A brief summary",
            "excerpt": "Teaser text for listings"
          }
        }
      ]
    },
    "view": "main"
  }'
```

## References

- [Entity Command API Docs](https://docs.parameter1.com/mindful-apis/entity-command-api)
- [Create Article POST](https://docs.parameter1.com/mindful-apis/entity-command-api#create-an-article-post)
- [Metadata Endpoint](https://docs.parameter1.com/mindful-apis/entity-command-api#metadata)
