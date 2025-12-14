from flask import request, jsonify, current_app, g
from functools import wraps
from datetime import datetime
from app import db
from app.models import NewsContent, NewsSource, Publication
from app.api import bp


def require_api_key(f):
    """
    Validates API key against:
    1. Global N8N_API_KEY (system-wide access)
    2. Publication-specific access_api_key (restricted access)

    If using a publication-specific key, stores the publication in g.authenticated_publication
    for validation in the route handler.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')

        if not api_key:
            return jsonify({'error': 'Missing API key'}), 401

        # Check global API key first
        if api_key == current_app.config.get('N8N_API_KEY'):
            g.authenticated_publication = None  # Global access
            return f(*args, **kwargs)

        # Check publication-specific API keys
        publication = Publication.query.filter_by(
            access_api_key=api_key,
            is_active=True
        ).first()

        if publication:
            g.authenticated_publication = publication  # Restricted access
            return f(*args, **kwargs)

        return jsonify({'error': 'Invalid API key'}), 401

    return decorated_function


def validate_publication_access(publication_id):
    """
    Validates that the API key has access to the specified publication.
    Returns (is_valid, error_response)
    """
    # Global API key has access to all publications
    if g.get('authenticated_publication') is None:
        return True, None

    # Publication-specific key can only access its own publication
    authenticated_pub_id = g.authenticated_publication.id

    # Debug logging
    current_app.logger.info(f"Publication Access Check: API key pub_id={authenticated_pub_id}, requested pub_id={publication_id}, match={authenticated_pub_id == publication_id}")

    if authenticated_pub_id != publication_id:
        return False, (
            jsonify({
                'error': 'API key does not have access to this publication',
                'authenticated_publication_id': authenticated_pub_id,
                'requested_publication_id': publication_id
            }),
            403
        )

    return True, None


@bp.route('/news', methods=['POST'])
@require_api_key
def create_news():
    """
    Create a single news content item from JSON format.

    Expected format:
    {
      "publication_id": 1,
      "title": "...",
      "summary": "...",
      "body": "...",
      "keywords": ["keyword1", "keyword2"],
      "references": [
        {
          "title": "...",
          "source_name": "...",
          "published_date": "YYYY-MM-DD",
          "url": "..."
        }
      ]
    }
    """
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Handle array format - extract first item
    if isinstance(data, list):
        if len(data) == 0:
            return jsonify({'error': 'Empty list provided'}), 400
        data = data[0]

    # Check for publication_id
    if 'publication_id' not in data:
        return jsonify({'error': 'Missing required field: publication_id'}), 400

    # Convert publication_id to integer if it's a string
    try:
        publication_id = int(data['publication_id'])
    except (ValueError, TypeError):
        return jsonify({'error': 'publication_id must be a valid integer'}), 400

    # Validate API key has access to this publication
    is_valid, error_response = validate_publication_access(publication_id)
    if not is_valid:
        return error_response

    publication = Publication.query.get(publication_id)
    if not publication:
        return jsonify({'error': 'Publication not found'}), 404

    # Check for title
    if not data.get('title'):
        return jsonify({'error': 'Missing required field: title'}), 400

    try:
        # Concatenate keywords as comma-separated string
        keywords_list = data.get('keywords', [])
        keywords_str = ', '.join(keywords_list) if keywords_list else None

        # Concatenate references into source_url and source_name
        references = data.get('references', [])
        source_entries = []
        source_names = []
        published_date = None

        for ref in references:
            url = ref.get('url')
            ref_date = ref.get('published_date')
            if url:
                if ref_date:
                    source_entries.append(f"{url} ({ref_date})")
                else:
                    source_entries.append(url)
            if ref.get('source_name'):
                source_names.append(ref['source_name'])
            # Use the first published_date found for the record's published_date
            if not published_date and ref_date:
                try:
                    published_date = datetime.fromisoformat(ref_date)
                except ValueError:
                    pass

        source_url_str = ' | '.join(source_entries) if source_entries else None
        source_name_str = ', '.join(set(source_names)) if source_names else None

        content = NewsContent(
            publication_id=publication_id,
            title=data['title'],
            content=data.get('body'),
            summary=data.get('summary'),
            notes=data.get('notes'),
            source_url=source_url_str,
            source_name=source_name_str,
            keywords=keywords_str,
            published_date=published_date,
            status=data.get('status', 'staged')
        )

        db.session.add(content)
        db.session.commit()

        return jsonify({
            'success': True,
            'id': content.id,
            'message': 'News content created successfully'
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to create news content: {str(e)}'}), 500


@bp.route('/news/bulk', methods=['POST'])
@require_api_key
def create_news_bulk():
    data = request.get_json()

    if not data or not isinstance(data, list):
        return jsonify({'error': 'Expected a list of news items'}), 400

    created = []
    errors = []

    for idx, item in enumerate(data):
        try:
            required_fields = ['title', 'publication_id']
            for field in required_fields:
                if field not in item:
                    errors.append({'index': idx, 'error': f'Missing required field: {field}'})
                    continue

            # Convert publication_id to integer if it's a string
            try:
                publication_id = int(item['publication_id'])
            except (ValueError, TypeError):
                errors.append({'index': idx, 'error': 'publication_id must be a valid integer'})
                continue

            # Validate API key has access to this publication
            is_valid, _ = validate_publication_access(publication_id)
            if not is_valid:
                errors.append({'index': idx, 'error': 'API key does not have access to this publication'})
                continue

            publication = Publication.query.get(publication_id)
            if not publication:
                errors.append({'index': idx, 'error': 'Publication not found'})
                continue

            content = NewsContent(
                publication_id=publication_id,
                title=item['title'],
                content=item.get('content'),
                summary=item.get('summary'),
                notes=item.get('notes'),
                author=item.get('author'),
                source_url=item.get('source_url'),
                source_name=item.get('source_name'),
                image_url=item.get('image_url'),
                published_date=datetime.fromisoformat(item['published_date']) if item.get('published_date') else None,
                status=item.get('status', 'staged'),
                extra_data=item.get('extra_data')
            )

            db.session.add(content)
            created.append(idx)

        except Exception as e:
            errors.append({'index': idx, 'error': str(e)})

    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'created': len(created),
            'errors': errors
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to commit: {str(e)}'}), 500


@bp.route('/sources/<int:publication_id>', methods=['GET'])
@require_api_key
def get_news_sources(publication_id):
    # Validate API key has access to this publication
    is_valid, error_response = validate_publication_access(publication_id)
    if not is_valid:
        return error_response

    publication = Publication.query.get(publication_id)
    if not publication:
        return jsonify({'error': 'Publication not found'}), 404

    sources = NewsSource.query.filter_by(
        publication_id=publication_id,
        is_active=True
    ).all()

    return jsonify({
        'sources': [
            {
                'id': source.id,
                'name': source.name,
                'type': source.source_type,
                'url': source.url,
                'keywords': source.keywords,
                'config': source.config
            }
            for source in sources
        ]
    })


@bp.route('/publications', methods=['GET'])
@require_api_key
def get_publications():
    # If using publication-specific API key, only return that publication
    if g.get('authenticated_publication'):
        publications = [g.authenticated_publication]
    else:
        # Global API key gets all active publications
        publications = Publication.query.filter_by(is_active=True).all()

    return jsonify({
        'publications': [
            {
                'id': pub.id,
                'name': pub.name,
                'slug': pub.slug,
                'industry': pub.industry_description,
                'reader_personas': pub.reader_personas,
                'reader_pain_points': pub.reader_pain_points
            }
            for pub in publications
        ]
    })


@bp.route('/publications/<int:publication_id>', methods=['GET'])
@require_api_key
def get_publication(publication_id):
    # Validate API key has access to this publication
    is_valid, error_response = validate_publication_access(publication_id)
    if not is_valid:
        return error_response

    publication = Publication.query.get(publication_id)
    if not publication:
        return jsonify({'error': 'Publication not found'}), 404

    return jsonify({
        'id': publication.id,
        'name': publication.name,
        'slug': publication.slug,
        'industry_description': publication.industry_description,
        'reader_personas': publication.reader_personas,
        'reader_pain_points': publication.reader_pain_points,
        'cms_url': publication.cms_url,
        'is_active': publication.is_active,
        'created_at': publication.created_at.isoformat() if publication.created_at else None
    })