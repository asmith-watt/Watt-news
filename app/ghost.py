import jwt
import requests
from datetime import datetime, timezone, timedelta


def _generate_ghost_jwt(api_key):
    """Generate a short-lived JWT for Ghost Admin API authentication.

    Args:
        api_key: Ghost Admin API key in the format 'key_id:secret'

    Returns:
        Encoded JWT token string
    """
    key_id, secret = api_key.split(':')
    secret_bytes = bytes.fromhex(secret)

    now = datetime.now(timezone.utc)
    payload = {
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(minutes=5)).timestamp()),
        'aud': '/admin/',
    }
    headers = {
        'kid': key_id,
    }

    return jwt.encode(payload, secret_bytes, algorithm='HS256', headers=headers)


def create_ghost_post(ghost_url, api_key, title, html, excerpt=None, tags=None, status='draft'):
    """Create a post in Ghost CMS.

    Args:
        ghost_url: Base Ghost URL (e.g. https://my-site.ghost.io)
        api_key: Ghost Admin API key
        title: Post title
        html: Post HTML content
        excerpt: Optional post excerpt
        tags: Optional list of tag names
        status: Post status (default: 'draft')

    Returns:
        Response JSON dict from Ghost API

    Raises:
        requests.exceptions.HTTPError: On non-2xx response
    """
    ghost_url = ghost_url.rstrip('/')
    token = _generate_ghost_jwt(api_key)

    post_data = {
        'title': title,
        'html': html,
        'status': status,
    }

    if excerpt:
        post_data['custom_excerpt'] = excerpt

    if tags:
        post_data['tags'] = [{'name': t.strip()} for t in tags if t.strip()]

    url = f'{ghost_url}/ghost/api/admin/posts/?source=html'
    headers = {
        'Authorization': f'Ghost {token}',
        'Content-Type': 'application/json',
    }

    response = requests.post(url, json={'posts': [post_data]}, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def create_ghost_newsletter_post(ghost_url, api_key, title, html, newsletter_slug=None, status='draft'):
    """Create a newsletter post in Ghost CMS.

    Args:
        ghost_url: Base Ghost URL
        api_key: Ghost Admin API key
        title: Post title
        html: Fully rendered newsletter HTML
        newsletter_slug: Optional Ghost newsletter slug for email delivery
        status: Post status (default: 'draft')

    Returns:
        Response JSON dict from Ghost API

    Raises:
        requests.exceptions.HTTPError: On non-2xx response
    """
    ghost_url = ghost_url.rstrip('/')
    token = _generate_ghost_jwt(api_key)

    post_data = {
        'title': title,
        'html': html,
        'status': status,
    }

    if newsletter_slug:
        post_data['newsletter'] = {'slug': newsletter_slug}
        post_data['email_segment'] = 'all'

    url = f'{ghost_url}/ghost/api/admin/posts/?source=html'
    headers = {
        'Authorization': f'Ghost {token}',
        'Content-Type': 'application/json',
    }

    response = requests.post(url, json={'posts': [post_data]}, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()
