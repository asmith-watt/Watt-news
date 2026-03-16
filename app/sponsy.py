import requests


def fetch_placements(api_key, publication_id):
    """Fetch available placements from Sponsy. Returns list of (id, name) tuples."""
    try:
        resp = requests.get(
            f'https://api.getsponsy.com/v1/publications/{publication_id}/placements',
            headers={'x-api-key': api_key},
            timeout=10,
        )
        resp.raise_for_status()
        placements = resp.json().get('data', [])
        return [(p['id'], p['name']) for p in placements]
    except Exception:
        return []


def fetch_ad_blocks(api_key, publication_id):
    """Fetch available ad blocks from Sponsy. Returns list of (id, name) tuples."""
    try:
        resp = requests.get(
            'https://api.getsponsy.com/ad-blocks',
            params={'publicationId': publication_id},
            headers={'x-api-key': api_key},
            timeout=10,
        )
        resp.raise_for_status()
        blocks = resp.json()
        if isinstance(blocks, dict):
            blocks = blocks.get('data', [])
        return [(b['id'], b.get('name', b['id'])) for b in blocks]
    except Exception:
        return []


def fetch_ad_block_html(api_key, ad_block_id, placement_id, date):
    """Fetch rendered ad HTML from Sponsy for a given ad block and date.

    Uses the POST /ad-blocks/html endpoint.
    Returns the HTML string or None on any error.
    """
    try:
        body = {
            'adBlockId': ad_block_id,
            'date': date,
        }
        if placement_id:
            body['placementId'] = placement_id
        resp = requests.post(
            'https://api.getsponsy.com/ad-blocks/html',
            json=body,
            headers={'x-api-key': api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        html = data.get('html') if isinstance(data, dict) else None
        if html and html.strip() != '<span />':
            return html
    except Exception:
        pass
    return None


def fetch_slot_html(api_key, publication_id, placement_id, date):
    """Fetch ad HTML from Sponsy for a given placement and date.

    Returns the HTML string from the first slot's `copy` field, or None on any error.
    """
    try:
        resp = requests.get(
            f'https://api.getsponsy.com/v1/publications/{publication_id}/slots',
            params={'placementId': placement_id, 'date': date, 'limit': 1},
            headers={'x-api-key': api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        slots = data.get('data', [])
        if slots:
            copy = slots[0].get('copy')
            if isinstance(copy, dict):
                return copy.get('html') or copy.get('markdown') or None
            return copy or None
    except Exception:
        pass
    return None
