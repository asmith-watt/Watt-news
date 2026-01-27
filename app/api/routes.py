from flask import request, jsonify, current_app, g
from functools import wraps
from datetime import datetime
from app import db
from app.models import NewsContent, NewsSource, Publication, WorkflowRun, ContentVersion
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

    Supported formats:

    1. Legacy (single version, no ai_provider):
    {
      "publication_id": 1,
      "title": "...",
      "summary": "...",
      "body": "...",
      "keywords": ["keyword1", "keyword2"],
      "references": [...]
    }

    2. Flat array of versions (recommended for n8n):
    [
      {
        "publication_id": 1,
        "ai_provider": "anthropic",
        "ai_model": "claude-3-opus",
        "quality_score": 87.5,
        "title": "...",
        "deck": "...",
        "teaser": "...",
        "body": "...",
        "summary": "...",
        "notes": "...",
        "keywords": ["k1", "k2"],
        "references": [...]
      },
      {
        "publication_id": 1,
        "ai_provider": "openai",
        "ai_model": "gpt-4",
        "quality_score": 82.0,
        "title": "...",
        "deck": "...",
        "body": "...",
        ...
      }
    ]

    3. Nested versions format:
    {
      "publication_id": 1,
      "title": "...",
      "versions": [...]
    }
    """
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Unwrap if nested in "payload" key (common n8n pattern)
    if isinstance(data, dict) and 'payload' in data:
        data = data['payload']

    # Detect format: flat array of versions vs single object
    versions_data = []
    if isinstance(data, list):
        if len(data) == 0:
            return jsonify({'error': 'Empty list provided'}), 400

        # Check if this is a flat array of versions (each item has ai_provider)
        if data[0].get('ai_provider'):
            # Flat array format: extract header from first item, all items are versions
            versions_data = data
            data = data[0]  # Use first item for header fields
        else:
            # Legacy array format: just take first item
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

        source_url_str = data.get('source_url') or (' | '.join(source_entries) if source_entries else None)
        source_name_str = data.get('source_name') or (', '.join(set(source_names)) if source_names else None)

        # If no versions from flat array, check for nested versions format
        if not versions_data:
            versions_data = data.get('versions', [])

        # Create parent NewsContent (shared metadata)
        content = NewsContent(
            publication_id=publication_id,
            title=data['title'],
            source_url=source_url_str,
            source_name=source_name_str,
            image_url=data.get('image_url'),
            image_thumbnail=data.get('image_thumbnail'),
            keywords=keywords_str,
            published_date=published_date,
            status=data.get('status', 'staged')
        )

        # If no versions provided, store content in legacy fields (backward compatibility)
        if not versions_data:
            content.deck = data.get('deck')
            content.teaser = data.get('teaser')
            content.content = data.get('body')
            content.summary = data.get('summary')
            content.notes = data.get('notes')

        db.session.add(content)
        db.session.flush()  # Get content.id before creating versions

        # Create ContentVersion records
        created_versions = []
        best_version = None
        best_score = -1

        for v_data in versions_data:
            if not v_data.get('ai_provider'):
                continue  # Skip versions without provider

            version = ContentVersion(
                content_id=content.id,
                ai_provider=v_data['ai_provider'],
                ai_model=v_data.get('ai_model'),
                quality_score=v_data.get('quality_score'),
                deck=v_data.get('deck'),
                teaser=v_data.get('teaser'),
                content=v_data.get('body') or v_data.get('content'),
                summary=v_data.get('summary'),
                notes=v_data.get('notes')
            )
            db.session.add(version)
            db.session.flush()  # Get version.id
            created_versions.append(version)

            # Track best version by quality score
            score = v_data.get('quality_score') or 0
            if score > best_score:
                best_score = score
                best_version = version

        # Auto-select the best version (highest quality score)
        if best_version:
            content.selected_version_id = best_version.id

        db.session.commit()

        response = {
            'success': True,
            'id': content.id,
            'message': 'News content created successfully'
        }

        if created_versions:
            response['version_ids'] = [v.id for v in created_versions]
            response['selected_version_id'] = content.selected_version_id

        return jsonify(response), 201

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
                deck=item.get('deck'),
                teaser=item.get('teaser'),
                content=item.get('content'),
                summary=item.get('summary'),
                notes=item.get('notes'),
                author=item.get('author'),
                source_url=item.get('source_url'),
                source_name=item.get('source_name'),
                image_url=item.get('image_url'),
                image_thumbnail=item.get('image_thumbnail'),
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
                'publication_id': publication.id,
                'industry_description': publication.industry_description,
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
                'publication_domain': pub.publication_domain,
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
        'publication_domain': publication.publication_domain,
        'industry_description': publication.industry_description,
        'reader_personas': publication.reader_personas,
        'reader_pain_points': publication.reader_pain_points,
        'cms_url': publication.cms_url,
        'is_active': publication.is_active,
        'created_at': publication.created_at.isoformat() if publication.created_at else None
    })


@bp.route('/workflow/<workflow_id>/status', methods=['GET'])
def get_workflow_status(workflow_id):
    """Get the status of a workflow run. No auth required as workflow_id is unique."""
    workflow = WorkflowRun.query.get(workflow_id)
    if not workflow:
        return jsonify({'error': 'Workflow not found'}), 404

    return jsonify({
        'id': workflow.id,
        'status': workflow.status,
        'message': workflow.message,
        'publication_id': workflow.publication_id,
        'created_at': workflow.created_at.isoformat() if workflow.created_at else None,
        'completed_at': workflow.completed_at.isoformat() if workflow.completed_at else None
    })


@bp.route('/workflow/<workflow_id>/complete', methods=['POST'])
@require_api_key
def complete_workflow(workflow_id):
    """Called by n8n when a workflow completes."""
    workflow = WorkflowRun.query.get(workflow_id)
    if not workflow:
        return jsonify({'error': 'Workflow not found'}), 404

    # Use force=True to parse JSON even without Content-Type header (n8n sometimes omits it)
    data = request.get_json(force=True, silent=True) or {}

    workflow.status = data.get('status', 'completed')
    workflow.message = data.get('message')
    workflow.completed_at = datetime.utcnow()

    db.session.commit()

    return jsonify({
        'success': True,
        'id': workflow.id,
        'status': workflow.status
    })


@bp.route('/workflow/<workflow_id>/image-complete', methods=['POST'])
@require_api_key
def complete_image_workflow(workflow_id):
    """Called by n8n when image generation completes. Updates the content with image URLs."""
    workflow = WorkflowRun.query.get(workflow_id)
    if not workflow:
        return jsonify({'error': 'Workflow not found'}), 404

    # Use force=True to parse JSON even without Content-Type header
    data = request.get_json(force=True, silent=True) or {}

    # Get content_id from the workflow message or from the request
    content_id = data.get('content_id')
    if not content_id and workflow.message:
        # Parse content_id from message (format: "content_id:123")
        if workflow.message.startswith('content_id:'):
            try:
                content_id = int(workflow.message.split(':')[1])
            except (ValueError, IndexError):
                pass

    if not content_id:
        workflow.status = 'failed'
        workflow.message = 'No content_id provided'
        workflow.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'error': 'No content_id provided'}), 400

    content = NewsContent.query.get(content_id)
    if not content:
        workflow.status = 'failed'
        workflow.message = f'Content {content_id} not found'
        workflow.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'error': 'Content not found'}), 404

    # Update content with image URLs
    image_thumbnail = data.get('image_thumbnail') or data.get('thumbnail_url')
    image_url = data.get('image_url') or data.get('download_url')

    if image_thumbnail:
        content.image_thumbnail = image_thumbnail
    if image_url:
        content.image_url = image_url

    # Update workflow status
    workflow.status = data.get('status', 'completed')
    workflow.message = data.get('message', f'Image generated for content {content_id}')
    workflow.completed_at = datetime.utcnow()

    db.session.commit()

    return jsonify({
        'success': True,
        'id': workflow.id,
        'status': workflow.status,
        'content_id': content_id,
        'image_thumbnail': content.image_thumbnail,
        'image_url': content.image_url
    })


@bp.route('/workflow/<workflow_id>/audit-complete', methods=['POST'])
@require_api_key
def complete_audit_workflow(workflow_id):
    """
    Called by n8n when audit workflow completes.
    Creates a new final/patched version and optionally updates quality scores on existing versions.

    Expected payload:
    {
      "content_id": 123,
      "status": "completed",
      "final_version": {
        "ai_provider": "final",
        "ai_model": "audited",
        "quality_score": 95.0,
        "deck": "...",
        "teaser": "...",
        "body": "...",
        "summary": "...",
        "notes": "Audit notes..."
      },
      "version_scores": [
        {"version_id": 1, "quality_score": 82.5},
        {"version_id": 2, "quality_score": 78.0}
      ]
    }
    """
    workflow = WorkflowRun.query.get(workflow_id)
    if not workflow:
        return jsonify({'error': 'Workflow not found'}), 404

    data = request.get_json(force=True, silent=True) or {}

    # Get content_id from payload or workflow message
    content_id = data.get('content_id')
    if not content_id and workflow.message:
        if workflow.message.startswith('content_id:'):
            try:
                content_id = int(workflow.message.split(':')[1])
            except (ValueError, IndexError):
                pass

    if not content_id:
        workflow.status = 'failed'
        workflow.message = 'No content_id provided'
        workflow.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'error': 'No content_id provided'}), 400

    content = NewsContent.query.get(content_id)
    if not content:
        workflow.status = 'failed'
        workflow.message = f'Content {content_id} not found'
        workflow.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'error': 'Content not found'}), 404

    try:
        # Update quality scores on existing versions if provided
        version_scores = data.get('version_scores', [])
        for score_update in version_scores:
            version_id = score_update.get('version_id')
            new_score = score_update.get('quality_score')
            if version_id and new_score is not None:
                version = ContentVersion.query.get(version_id)
                if version and version.content_id == content.id:
                    version.quality_score = new_score

        # Create final/patched version if provided
        final_version_data = data.get('final_version')
        new_version = None

        if final_version_data:
            new_version = ContentVersion(
                content_id=content.id,
                ai_provider=final_version_data.get('ai_provider', 'final'),
                ai_model=final_version_data.get('ai_model', 'audited'),
                quality_score=final_version_data.get('quality_score'),
                deck=final_version_data.get('deck'),
                teaser=final_version_data.get('teaser'),
                content=final_version_data.get('body') or final_version_data.get('content'),
                summary=final_version_data.get('summary'),
                notes=final_version_data.get('notes'),
                is_final=True
            )
            db.session.add(new_version)
            db.session.flush()

            # Set as selected version
            content.selected_version_id = new_version.id

        # Update workflow status
        workflow.status = data.get('status', 'completed')
        workflow.message = data.get('message', f'Audit completed for content {content_id}')
        workflow.completed_at = datetime.utcnow()

        db.session.commit()

        response = {
            'success': True,
            'id': workflow.id,
            'status': workflow.status,
            'content_id': content_id
        }

        if new_version:
            response['final_version_id'] = new_version.id

        return jsonify(response)

    except Exception as e:
        db.session.rollback()
        workflow.status = 'failed'
        workflow.message = str(e)
        workflow.completed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'error': f'Failed to process audit result: {str(e)}'}), 500