from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from datetime import datetime
import uuid
from app import db
from app.models import NewsContent, Publication, WorkflowRun, ContentVersion
from app.main import bp
import requests


@bp.route('/')
@login_required
def index():
    return redirect(url_for('main.dashboard'))


@bp.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'staged')
    publication_id = request.args.get('publication_id', type=int)

    # Get available publications for the user
    if current_user.has_role('admin'):
        publications = Publication.query.filter_by(is_active=True).order_by(Publication.id).all()
    else:
        publications = [p for p in current_user.publications if p.is_active]

    # Default to first publication if none selected
    if not publication_id and publications:
        publication_id = publications[0].id

    current_publication = Publication.query.get(publication_id) if publication_id else None

    query = NewsContent.query

    # Filter by selected publication
    if publication_id:
        query = query.filter_by(publication_id=publication_id)
    elif not current_user.has_role('admin') and current_user.publications:
        pub_ids = current_user.get_publication_ids()
        query = query.filter(NewsContent.publication_id.in_(pub_ids))

    if status != 'all':
        query = query.filter_by(status=status)

    content = query.order_by(NewsContent.created_at.desc()).paginate(
        page=page, per_page=current_app.config['ITEMS_PER_PAGE'], error_out=False
    )

    return render_template('main/dashboard.html', title='Dashboard', content=content, status=status,
                           publications=publications, current_publication=current_publication)


@bp.route('/content/<int:id>')
@login_required
def view_content(id):
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        flash('Access denied', 'error')
        return redirect(url_for('main.dashboard'))

    return render_template('main/content_detail.html', title=content.title, content=content)


@bp.route('/content/<int:id>/push', methods=['POST'])
@login_required
def push_to_cms(id):
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    if content.pushed_to_cms:
        return jsonify({'error': 'Content already pushed to CMS'}), 400

    publication = content.publication
    if not publication.cms_url or not publication.cms_api_key:
        return jsonify({'error': 'CMS configuration not set for this publication'}), 400

    try:
        payload = {
            'command': {
                'input': [
                    {
                        'form': 'POST',
                        'props': {
                            'title': content.title,
                            'type': 'ARTICLE',
                            'body': content.content
                        }
                    }
                ]
            },
            'view': 'main'
        }

        # Add Bearer prefix if not already present
        api_key = publication.cms_api_key
        if not api_key.lower().startswith('bearer '):
            api_key = f'Bearer {api_key}'

        headers = {
            'Authorization': api_key,
            'Content-Type': 'application/json',
            'x-namespace': 'watt/default'
        }

        response = requests.post(publication.cms_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()

        cms_response = response.json()
        content.pushed_to_cms = True
        content.cms_id = cms_response.get('id')
        content.pushed_at = datetime.utcnow()
        content.pushed_by_id = current_user.id
        content.status = 'published'

        db.session.commit()

        return jsonify({'success': True, 'message': 'Content pushed to CMS successfully', 'cms_id': content.cms_id})

    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to push to CMS: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


@bp.route('/content/<int:id>/version/<int:version_id>/push', methods=['POST'])
@login_required
def push_version_to_cms(id, version_id):
    """Push a specific version of content to CMS."""
    content = NewsContent.query.get_or_404(id)
    version = ContentVersion.query.get_or_404(version_id)

    # Verify version belongs to this content
    if version.content_id != content.id:
        return jsonify({'error': 'Version does not belong to this content'}), 400

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    if version.pushed_to_cms:
        return jsonify({'error': 'This version has already been pushed to CMS'}), 400

    publication = content.publication
    if not publication.cms_url or not publication.cms_api_key:
        return jsonify({'error': 'CMS configuration not set for this publication'}), 400

    try:
        payload = {
            'command': {
                'input': [
                    {
                        'form': 'POST',
                        'props': {
                            'title': content.title,
                            'type': 'ARTICLE',
                            'body': version.content
                        }
                    }
                ]
            },
            'view': 'main'
        }

        api_key = publication.cms_api_key
        if not api_key.lower().startswith('bearer '):
            api_key = f'Bearer {api_key}'

        headers = {
            'Authorization': api_key,
            'Content-Type': 'application/json',
            'x-namespace': 'watt/default'
        }

        response = requests.post(publication.cms_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()

        cms_response = response.json()
        version.pushed_to_cms = True
        version.cms_id = cms_response.get('id')
        version.pushed_at = datetime.utcnow()
        version.pushed_by_id = current_user.id

        # Update parent content status
        content.status = 'published'

        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'{version.ai_provider.title()} version pushed to CMS successfully',
            'cms_id': version.cms_id,
            'version_id': version.id
        })

    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to push to CMS: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


@bp.route('/content/<int:id>/select-version/<int:version_id>', methods=['POST'])
@login_required
def select_version(id, version_id):
    """Select a specific version as the preferred version for this content."""
    content = NewsContent.query.get_or_404(id)
    version = ContentVersion.query.get_or_404(version_id)

    # Verify version belongs to this content
    if version.content_id != content.id:
        return jsonify({'error': 'Version does not belong to this content'}), 400

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    content.selected_version_id = version.id
    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'Selected {version.ai_provider.title()} version',
        'selected_version_id': version.id
    })


@bp.route('/content/<int:id>/status', methods=['POST'])
@login_required
def update_status(id):
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    new_status = request.json.get('status')
    if new_status not in ['staged', 'approved', 'rejected', 'published']:
        return jsonify({'error': 'Invalid status'}), 400

    content.status = new_status

    # Handle rejection reason
    if new_status == 'rejected':
        rejection_reason = request.json.get('rejection_reason', '').strip()
        content.rejection_reason = rejection_reason if rejection_reason else None
    elif new_status != 'rejected':
        # Clear rejection reason when status changes away from rejected
        content.rejection_reason = None

    db.session.commit()

    return jsonify({'success': True, 'status': content.status})


@bp.route('/publication/<int:id>/trigger-content-workflow', methods=['POST'])
@login_required
def trigger_content_workflow(id):
    publication = Publication.query.get_or_404(id)

    # Check access
    if not current_user.has_role('admin') and not current_user.has_publication_access(id):
        flash('Access denied', 'error')
        return redirect(url_for('main.dashboard'))

    workflow_url = current_app.config.get('N8N_CONTENT_WORKFLOW_URL')
    if not workflow_url:
        flash('Content workflow URL is not configured. Set N8N_CONTENT_WORKFLOW_URL environment variable.', 'error')
        return redirect(url_for('main.dashboard', publication_id=id))

    # Create workflow run record
    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=publication.id,
        triggered_by_id=current_user.id,
        workflow_type='content_generation',
        status='pending'
    )
    db.session.add(workflow_run)
    db.session.commit()

    try:
        # Fire-and-forget: use a very short timeout just to send the request
        requests.get(
            workflow_url,
            params={
                'publication_id': publication.id,
                'workflow_id': workflow_id
            },
            timeout=0.5
        )
    except requests.exceptions.Timeout:
        # Expected - we're not waiting for a response
        pass
    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        flash(f'Failed to trigger workflow: {str(e)}', 'error')
        return redirect(url_for('main.dashboard', publication_id=id))

    # Update status to running
    workflow_run.status = 'running'
    db.session.commit()

    return redirect(url_for('main.dashboard', publication_id=id, workflow_id=workflow_id))


@bp.route('/content/<int:id>/generate-image', methods=['POST'])
@login_required
def generate_image(id):
    """Trigger image generation workflow for a content item."""
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    workflow_url = current_app.config.get('N8N_IMAGE_WORKFLOW_URL')
    if not workflow_url:
        return jsonify({'error': 'Image workflow URL is not configured. Set N8N_IMAGE_WORKFLOW_URL environment variable.'}), 400

    # Create workflow run record
    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=content.publication_id,
        triggered_by_id=current_user.id,
        workflow_type='image_generation',
        status='pending',
        message=f'content_id:{content.id}'  # Store content_id for the callback
    )
    db.session.add(workflow_run)
    db.session.commit()

    try:
        # Get summary from selected version or fall back to legacy fields
        version = content.selected_version
        if version:
            summary = version.summary or version.teaser or version.deck or ''
        else:
            summary = content.summary or content.teaser or content.deck or ''

        # POST to n8n with article summary
        payload = {
            'workflow_id': workflow_id,
            'content_id': content.id,
            'title': content.title,
            'summary': summary
        }

        response = requests.post(
            workflow_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=5
        )
        response.raise_for_status()

    except requests.exceptions.Timeout:
        # Timeout is acceptable for async workflows
        pass
    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        return jsonify({'error': f'Failed to trigger image workflow: {str(e)}'}), 500

    # Update status to running
    workflow_run.status = 'running'
    db.session.commit()

    return jsonify({
        'success': True,
        'workflow_id': workflow_id,
        'message': 'Image generation started'
    })


@bp.route('/content/<int:id>/audit', methods=['POST'])
@login_required
def trigger_audit(id):
    """Trigger audit workflow to patch and score article versions."""
    content = NewsContent.query.get_or_404(id)

    if not current_user.has_role('admin') and not current_user.has_publication_access(content.publication_id):
        return jsonify({'error': 'Access denied'}), 403

    # Check if there are versions to audit
    if not content.versions:
        return jsonify({'error': 'No versions to audit. This article has no AI-generated versions.'}), 400

    workflow_url = current_app.config.get('N8N_AUDIT_WORKFLOW_URL')
    if not workflow_url:
        return jsonify({'error': 'Audit workflow URL is not configured. Set N8N_AUDIT_WORKFLOW_URL environment variable.'}), 400

    # Create workflow run record
    workflow_id = str(uuid.uuid4())
    workflow_run = WorkflowRun(
        id=workflow_id,
        publication_id=content.publication_id,
        triggered_by_id=current_user.id,
        workflow_type='audit',
        status='pending',
        message=f'content_id:{content.id}'
    )
    db.session.add(workflow_run)
    db.session.commit()

    try:
        # Build payload with all versions
        versions_payload = []
        for version in content.versions:
            versions_payload.append({
                'version_id': version.id,
                'ai_provider': version.ai_provider,
                'ai_model': version.ai_model,
                'quality_score': version.quality_score,
                'is_final': version.is_final,
                'deck': version.deck,
                'teaser': version.teaser,
                'body': version.content,
                'summary': version.summary,
                'notes': version.notes
            })

        payload = {
            'workflow_id': workflow_id,
            'article_id': content.id,
            'title': content.title,
            'source_url': content.source_url,
            'source_name': content.source_name,
            'keywords': content.keywords,
            'versions': versions_payload
        }

        response = requests.post(
            workflow_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=5
        )
        response.raise_for_status()

    except requests.exceptions.Timeout:
        # Timeout is acceptable for async workflows
        pass
    except requests.exceptions.RequestException as e:
        workflow_run.status = 'failed'
        workflow_run.message = str(e)
        db.session.commit()
        return jsonify({'error': f'Failed to trigger audit workflow: {str(e)}'}), 500

    # Update status to running
    workflow_run.status = 'running'
    db.session.commit()

    return jsonify({
        'success': True,
        'workflow_id': workflow_id,
        'message': 'Audit workflow started'
    })