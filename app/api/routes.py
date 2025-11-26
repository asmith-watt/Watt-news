from flask import request, jsonify, current_app
from functools import wraps
from datetime import datetime
from app import db
from app.models import NewsContent, NewsSource, Publication
from app.api import bp


def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key or api_key != current_app.config.get('N8N_API_KEY'):
            return jsonify({'error': 'Invalid or missing API key'}), 401
        return f(*args, **kwargs)
    return decorated_function


@bp.route('/news', methods=['POST'])
@require_api_key
def create_news():
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    required_fields = ['title', 'publication_id']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing required field: {field}'}), 400

    publication = Publication.query.get(data['publication_id'])
    if not publication:
        return jsonify({'error': 'Publication not found'}), 404

    try:
        content = NewsContent(
            publication_id=data['publication_id'],
            title=data['title'],
            content=data.get('content'),
            summary=data.get('summary'),
            author=data.get('author'),
            source_url=data.get('source_url'),
            source_name=data.get('source_name'),
            image_url=data.get('image_url'),
            published_date=datetime.fromisoformat(data['published_date']) if data.get('published_date') else None,
            status=data.get('status', 'staged'),
            extra_data=data.get('extra_data')
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

            publication = Publication.query.get(item['publication_id'])
            if not publication:
                errors.append({'index': idx, 'error': 'Publication not found'})
                continue

            content = NewsContent(
                publication_id=item['publication_id'],
                title=item['title'],
                content=item.get('content'),
                summary=item.get('summary'),
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
    publication = Publication.query.get(publication_id)
    if not publication:
        return jsonify({'error': 'Publication not found'}), 404

    sources = NewsSource.query.filter_by(
        publication_id=publication_id,
        is_active=True
    ).all()

    return jsonify({
        'publication_id': publication_id,
        'publication_name': publication.name,
        'industry': publication.industry_description,
        'sources': [
            {
                'id': source.id,
                'name': source.name,
                'type': source.source_type,
                'url': source.url,
                'config': source.config
            }
            for source in sources
        ]
    })


@bp.route('/publications', methods=['GET'])
@require_api_key
def get_publications():
    publications = Publication.query.filter_by(is_active=True).all()

    return jsonify({
        'publications': [
            {
                'id': pub.id,
                'name': pub.name,
                'slug': pub.slug,
                'industry': pub.industry_description
            }
            for pub in publications
        ]
    })