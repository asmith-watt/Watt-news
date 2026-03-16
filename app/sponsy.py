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
            return slots[0].get('copy') or None
    except Exception:
        pass
    return None
